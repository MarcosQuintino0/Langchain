"""Leitura das mensagens do LangChain: uso de token, texto e tamanho de entrada."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from orquestrador.contratos import UsoDeTokens


def uso_da_mensagem(mensagem: BaseMessage) -> UsoDeTokens:
    """Lê `usage_metadata` (ou o `token_usage` do provedor) de uma resposta."""
    bruto = getattr(mensagem, "usage_metadata", None)
    if isinstance(bruto, dict) and bruto:
        return UsoDeTokens(
            entrada=int(bruto.get("input_tokens") or 0),
            saida=int(bruto.get("output_tokens") or 0),
        )
    metadados = getattr(mensagem, "response_metadata", None) or {}
    uso = metadados.get("token_usage") or metadados.get("usage") or {}
    if isinstance(uso, dict) and uso:
        return UsoDeTokens(
            entrada=int(uso.get("prompt_tokens") or 0),
            saida=int(uso.get("completion_tokens") or 0),
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
        tamanho = len(str(getattr(mensagem, "content", "") or ""))
        if isinstance(mensagem, SystemMessage):
            instrucao += tamanho
        else:
            entrada += tamanho
    return instrucao, entrada


def texto_da_mensagem(mensagem: BaseMessage | None) -> str:
    """Conteúdo textual, tolerando o formato em blocos de alguns provedores."""
    if mensagem is None:
        return ""
    conteudo = getattr(mensagem, "content", "")
    if isinstance(conteudo, str):
        return conteudo
    partes: list[str] = []
    for bloco in conteudo or []:
        if isinstance(bloco, str):
            partes.append(bloco)
        elif isinstance(bloco, dict) and bloco.get("type") == "text":
            partes.append(str(bloco.get("text", "")))
    return "\n".join(partes)
