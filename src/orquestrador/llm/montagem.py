"""Montagem dos prompts — a implementação do princípio 2.

    prompt_reparo = instrucao_fixa_do_estagio + artefato_atual + delta.violacoes

Nada além disso. Sem histórico de tentativas: é esse corte que troca o custo
quadrático por custo linear, e ele mora aqui, num lugar só, para não escapar por
descuido em algum estágio.

Mora em `llm/` porque o que ele produz é **entrada de modelo**: junto do cliente,
da saída estruturada e da contagem de mensagens. Não conhece gate nem recurso — o
`Delta` chega pronto, e este módulo só o renderiza.

O **conteúdo** das instruções fixas é Fase 2; aqui só a carga do arquivo e a
substituição de `{{chave}}`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orquestrador.contratos import Delta
from orquestrador.raiz import DIR_PROMPTS_PADRAO

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class PromptAusente(FileNotFoundError):
    pass


def carregar_prompt(
    nome: str,
    dados: dict[str, Any] | None = None,
    *,
    dir_prompts: Path | None = None,
) -> str:
    """Lê `<dir_prompts>/<nome>.md` e substitui os `{{placeholders}}` conhecidos.

    O diretório vem da configuração (`[caminhos].prompts`), com padrão na raiz do
    projeto: prompt é conteúdo editorial, não código de pacote.

    Placeholder sem valor é mantido literal — o arquivo é um placeholder da Fase 1
    e apagá-lo em silêncio esconderia a lacuna.
    """
    arquivo = (dir_prompts or DIR_PROMPTS_PADRAO) / f"{nome}.md"
    if not arquivo.is_file():
        raise PromptAusente(f"prompt do estágio não encontrado: {arquivo}")
    texto = arquivo.read_text(encoding="utf-8")
    valores = dados or {}

    def substituir(casamento: re.Match[str]) -> str:
        chave = casamento.group(1)
        if chave not in valores:
            return casamento.group(0)
        return str(valores[chave])

    return _PLACEHOLDER.sub(substituir, texto)


def esquema_json(tipo: type[BaseModel]) -> str:
    """JSON Schema do contrato de saída, para colar no prompt."""
    return json.dumps(tipo.model_json_schema(by_alias=True), ensure_ascii=False, indent=2)


def montar_entrada_inicial(secoes: dict[str, str]) -> str:
    """Concatena as seções da entrada da primeira tentativa."""
    partes: list[str] = []
    for titulo, corpo in secoes.items():
        if corpo is None or str(corpo).strip() == "":
            continue
        partes.append(f"## {titulo}\n\n{corpo}".rstrip())
    return "\n\n".join(partes) + "\n"


def montar_entrada_reparo(artefato_atual: str, delta: Delta) -> str:
    """A entrada de uma tentativa de reparo: artefato atual + violações. Só isso."""
    return montar_entrada_inicial(
        {
            f"Artefato atual ({delta.recurso})": f"```\n{artefato_atual.strip()}\n```",
            "Violações a corrigir": delta.render(),
        }
    )
