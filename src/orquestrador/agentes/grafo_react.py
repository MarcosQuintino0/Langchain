"""Todo o acoplamento com o LangGraph, num arquivo só.

Este módulo existe para ter **um nome próprio** no dia da migração. Aqui dentro
estão as quatro coisas que dependem da versão da biblioteca e de mais nada:

* `create_react_agent`, que o LangGraph 1.x marca como depreciado;
* as duas únicas supressões `# pyright: ignore` dos agentes;
* a tolerância à troca de nome do parâmetro de prompt entre versões;
* `SENTINELA_SEM_PASSOS`, um literal copiado de dentro do prebuilt.

Enquanto isso morava em `agentes/mapeador.py`, o `filterwarnings` do
`pyproject.toml` precisava silenciar `DeprecationWarning` de um módulo de 597
linhas — inclusive de qualquer aviso novo que aparecesse ali por outro motivo.
Isolado, ele silencia 100.

O que este módulo **não** faz: não conhece recurso, contrato de saída, gate nem
telemetria. Ele recebe modelo, tools e instrução, e devolve algo que se invoca.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Protocol, TypedDict, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool

from orquestrador.excecoes import FalhaDeEstagio
from orquestrador.llm.mensagens import texto_da_mensagem

# Quando o ReAct fica sem passos, o prebuilt do LangGraph **não** levanta
# GraphRecursionError: ele encerra o grafo devolvendo esta mensagem no lugar da
# resposta do modelo (langgraph/prebuilt/chat_agent_executor.py, `_are_more_steps_needed`).
# É um literal da biblioteca, então o acoplamento é de versão: se ele mudar, a
# detecção degrada para o comportamento antigo (falha por schema inválido), nunca
# para um crash. Revisar ao migrar para `langchain.agents.create_agent`.
SENTINELA_SEM_PASSOS = "Sorry, need more steps to process this request."


class EstadoDoReAct(TypedDict, total=False):
    """O estado que o grafo ReAct devolve, na parte que este projeto lê.

    `total=False` porque o dicionário do LangGraph carrega mais chaves do que esta
    (e mais a cada versão), e porque `messages` pode faltar num encerramento
    anômalo. O que a declaração compra é a única coisa que interessa: que o
    conteúdo de `messages` seja `BaseMessage`, e não `Unknown` atravessando a
    telemetria, o parser e a detecção de "acabaram os passos".
    """

    messages: list[BaseMessage]


class GrafoReAct(Protocol):
    """A fatia do grafo compilado que o mapeador usa: uma invocação, um estado.

    O tipo real é `CompiledStateGraph`, cujos parâmetros genéricos o LangGraph deixa
    indeterminados — o verificador o lê como `CompiledStateGraph[Unknown, ...]`, e
    daí em diante tudo que sai dele é `Unknown`. Declarar o que consumimos corta a
    propagação num ponto só e documenta o acoplamento real com a biblioteca.
    """

    def invoke(
        self, input: dict[str, Any], config: dict[str, Any] | None = None
    ) -> EstadoDoReAct: ...


def criar_agente(modelo: BaseChatModel, ferramentas: list[BaseTool], instrucao: str) -> GrafoReAct:
    """Cria o ReAct do LangGraph, tolerando a troca de nome do parâmetro de prompt.

    É o `create_react_agent` do LangGraph (function calling nativo), não o
    homônimo legado do LangChain clássico, que fazia parsing frágil de texto.

    O LangGraph 1.x marca este símbolo como depreciado e aponta o sucessor
    `langchain.agents.create_agent`, do pacote `langchain` — que não é dependência
    deste projeto. Enquanto o prebuilt existir (removido só na v2.0), ele é o
    caminho; quando sumir, o ImportError abaixo diz exatamente para onde migrar.
    """
    try:
        # Import tardio de propósito: é ele que transforma o
        # sumiço do prebuilt na v2.0 do LangGraph na mensagem de migração abaixo,
        # em vez de um ImportError na carga do módulo, longe da explicação.
        from langgraph.prebuilt import (  # noqa: PLC0415
            create_react_agent,  # pyright: ignore[reportDeprecated, reportUnknownVariableType]
        )
    except ImportError as erro:  # pragma: no cover - ambiente incompleto
        raise FalhaDeEstagio(
            "não foi possível importar langgraph.prebuilt.create_react_agent. "
            'Rode `pip install -e ".[dev]"`; se o LangGraph já for 2.x, '
            "migre para `from langchain.agents import create_agent` (pacote langchain)."
        ) from erro

    # As supressões deste módulo são só duas — a do import acima e a desta linha —, e
    # nenhuma delas é sobre este código:
    #
    #   reportDeprecated          a migração para `langchain.agents.create_agent` que a
    #                             docstring explica e que a versão pinada não permite;
    #   reportUnknownVariableType o LangGraph devolve `CompiledStateGraph[Unknown, ...]`
    #                             — genéricos que ele mesmo não fecha.
    #
    # Elas param aqui: dar um nome tipado ao símbolo faz as três chamadas abaixo
    # dispensarem supressão, e o `cast` para `GrafoReAct` impede que o `Unknown` do
    # retorno saia desta função.
    criar: Callable[..., Any] = create_react_agent  # pyright: ignore[reportDeprecated, reportUnknownVariableType]
    parametros = inspect.signature(criar).parameters
    for nome in ("prompt", "state_modifier", "messages_modifier"):
        if nome in parametros:
            return cast(GrafoReAct, criar(modelo, ferramentas, **{nome: instrucao}))
    return cast(GrafoReAct, criar(modelo, ferramentas))


def acabaram_os_passos(mensagens: list[BaseMessage]) -> bool:
    """O ReAct encerrou por falta de passos, em vez de responder?

    Mora aqui, e não no mapeador, porque a pergunta é sobre o comportamento do
    prebuilt: quem sabe reconhecer o encerramento anômalo é quem conhece a
    sentinela.
    """
    if not mensagens:
        return False
    return texto_da_mensagem(mensagens[-1]).strip() == SENTINELA_SEM_PASSOS
