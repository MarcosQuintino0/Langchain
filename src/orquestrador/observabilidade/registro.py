"""Escritor resiliente do JSONL v2 e apresentação no console.

O JSONL local continua sendo a fonte primária. Antes de persistir, todo payload
passa pela política canônica de redação; conteúdo bruto de processo, prompt ou
mensagem vira somente tamanho e hash. Falha desta camada é visível no console,
mas nunca decide o fluxo do pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO, cast

from pydantic import BaseModel, JsonValue
from rich.console import Console
from rich.markup import escape

from orquestrador.ferramentas.privacidade import redigir_texto
from orquestrador.observabilidade.eventos import ESQUEMA_DOS_EVENTOS, Evento, TipoDeEvento
from orquestrador.observabilidade.rastreamento import contexto_atual

__all__ = [
    "RegistradorDeEventos",
    "Registro",
    "configurar_console",
    "dados_para_log",
    "diretorio_de_execucao",
]

_CHAVES_DE_CONTEUDO_BRUTO = frozenset(
    {
        "conteudo",
        "mensagem_bruta",
        "mensagens",
        "prompt",
        "saida_bruta",
        "stderr",
        "stdout",
    }
)
_CHAVES_DE_CREDENCIAL = frozenset(
    {
        "api_key",
        "authorization",
        "autorizacao",
        "credential",
        "credentials",
        "password",
        "secret",
        "segredo",
        "senha",
        "token",
    }
)
_LIMITE_DE_TEXTO = 4096


def dados_para_log(valor: Any) -> Any:
    """Converte objetos conhecidos para a álgebra de valores JSON."""
    if isinstance(valor, BaseModel):
        return dados_para_log(valor.model_dump(mode="json"))
    if isinstance(valor, Path):
        return str(valor)
    if isinstance(valor, (list, tuple, set, frozenset)):
        sequencia = cast(list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any], valor)
        return [dados_para_log(item) for item in sequencia]
    if isinstance(valor, dict):
        itens = cast(dict[Any, Any], valor).items()
        return {str(chave): dados_para_log(item) for chave, item in itens}
    if valor is None or isinstance(valor, str | int | float | bool):
        return valor
    return str(valor)


@dataclass
class _ContagemDeSanitizacao:
    redacoes: int = 0
    conteudos_omitidos: int = 0
    truncamentos: int = 0

    def para_json(self) -> dict[str, JsonValue]:
        return {
            "redacoes": self.redacoes,
            "conteudos_omitidos": self.conteudos_omitidos,
            "truncamentos": self.truncamentos,
        }


def _nome_sensivel(chave: str) -> bool:
    normalizada = chave.lower().replace("-", "_").replace(".", "_")
    if normalizada.endswith("_env") or normalizada in {"tokens", "max_tokens"}:
        return False
    partes = {parte for parte in re.split(r"_+", normalizada) if parte}
    return normalizada in _CHAVES_DE_CREDENCIAL or bool(partes & _CHAVES_DE_CREDENCIAL)


def _resumo_de_conteudo(texto: str) -> dict[str, JsonValue]:
    bruto = texto.encode("utf-8")
    return {
        "bytes": len(bruto),
        "sha256": hashlib.sha256(bruto).hexdigest(),
        "omitido": True,
    }


def _sanitizar(valor: Any, contagem: _ContagemDeSanitizacao, *, chave: str = "") -> JsonValue:
    convertido = dados_para_log(valor)
    if _nome_sensivel(chave):
        contagem.redacoes += 1
        return "[redigido: campo sensível]"
    if chave.lower() in _CHAVES_DE_CONTEUDO_BRUTO and isinstance(convertido, str):
        contagem.conteudos_omitidos += 1
        return _resumo_de_conteudo(convertido)
    if isinstance(convertido, str):
        redacao = redigir_texto(convertido)
        contagem.redacoes += redacao.trechos
        texto = redacao.texto
        if len(texto) > _LIMITE_DE_TEXTO:
            contagem.truncamentos += 1
            texto = texto[:_LIMITE_DE_TEXTO] + "…[truncado]"
        return texto
    if isinstance(convertido, list):
        itens = cast(list[Any], convertido)
        return [_sanitizar(item, contagem) for item in itens]
    if isinstance(convertido, dict):
        itens_do_mapa = cast(dict[Any, Any], convertido).items()
        return {
            str(nome): _sanitizar(item, contagem, chave=str(nome))
            for nome, item in itens_do_mapa
        }
    return cast(JsonValue, convertido)


class RegistradorDeEventos(Protocol):
    def evento(self, tipo: TipoDeEvento | str, **campos: Any) -> None: ...

    def aviso(self, texto: str) -> None: ...


class _Exportador(Protocol):
    falhas: int
    descartados: int

    def exportar(self, linha: dict[str, JsonValue]) -> None: ...

    def fechar(self) -> None: ...


def configurar_console() -> None:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        except (AttributeError, OSError, ValueError):
            pass


class Registro:
    """Serializa eventos v2 com ordem total por execução e escrita protegida."""

    def __init__(
        self,
        caminho: Path,
        console: Console | None = None,
        *,
        run_id: str | None = None,
        intervalo_pulso_s: float | None = 30.0,
        exportador: _Exportador | None = None,
    ) -> None:
        self.caminho = caminho
        self.console = console or Console()
        self.inicio = time.perf_counter()
        self.run_id = run_id or caminho.parent.name
        self.trace_id = uuid.uuid4().hex
        self._span_raiz = uuid.uuid4().hex[:16]
        self._sequencia = 0
        self._mutex = threading.RLock()
        self._falha_avisada = False
        self._exportador = exportador
        self._fluxo: TextIO | None = None
        self._lock: TextIO | None = None
        self._caminho_lock = caminho.parent / "execucao.lock"
        self._parar_pulso = threading.Event()
        self._thread_pulso: threading.Thread | None = None
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            self._lock = self._caminho_lock.open("x", encoding="utf-8")
            self._lock.write(f"{os.getpid()}\n")
            self._lock.flush()
            self._fluxo = self.caminho.open("a", encoding="utf-8")
        except OSError as erro:
            self.aviso(f"observabilidade local indisponível: {type(erro).__name__}: {erro}")
        if intervalo_pulso_s is not None and intervalo_pulso_s > 0:
            self._thread_pulso = threading.Thread(
                target=self._pulsar,
                args=(intervalo_pulso_s,),
                name="observabilidade-heartbeat",
                daemon=True,
            )
            self._thread_pulso.start()

    def evento(self, tipo: TipoDeEvento | str, **campos: Any) -> None:
        """Compatibilidade para emissores existentes; evento novo usa `emitir`."""
        try:
            tipo_validado = tipo if isinstance(tipo, TipoDeEvento) else TipoDeEvento(tipo)
            convertidos = cast(dict[str, JsonValue], dados_para_log(campos))
            self.emitir(Evento.de_campos(tipo_validado, convertidos))
        except Exception as erro:
            self._avisar_falha(erro)

    def emitir(self, evento: Evento) -> None:
        """Acrescenta envelope, sanitiza e persiste uma linha atômica no processo."""
        contagem = _ContagemDeSanitizacao()
        dados = cast(dict[str, JsonValue], _sanitizar(evento.dados, contagem))
        contexto = contexto_atual()
        trace_id = contexto.trace_id if contexto else self.trace_id
        span_id = contexto.span_id if contexto else self._span_raiz
        parent_span_id = contexto.parent_span_id if contexto else None
        with self._mutex:
            self._sequencia += 1
            linha: dict[str, JsonValue] = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "t_s": round(time.perf_counter() - self.inicio, 3),
                "schema_version": ESQUEMA_DOS_EVENTOS,
                "tipo": evento.tipo.value,
                "run_id": self.run_id,
                "event_id": str(uuid.uuid4()),
                "seq": self._sequencia,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "dados": dados,
                "sanitizacao": contagem.para_json(),
            }
            if self._fluxo is not None:
                try:
                    self._fluxo.write(json.dumps(linha, ensure_ascii=False) + "\n")
                    self._fluxo.flush()
                except (OSError, TypeError, ValueError) as erro:
                    self._avisar_falha(erro)
        if self._exportador is not None:
            try:
                self._exportador.exportar(linha)
            except Exception as erro:
                self._avisar_falha(erro)

    def _pulsar(self, intervalo_s: float) -> None:
        while not self._parar_pulso.wait(intervalo_s):
            self.evento(TipoDeEvento.PULSO, pid=os.getpid())

    def _avisar_falha(self, erro: BaseException) -> None:
        if self._falha_avisada:
            return
        self._falha_avisada = True
        self.aviso(f"falha na observabilidade (execução segue): {type(erro).__name__}")

    def titulo(self, texto: str) -> None:
        self.console.rule(f"[bold]{escape(texto)}")

    def info(self, texto: str) -> None:
        self.console.print(escape(texto))

    def ok(self, texto: str) -> None:
        self.console.print(f"[green]✓[/green] {escape(texto)}")

    def falha(self, texto: str) -> None:
        self.console.print(f"[red]✗[/red] {escape(texto)}")

    def aviso(self, texto: str) -> None:
        self.console.print(f"[yellow]![/yellow] {escape(texto)}")

    def fechar(self) -> None:
        self._parar_pulso.set()
        if self._thread_pulso is not None:
            self._thread_pulso.join(timeout=1.0)
        if self._exportador is not None:
            self._exportador.fechar()
            if self._exportador.falhas or self._exportador.descartados:
                self.aviso(
                    "exportação OTLP incompleta: "
                    f"{self._exportador.falhas} falha(s), "
                    f"{self._exportador.descartados} evento(s) descartado(s)"
                )
        with self._mutex:
            if self._fluxo is not None and not self._fluxo.closed:
                self._fluxo.close()
            if self._lock is not None and not self._lock.closed:
                self._lock.close()
            try:
                self._caminho_lock.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> Registro:
        return self

    def __exit__(self, tipo: object, erro: object, _traceback: object) -> None:
        if tipo is not None:
            self.evento(
                TipoDeEvento.EXECUCAO_ABORTADA,
                motivo="exceção não tratada",
                erro_tipo=getattr(tipo, "__name__", str(tipo)),
            )
        self.fechar()


def diretorio_de_execucao(base: Path) -> Path:
    """Cria diretório exclusivo, com UUID impedindo colisão entre processos."""
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    for _ in range(10):
        destino = base / f"{marca}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            destino.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return destino
    raise OSError(f"não foi possível reservar diretório de execução sob {base}")
