"""Exportação assíncrona e opt-in de traces/métricas pelo OTLP/HTTP JSON.

O exportador recebe somente a linha já sanitizada pelo `Registro` e projeta uma
allowlist de atributos. Não exporta logs, prompts, mensagens, caminhos ou saída
de ferramentas. A fila é não bloqueante e o worker é daemon: indisponibilidade do
Collector incrementa uma medida local, nunca altera o pipeline.
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import unquote

import httpx
from pydantic import JsonValue

from orquestrador.config import ConfigOtlp

__all__ = ["ExportadorOtlp"]

Transporte = Callable[[str, dict[str, object], dict[str, str], float], None]

_TIPOS_DE_SPAN = frozenset(
    {
        "operacao_concluida",
        "operacao_falhou",
        "requisicao_llm_concluida",
        "requisicao_llm_falhou",
        "processo",
        "gate",
    }
)
_INICIOS = frozenset({"operacao_iniciada", "requisicao_llm_iniciada"})
_ATRIBUTOS_SEGUROS = frozenset(
    {
        "aprovado",
        "codigo",
        "erro_tipo",
        "estagio",
        "estado",
        "finish_reason",
        "gate",
        "operacao",
        "simulado",
        "status",
        "timeout",
    }
)
_IDENTIFICADORES = frozenset({"endpoint", "fatia", "modelo", "request_id", "recurso", "run_id"})


@dataclass(frozen=True)
class _Envio:
    caminho: str
    dados: dict[str, object]


def _transporte_http(
    url: str, dados: dict[str, object], headers: dict[str, str], timeout: float
) -> None:
    with httpx.Client(timeout=timeout) as cliente:
        resposta = cliente.post(url, json=dados, headers=headers)
        resposta.raise_for_status()


def _headers(config: ConfigOtlp) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    bruto = os.environ.get(config.headers_env, "") if config.headers_env else ""
    for item in bruto.split(","):
        if "=" not in item:
            continue
        nome, valor = item.split("=", 1)
        if nome.strip():
            headers[unquote(nome.strip())] = unquote(valor.strip())
    return headers


def _nanos(ts: object) -> int:
    try:
        return int(datetime.fromisoformat(str(ts)).timestamp() * 1_000_000_000)
    except ValueError:
        return 0


def _atributo(chave: str, valor: object) -> dict[str, object]:
    if isinstance(valor, bool):
        tipado: dict[str, object] = {"boolValue": valor}
    elif isinstance(valor, int):
        tipado = {"intValue": str(valor)}
    elif isinstance(valor, float):
        tipado = {"doubleValue": valor}
    else:
        tipado = {"stringValue": str(valor)}
    return {"key": chave, "value": tipado}


def _recurso(service_name: str) -> dict[str, object]:
    return {"attributes": [_atributo("service.name", service_name)]}


class ExportadorOtlp:
    """Fila de payloads OTLP/HTTP; `exportar` nunca espera por rede."""

    def __init__(
        self,
        config: ConfigOtlp,
        *,
        transporte: Transporte | None = None,
        capacidade: int = 1000,
    ) -> None:
        self.config = config
        self._transporte = transporte or _transporte_http
        self._headers = _headers(config)
        self._base = str(config.endpoint).rstrip("/")
        self._fila: queue.Queue[_Envio | None] = queue.Queue(maxsize=capacidade)
        self._inicios: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self.falhas = 0
        self.descartados = 0
        if config.habilitado:
            self._thread = threading.Thread(
                target=self._trabalhar,
                name="observabilidade-otlp",
                daemon=True,
            )
            self._thread.start()

    def exportar(self, linha: dict[str, JsonValue]) -> None:
        if not self.config.habilitado:
            return
        tipo = str(linha.get("tipo") or "")
        dados = cast(dict[str, JsonValue], linha.get("dados") or {})
        chave = str(dados.get("request_local_id") or linha.get("span_id") or "")
        if tipo in _INICIOS:
            self._inicios[chave] = _nanos(linha.get("ts"))
            return
        if tipo not in _TIPOS_DE_SPAN:
            return
        envios = [_Envio("/v1/traces", self._trace(linha, dados))]
        metricas = self._metricas(linha, dados)
        if metricas is not None:
            envios.append(_Envio("/v1/metrics", metricas))
        for envio in envios:
            try:
                self._fila.put_nowait(envio)
            except queue.Full:
                self.descartados += 1

    def _trace(self, linha: dict[str, JsonValue], dados: dict[str, JsonValue]) -> dict[str, object]:
        tipo = str(linha.get("tipo") or "evento")
        chave = str(dados.get("request_local_id") or linha.get("span_id") or "")
        fim = _nanos(linha.get("ts"))
        duracao = dados.get("duracao_s")
        inicio = self._inicios.pop(chave, 0)
        if not inicio and isinstance(duracao, int | float):
            inicio = max(0, fim - int(float(duracao) * 1_000_000_000))
        if not inicio:
            inicio = fim
        origem_span = chave or str(linha.get("event_id") or tipo)
        span_id = hashlib.sha256(origem_span.encode("utf-8")).hexdigest()[:16]
        atributos: list[dict[str, object]] = [_atributo("evento.tipo", tipo)]
        permitidos = set(_ATRIBUTOS_SEGUROS)
        if self.config.incluir_identificadores:
            permitidos.update(_IDENTIFICADORES)
        combinado: dict[str, object] = {**dados}
        if self.config.incluir_identificadores:
            combinado["run_id"] = linha.get("run_id")
        for nome in sorted(permitidos):
            valor = combinado.get(nome)
            if isinstance(valor, str | int | float | bool):
                atributos.append(_atributo(f"orquestrador.{nome}", valor))
        status = {"code": 2 if tipo.endswith("falhou") else 1}
        span: dict[str, object] = {
            "traceId": str(linha.get("trace_id") or "").replace("-", "")[:32],
            "spanId": span_id,
            "name": tipo,
            "kind": 1,
            "startTimeUnixNano": str(inicio),
            "endTimeUnixNano": str(fim),
            "attributes": atributos,
            "status": status,
        }
        pai = linha.get("parent_span_id")
        if isinstance(pai, str) and pai:
            span["parentSpanId"] = pai[:16]
        return {
            "resourceSpans": [
                {
                    "resource": _recurso(self.config.service_name),
                    "scopeSpans": [
                        {"scope": {"name": "orquestrador.observabilidade"}, "spans": [span]}
                    ],
                }
            ]
        }

    def _metricas(
        self, linha: dict[str, JsonValue], dados: dict[str, JsonValue]
    ) -> dict[str, object] | None:
        tipo = str(linha.get("tipo") or "")
        valores: list[tuple[str, int | float]] = []
        if tipo == "requisicao_llm_concluida":
            uso = dados.get("uso")
            if isinstance(uso, dict):
                for campo in ("entrada", "saida", "cache_lido", "cache_escrito", "raciocinio"):
                    valor = cast(dict[str, JsonValue], uso).get(campo)
                    if isinstance(valor, int | float) and not isinstance(valor, bool):
                        valores.append((f"orquestrador.llm.tokens.{campo}", valor))
        duracao = dados.get("duracao_s")
        if isinstance(duracao, int | float) and not isinstance(duracao, bool):
            valores.append((f"orquestrador.{tipo}.duracao_s", duracao))
        if not valores:
            return None
        instante = str(_nanos(linha.get("ts")))
        metricas: list[dict[str, object]] = []
        for nome, valor in valores:
            ponto: dict[str, object] = {"timeUnixNano": instante}
            ponto["asInt" if isinstance(valor, int) else "asDouble"] = (
                str(valor) if isinstance(valor, int) else valor
            )
            metricas.append(
                {
                    "name": nome,
                    "sum": {
                        "aggregationTemporality": 2,
                        "isMonotonic": True,
                        "dataPoints": [ponto],
                    },
                }
            )
        return {
            "resourceMetrics": [
                {
                    "resource": _recurso(self.config.service_name),
                    "scopeMetrics": [
                        {
                            "scope": {"name": "orquestrador.observabilidade"},
                            "metrics": metricas,
                        }
                    ],
                }
            ]
        }

    def _trabalhar(self) -> None:
        while True:
            envio = self._fila.get()
            try:
                if envio is None:
                    return
                self._transporte(
                    self._base + envio.caminho,
                    envio.dados,
                    self._headers,
                    float(self.config.timeout_s),
                )
            except Exception:
                self.falhas += 1
            finally:
                self._fila.task_done()

    def fechar(self) -> None:
        if self._thread is None:
            return
        try:
            self._fila.put_nowait(None)
        except queue.Full:
            self.descartados += 1
            return
        self._thread.join(timeout=min(float(self.config.timeout_s), 0.5) + 0.1)
