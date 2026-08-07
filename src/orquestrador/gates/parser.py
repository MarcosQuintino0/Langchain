"""JSON dos scripts `.mjs` → `ResultadoGate` / `Violacao`.

Funções puras: recebem a saída de processo já capturada, não invocam nada. É o
que torna o parsing testável sem Node instalado.
"""

from __future__ import annotations

import json
from typing import Any

from orquestrador.contratos import ResultadoGate, Violacao
from orquestrador.excecoes import ErroDeInvocacao
from orquestrador.ferramentas.processo import SaidaProcesso
from orquestrador.textos import extrair_json

__all__ = [
    "ErroDeInvocacao",
    "extrair_json",
    "resultado_do_validador",
    "resumo_da_cobertura",
    "violacoes_do_eslint",
]


def resultado_do_validador(saida: SaidaProcesso, *, gate: str) -> ResultadoGate:
    """Converte a saída de `validar-suite-gerada.mjs --json` num veredito.

    O script escreve no **stdout quando aprova** e no **stderr quando reprova**;
    ler só um dos dois faz reprovação parecer saída vazia.
    """
    if saida.codigo == 2:
        raise ErroDeInvocacao(
            "validar-suite-gerada.mjs recusou a invocação (exit 2): "
            f"{saida.texto[:1000]}\ncomando: {saida.comando}"
        )

    # Tenta os dois fluxos: aprovação sai pelo stdout, reprovação pelo stderr, e
    # um aviso solto no fluxo "errado" não pode fazer o gate parecer quebrado.
    dados: dict[str, Any] | None = None
    ultimo_erro: Exception | None = None
    for fluxo in (saida.stdout, saida.stderr):
        if not fluxo.strip():
            continue
        try:
            dados = extrair_json(fluxo)
            break
        except (ValueError, json.JSONDecodeError) as erro:
            ultimo_erro = erro
    if dados is None:
        raise ErroDeInvocacao(
            f"saída não-JSON de validar-suite-gerada.mjs ({ultimo_erro or 'saída vazia'}). "
            f"código={saida.codigo} comando={saida.comando}\n{saida.texto[:1000]}"
        ) from ultimo_erro

    valido = bool(dados.get("valid"))
    return ResultadoGate(
        aprovado=valido,
        violacoes=[Violacao.model_validate(item) for item in dados.get("errors") or []],
        avisos=[Violacao.model_validate(item) for item in dados.get("warnings") or []],
        saida_bruta=saida.texto,
        gate=gate,
    )


def resumo_da_cobertura(saida: SaidaProcesso) -> dict[str, Any]:
    """Contadores de `qa-cobertura.mjs --json`.

    Devolve `{}` quando o script não conseguiu gerar o relatório — ele sai 0 mesmo
    nesse caso, então a ausência do JSON é o único sinal.
    """
    try:
        return extrair_json(saida.stdout)
    except (ValueError, json.JSONDecodeError):
        return {}


def violacoes_do_eslint(stdout: str) -> list[Violacao]:
    """Converte `eslint --format json` em violações com arquivo e linha."""
    try:
        arquivos = json.loads(stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []
    violacoes: list[Violacao] = []
    for arquivo in arquivos if isinstance(arquivos, list) else []:
        caminho = str(arquivo.get("filePath", "")).replace("\\", "/")
        for mensagem in arquivo.get("messages") or []:
            if mensagem.get("severity") != 2:
                continue
            regra = mensagem.get("ruleId") or "eslint"
            violacoes.append(
                Violacao(
                    codigo="QAORQ-021",
                    mensagem=f"{regra}: {mensagem.get('message', '').strip()}",
                    arquivo=caminho,
                    linha=mensagem.get("line"),
                )
            )
    return violacoes
