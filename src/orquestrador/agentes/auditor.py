"""Auditor semântico — STUB da Fase 1.

Princípio 5: o auditor fica **fora do loop quente**. Ele é caro, tem taxa de falso
positivo alta e roda sob demanda, em sessão limpa, com humano triando o veredito.
Nunca é chamado pelos loops de reparo dos gates.

Interface (definitiva):

    auditar(backend, manifesto, specs) -> ResultadoAuditoria
        {naoAplica_refutados: [...], oraculos_fracos: [...], veredito: "íntegro" | "revisar"}

A implementação é Fase 2 e mora em `references/auditar-cobertura.md`.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.contratos import ResultadoAuditoria

ESTAGIO = "auditor"


class AuditorNaoImplementado(NotImplementedError):
    """Chamada real ao auditor antes da Fase 2."""


def auditar(
    caminho_backend: Path,
    caminho_manifesto: Path,
    amostra_specs: list[Path],
    *,
    permitir_stub: bool = False,
) -> ResultadoAuditoria:
    """Confronta o gabarito com o backend e com uma amostra de specs.

    Quando implementado (Fase 2), deve:

    * refutar cada `naoAplica` cuja justificativa não se sustenta diante do código
      do backend — `naoAplica_refutados`;
    * apontar oráculos fracos na amostra de specs (status flexível, mera existência
      do corpo, ausência de erro 5xx) — `oraculos_fracos`;
    * devolver `veredito` para triagem humana, nunca para o loop automático.

    Na Fase 1, `permitir_stub=True` devolve um veredito vazio marcado como
    "revisar" — nunca "íntegro", para que o stub não seja confundido com auditoria
    feita.
    """
    if not permitir_stub:
        raise AuditorNaoImplementado(
            "auditor semântico é stub da Fase 1. Use permitir_stub=True para obter o "
            "resultado vazio, ou implemente conforme references/auditar-cobertura.md."
        )
    return ResultadoAuditoria(
        nao_aplica_refutados=[],
        oraculos_fracos=[],
        veredito="revisar",
    )


def descrever(caminho_backend: Path, caminho_manifesto: Path, amostra_specs: list[Path]) -> str:
    """Texto de console explicando por que o veredito veio vazio."""
    return (
        "auditor semântico: STUB da Fase 1 (nada foi auditado).\n"
        f"  backend:   {caminho_backend}\n"
        f"  manifesto: {caminho_manifesto}\n"
        f"  specs na amostra: {len(amostra_specs)}\n"
        '  veredito: "revisar" — por construção, para não parecer auditoria cumprida.'
    )
