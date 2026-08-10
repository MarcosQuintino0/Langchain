"""Projeções read-only para relatórios e comparações de execuções locais."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, cast

from pydantic import JsonValue

from orquestrador.observabilidade.leitura import LeituraDeExecucao, ler_execucao

__all__ = [
    "ResumoDeExecucao",
    "comparar_execucoes",
    "historico",
    "listar_execucoes",
    "resumir_execucao",
]


@dataclass(frozen=True)
class ResumoDeExecucao:
    run_id: str
    caminho: str
    schema_versions: list[int]
    dry_run: bool | None
    ativa: bool
    terminal: str | None
    sucesso: bool | None
    duracao_s: float
    eventos: int
    chamadas_llm: int
    tools: int
    tokens: int
    problemas: int

    def para_json(self) -> dict[str, Any]:
        return asdict(self)


def _dados_de_inicio(leitura: LeituraDeExecucao) -> dict[str, JsonValue]:
    for evento in leitura.eventos:
        if evento.tipo == "execucao_iniciada":
            return evento.dados
    return {}


def _soma_tokens(dados: dict[str, JsonValue]) -> int:
    uso = dados.get("uso")
    if not isinstance(uso, dict):
        return 0
    tipado = cast(dict[str, JsonValue], uso)
    return sum(
        valor
        for chave in ("entrada", "saida")
        if isinstance((valor := tipado.get(chave)), int) and not isinstance(valor, bool)
    )


def resumir_execucao(caminho: Path) -> ResumoDeExecucao:
    leitura = ler_execucao(caminho)
    inicio = _dados_de_inicio(leitura)
    terminais = [
        evento
        for evento in leitura.eventos
        if evento.tipo in {"execucao_concluida", "execucao_abortada"}
    ]
    terminal = terminais[-1] if terminais else None
    sucesso_bruto = terminal.dados.get("sucesso") if terminal else None
    sucesso = sucesso_bruto if isinstance(sucesso_bruto, bool) else None
    chamadas = [
        evento
        for evento in leitura.eventos
        if evento.tipo in {"requisicao_llm_concluida", "chamada_llm"}
    ]
    dry_run_bruto = inicio.get("dry_run")
    return ResumoDeExecucao(
        run_id=leitura.eventos[0].run_id if leitura.eventos else caminho.name,
        caminho=str(leitura.caminho.parent),
        schema_versions=sorted({evento.schema_version for evento in leitura.eventos}),
        dry_run=dry_run_bruto if isinstance(dry_run_bruto, bool) else None,
        ativa=leitura.ativa,
        terminal=leitura.terminal,
        sucesso=sucesso,
        duracao_s=max((evento.t_s or 0.0 for evento in leitura.eventos), default=0.0),
        eventos=len(leitura.eventos),
        chamadas_llm=len(chamadas),
        tools=sum(evento.tipo == "tool" for evento in leitura.eventos),
        tokens=sum(_soma_tokens(evento.dados) for evento in chamadas),
        problemas=len(leitura.problemas),
    )


def listar_execucoes(base: Path) -> list[ResumoDeExecucao]:
    if not base.is_dir():
        return []
    diretorios = [
        caminho
        for caminho in base.iterdir()
        if caminho.is_dir() and (caminho / "execucao.jsonl").is_file()
    ]
    return [resumir_execucao(caminho) for caminho in sorted(diretorios, reverse=True)]


def historico(resumos: list[ResumoDeExecucao]) -> dict[str, dict[str, int | float]]:
    saida: dict[str, dict[str, int | float]] = {}
    for rotulo, selecao in (
        ("real", [item for item in resumos if item.dry_run is False]),
        ("dry_run", [item for item in resumos if item.dry_run is True]),
        ("desconhecido", [item for item in resumos if item.dry_run is None]),
    ):
        saida[rotulo] = {
            "execucoes": len(selecao),
            "sucessos": sum(item.sucesso is True for item in selecao),
            "duracao_mediana_s": round(median([item.duracao_s for item in selecao]), 3)
            if selecao
            else 0.0,
            "tokens": sum(item.tokens for item in selecao),
        }
    return saida


def comparar_execucoes(a: ResumoDeExecucao, b: ResumoDeExecucao) -> dict[str, Any]:
    return {
        "a": a.para_json(),
        "b": b.para_json(),
        "delta": {
            "duracao_s": round(b.duracao_s - a.duracao_s, 3),
            "eventos": b.eventos - a.eventos,
            "chamadas_llm": b.chamadas_llm - a.chamadas_llm,
            "tools": b.tools - a.tools,
            "tokens": b.tokens - a.tokens,
            "problemas": b.problemas - a.problemas,
        },
    }
