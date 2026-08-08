"""Cliente OpenRouter e seleção de modelo por estágio.

Princípio 6: o nome do modelo de cada estágio vem da configuração. Nenhum nome de
modelo aparece neste arquivo.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeConfiguracao


def criar_modelo(config: Config, estagio: str) -> BaseChatModel:
    """Instancia o modelo do estágio apontando para o OpenRouter."""
    from langchain_openai import ChatOpenAI  # import tardio: dry-run não precisa

    parametros = config.estagio(estagio)
    if parametros.modelo.strip().startswith("<"):
        raise ErroDeConfiguracao(
            f"[estagios.{estagio}] ainda está com o placeholder de modelo "
            f"({parametros.modelo!r}). Defina o id do modelo no OpenRouter."
        )

    extras: dict[str, Any] = {}
    if parametros.max_tokens:
        extras["max_tokens"] = parametros.max_tokens
    cabecalhos = config.openrouter.headers()
    if cabecalhos:
        extras["default_headers"] = cabecalhos
    return ChatOpenAI(
        model=parametros.modelo,
        base_url=str(config.openrouter.base_url),
        api_key=config.openrouter.chave(),
        temperature=parametros.temperatura,
        timeout=config.openrouter.timeout_s,
        max_retries=config.openrouter.max_retries,
        **extras,
    )
