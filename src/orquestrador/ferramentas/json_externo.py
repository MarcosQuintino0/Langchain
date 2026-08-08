"""Leitura tolerante de JSON vindo de fora — de um LLM ou de um script `.mjs`.

Implementação **única**: antes havia duas, uma em `modelos.py` e outra no parser
dos gates, com assinaturas diferentes fazendo a mesma coisa.

O nome diz o que o módulo é dono: recuperar JSON de **stdout de ferramenta
externa** e de resposta de modelo. Ele não trata texto genérico — não normaliza,
não formata, não corta. Chamava-se `textos.py`, e esse nome convidava a virar
depósito de qualquer manipulação de string do projeto.

Retorna sempre `dict[str, Any]`, nunca `Any`. Todos os consumidores esperam um
objeto — o `--json` do validador emite um objeto, e todo contrato de saída dos
agentes é um objeto. Recusar cedo uma lista ou um escalar produz um erro que diz
o que houve ("esperava objeto JSON"), em vez de empurrar o problema para o
Pydantic reclamar de um tipo inesperado três frames adiante.
"""

from __future__ import annotations

import json
from typing import Any, cast


def extrair_json(texto: str) -> dict[str, Any]:
    """Primeiro objeto JSON do texto, tolerando cerca ``` e prosa em volta."""
    limpo = _sem_cerca((texto or "").strip())
    if not limpo:
        raise ValueError("saída vazia")

    try:
        return _exigir_objeto(json.loads(limpo))
    except json.JSONDecodeError:
        pass

    inicio, fim = limpo.find("{"), limpo.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ValueError(f"nenhum objeto JSON na saída: {limpo[:400]}")
    return _exigir_objeto(json.loads(limpo[inicio : fim + 1]))


def _sem_cerca(texto: str) -> str:
    """Remove a cerca de código que alguns modelos insistem em colocar."""
    if not texto.startswith("```"):
        return texto
    corpo = texto[3:]
    if "```" in corpo:
        corpo = corpo.split("```", 1)[0]
    if corpo.lstrip().lower().startswith("json"):
        corpo = corpo.lstrip()[4:]
    return corpo.strip()


def _exigir_objeto(dados: Any) -> dict[str, Any]:
    if not isinstance(dados, dict):
        raise ValueError(f"esperava um objeto JSON no topo, veio {type(dados).__name__}")
    # O `isinstance` só prova `dict`, não `dict[str, Any]` — a chave fica `Unknown`, e
    # ela vaza para todo consumidor. O `cast` é seguro porque a origem é `json.loads`,
    # cujo objeto JSON tem chave string por definição do formato. Não é uma promessa
    # sobre o conteúdo: os valores continuam `Any` e são validados adiante.
    return cast(dict[str, Any], dados)
