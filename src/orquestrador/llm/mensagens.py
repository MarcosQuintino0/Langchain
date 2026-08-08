"""Leitura das mensagens do LangChain: uso de token, texto e tamanho de entrada.

Este é o lado de dentro da fronteira com o provedor: o formato do que chega é dele,
não nosso, e varia entre modelos do OpenRouter. Cada função aqui aceita essa
variação e devolve um tipo do projeto (`UsoDeTokens`, `str`, `int`) — nenhum `Any`
do LangChain sai deste módulo.
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from orquestrador.observabilidade.medidas import UsoDeTokens


def uso_da_mensagem(mensagem: BaseMessage) -> UsoDeTokens:
    """Lê `usage_metadata` (ou o `token_usage` do provedor) de uma resposta."""
    # `usage_metadata` é um `TypedDict` e existe só em `AIMessage` — checar a classe,
    # em vez de sondar o atributo com `getattr`, é o que dá ao verificador os nomes
    # dos contadores. Sondar apagava o tipo da mensagem inteira.
    if isinstance(mensagem, AIMessage) and mensagem.usage_metadata:
        return UsoDeTokens(
            entrada=mensagem.usage_metadata.get("input_tokens") or 0,
            saida=mensagem.usage_metadata.get("output_tokens") or 0,
        )
    # `response_metadata` é o corpo cru do provedor: sem forma declarada, e cada rota
    # do OpenRouter nomeia os contadores à sua maneira. Aqui `Any` é honesto — o que
    # não pode é sair daqui, e não sai: vira `UsoDeTokens`.
    uso: Any = (
        mensagem.response_metadata.get("token_usage")
        or mensagem.response_metadata.get("usage")
        or {}
    )
    if isinstance(uso, dict) and uso:
        contadores = cast(dict[str, Any], uso)
        return UsoDeTokens(
            entrada=int(contadores.get("prompt_tokens") or 0),
            saida=int(contadores.get("completion_tokens") or 0),
        )
    return UsoDeTokens()


def uso_das_mensagens(mensagens: list[BaseMessage]) -> UsoDeTokens:
    soma = UsoDeTokens()
    for mensagem in mensagens:
        if isinstance(mensagem, AIMessage):
            soma = soma + uso_da_mensagem(mensagem)
    return soma


def medir_mensagens(mensagens: list[BaseMessage]) -> tuple[int, int]:
    """(caracteres da instrução fixa, caracteres do resto) do que será enviado.

    A instrução do estágio vai na `SystemMessage` e é constante; o resto é a
    entrada da tentativa. Separar as duas é o que torna o crescimento — ou a
    ausência dele — legível no log.
    """
    instrucao = 0
    entrada = 0
    for mensagem in mensagens:
        tamanho = len(str(mensagem.content or ""))
        if isinstance(mensagem, SystemMessage):
            instrucao += tamanho
        else:
            entrada += tamanho
    return instrucao, entrada


def texto_da_mensagem(mensagem: BaseMessage | None) -> str:
    """Conteúdo textual, tolerando o formato em blocos de alguns provedores."""
    if mensagem is None:
        return ""
    conteudo = mensagem.content
    if isinstance(conteudo, str):
        return conteudo
    partes: list[str] = []
    # `content` em blocos é `list[str | dict]` pelo próprio contrato do LangChain, e
    # o Pydantic dele já recusou qualquer outra coisa na construção da mensagem — por
    # isso o `else` não repete um `isinstance(bloco, dict)`.
    for bloco in conteudo:
        if isinstance(bloco, str):
            partes.append(bloco)
        elif bloco.get("type") == "text":
            partes.append(str(bloco.get("text", "")))
    return "\n".join(partes)
