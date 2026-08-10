"""Leitor somente leitura dos JSONL históricos (v0, v1 e v2).

Versões antigas são normalizadas em memória: `run_id` vem do diretório, `seq` da
ordem física e os campos achatados viram `dados`. Nenhum arquivo é migrado ou
reescrito. O modo tolerante conserva o que ainda é legível; o estrito transforma
qualquer inconsistência estrutural em erro acionável para CI/CLI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import JsonValue

from orquestrador.observabilidade.eventos import TipoDeEvento

__all__ = [
    "EventoLido",
    "LeituraDeExecucao",
    "LogInvalido",
    "ProblemaDoLog",
    "ler_execucao",
]

_CAMPOS_V0_V1 = frozenset({"ts", "t_s", "schema_version", "tipo"})
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_16 = re.compile(r"^[0-9a-f]{16}$")
_TERMINAIS = frozenset(
    {TipoDeEvento.EXECUCAO_CONCLUIDA.value, TipoDeEvento.EXECUCAO_ABORTADA.value}
)

# As versões que este leitor sabe interpretar. Versão fora desta lista NÃO cai no
# ramo legado: o legado é o formato mais permissivo que existe, e deixar um
# formato futuro passar por ele produz "log validado sem problema" com o payload
# aninhado no lugar errado — o oposto do que `schema_version` existe para dar.
_VERSOES_CONHECIDAS = frozenset({0, 1, 2})

# O caractere que `decode(errors="replace")` deixa no lugar de byte inválido.
_SUBSTITUICAO = "�"


class LogInvalido(ValueError):
    pass


@dataclass(frozen=True)
class ProblemaDoLog:
    codigo: str
    mensagem: str
    linha: int | None = None
    severidade: Literal["erro", "aviso"] = "erro"


@dataclass(frozen=True)
class EventoLido:
    schema_version: int
    tipo: str
    run_id: str
    seq: int
    ts: str
    t_s: float | None
    event_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    dados: dict[str, JsonValue]
    linha: int


@dataclass
class LeituraDeExecucao:
    caminho: Path
    eventos: list[EventoLido] = field(default_factory=list[EventoLido])
    problemas: list[ProblemaDoLog] = field(default_factory=list[ProblemaDoLog])
    ativa: bool = False

    @property
    def terminal(self) -> str | None:
        encontrados = [evento.tipo for evento in self.eventos if evento.tipo in _TERMINAIS]
        return encontrados[-1] if encontrados else None


def _problema(
    leitura: LeituraDeExecucao,
    codigo: str,
    mensagem: str,
    linha: int | None = None,
    *,
    severidade: Literal["erro", "aviso"] = "erro",
) -> None:
    leitura.problemas.append(ProblemaDoLog(codigo, mensagem, linha, severidade))


def _inteiro(valor: Any, padrao: int) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else padrao


def _flutuante(valor: Any) -> float | None:
    return float(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else None


def _evento_antigo(dados: dict[str, Any], *, linha: int, run_id: str) -> EventoLido | None:
    tipo = dados.get("tipo")
    if not isinstance(tipo, str):
        return None
    versao = _inteiro(dados.get("schema_version"), 0)
    payload = {chave: valor for chave, valor in dados.items() if chave not in _CAMPOS_V0_V1}
    return EventoLido(
        schema_version=versao,
        tipo=tipo,
        run_id=run_id,
        seq=linha,
        ts=str(dados.get("ts") or ""),
        t_s=_flutuante(dados.get("t_s")),
        event_id=f"legacy-{linha}",
        trace_id=f"legacy-{run_id}",
        span_id="",
        parent_span_id=None,
        dados=cast(dict[str, JsonValue], payload),
        linha=linha,
    )


def _evento_v2(dados: dict[str, Any], *, linha: int) -> EventoLido | None:
    obrigatorios = ("tipo", "run_id", "event_id", "trace_id", "span_id", "seq", "dados")
    if any(chave not in dados for chave in obrigatorios):
        return None
    if not isinstance(dados["dados"], dict):
        return None
    return EventoLido(
        schema_version=2,
        tipo=str(dados["tipo"]),
        run_id=str(dados["run_id"]),
        seq=_inteiro(dados["seq"], -1),
        ts=str(dados.get("ts") or ""),
        t_s=_flutuante(dados.get("t_s")),
        event_id=str(dados["event_id"]),
        trace_id=str(dados["trace_id"]),
        span_id=str(dados["span_id"]),
        parent_span_id=(
            str(dados["parent_span_id"]) if dados.get("parent_span_id") is not None else None
        ),
        dados=cast(dict[str, JsonValue], dados["dados"]),
        linha=linha,
    )


def ler_execucao(caminho: Path, *, estrito: bool = False) -> LeituraDeExecucao:
    arquivo = caminho / "execucao.jsonl" if caminho.is_dir() else caminho
    run_derivado = arquivo.parent.name
    leitura = LeituraDeExecucao(
        caminho=arquivo,
        ativa=(arquivo.parent / "execucao.lock").is_file(),
    )
    try:
        bruto_do_arquivo = arquivo.read_bytes()
    except OSError as erro:
        _problema(leitura, "arquivo_ilegivel", f"{type(erro).__name__}: {erro}")
        return _finalizar(leitura, estrito)

    # `decode(errors="replace")`, e não `read_text`: `UnicodeDecodeError` herda de
    # `ValueError`, escapava do `except OSError` acima e derrubava o leitor
    # inteiro — justamente no log cortado no meio de um caractere multibyte, que
    # é o caso mais comum de execução interrompida e o motivo de existir um modo
    # tolerante. Agora o byte inválido vira problema e o resto continua legível.
    texto_do_arquivo = bruto_do_arquivo.decode("utf-8", errors="replace")
    if _SUBSTITUICAO in texto_do_arquivo:
        _problema(
            leitura,
            "bytes_invalidos",
            "byte inválido em UTF-8; as linhas afetadas foram lidas com substituição",
        )
    linhas = texto_do_arquivo.splitlines()

    for numero, texto in enumerate(linhas, 1):
        if not texto.strip():
            # Aviso, não erro: linha em branco é irregularidade cosmética. Como
            # erro, ela reprovava `execucoes validar` e bloqueava a retenção
            # daquele diretório para sempre.
            _problema(leitura, "linha_vazia", "linha vazia no JSONL", numero, severidade="aviso")
            continue
        try:
            bruto = json.loads(texto)
        except json.JSONDecodeError as erro:
            _problema(leitura, "json_invalido", f"coluna {erro.colno}: {erro.msg}", numero)
            continue
        if not isinstance(bruto, dict):
            _problema(leitura, "evento_nao_objeto", "a linha JSON não é objeto", numero)
            continue
        dados = cast(dict[str, Any], bruto)
        versao = _inteiro(dados.get("schema_version"), 0)
        if versao not in _VERSOES_CONHECIDAS:
            _problema(
                leitura,
                "versao_desconhecida",
                f"schema_version {versao} é mais novo que este leitor; a linha não foi "
                "interpretada. Atualize o orquestrador para ler este log.",
                numero,
            )
            continue
        evento = (
            _evento_v2(dados, linha=numero)
            if versao == 2
            else _evento_antigo(dados, linha=numero, run_id=run_derivado)
        )
        if evento is None:
            _problema(leitura, "contrato_invalido", f"envelope v{versao} incompleto", numero)
            continue
        leitura.eventos.append(evento)

    _validar(leitura)
    return _finalizar(leitura, estrito)


def _validar(leitura: LeituraDeExecucao) -> None:
    conhecidos = {tipo.value for tipo in TipoDeEvento}
    ids: set[str] = set()
    spans: set[str] = set()
    run_ids = {evento.run_id for evento in leitura.eventos}
    if len(run_ids) > 1:
        _problema(leitura, "run_id_misturado", f"run_ids encontrados: {sorted(run_ids)}")

    esperado = 1
    for evento in leitura.eventos:
        if evento.tipo not in conhecidos:
            _problema(leitura, "tipo_desconhecido", evento.tipo, evento.linha)
        if evento.schema_version == 2:
            if evento.seq != esperado:
                _problema(
                    leitura,
                    "sequencia_invalida",
                    f"esperado {esperado}, recebido {evento.seq}",
                    evento.linha,
                )
            esperado = evento.seq + 1
            try:
                UUID(evento.event_id)
            except ValueError:
                _problema(leitura, "event_id_invalido", evento.event_id, evento.linha)
            if evento.event_id in ids:
                _problema(leitura, "event_id_duplicado", evento.event_id, evento.linha)
            ids.add(evento.event_id)
            if not _HEX_32.fullmatch(evento.trace_id):
                _problema(leitura, "trace_id_invalido", evento.trace_id, evento.linha)
            if not _HEX_16.fullmatch(evento.span_id):
                _problema(leitura, "span_id_invalido", evento.span_id, evento.linha)
            if evento.parent_span_id and evento.parent_span_id not in spans:
                _problema(
                    leitura,
                    "span_pai_ausente",
                    evento.parent_span_id,
                    evento.linha,
                )
            spans.add(evento.span_id)

    terminais = [evento for evento in leitura.eventos if evento.tipo in _TERMINAIS]
    if not terminais:
        if leitura.ativa:
            # O lock silenciava a ausência de desfecho por completo, e o log de um
            # processo morto por queda passava a ser "válido" para sempre. Aviso, e
            # não erro: execução em andamento é legítima — o que não pode é a
            # ausência de evidência virar aprovação silenciosa.
            _problema(
                leitura,
                "sem_terminal_com_lock",
                "execução sem desfecho e com `execucao.lock` presente: ou está em "
                "andamento, ou o lock ficou órfão de um processo que morreu",
                severidade="aviso",
            )
        else:
            _problema(leitura, "terminal_ausente", "execução sem evento terminal")
    elif len(terminais) > 1:
        _problema(leitura, "terminal_duplicado", f"{len(terminais)} eventos terminais")
    else:
        # `pulso` fica de fora: o heartbeat roda numa thread própria até `fechar()`,
        # e um batimento que caísse depois do terminal reprovava execução sadia —
        # e a retenção então recusava aquele diretório para sempre.
        posteriores = [
            evento
            for evento in leitura.eventos
            if evento.linha > terminais[-1].linha and evento.tipo != TipoDeEvento.PULSO.value
        ]
        if posteriores:
            _problema(
                leitura,
                "evento_apos_terminal",
                f"{len(posteriores)} evento(s) depois do desfecho, começando em "
                f"{posteriores[0].tipo}",
                posteriores[0].linha,
            )


def _finalizar(leitura: LeituraDeExecucao, estrito: bool) -> LeituraDeExecucao:
    erros = [problema for problema in leitura.problemas if problema.severidade == "erro"]
    if estrito and erros:
        resumo = ", ".join(
            f"{problema.codigo}" + (f"@{problema.linha}" if problema.linha else "")
            for problema in erros
        )
        raise LogInvalido(f"{leitura.caminho}: {resumo}")
    return leitura
