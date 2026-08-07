"""Bloco 1 — agente mapeador (LLM com tools).

Agente ReAct que explora o backend incrementalmente e emite dois artefatos:
`Inventario` e `Manifesto`. Processa **um recurso por vez** e zera o histórico
entre recursos (princípio 3) — cada tentativa é uma invocação nova, com lista de
mensagens nova.

O conteúdo da instrução do estágio é Fase 2; aqui só a estrutura, as tools e o
contrato de saída.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field, ValidationError

from orquestrador.config import Config
from orquestrador.contratos import (
    Delta,
    RegistroDeChamada,
    Recurso,
    SaidaMapeador,
    UsoDeTokens,
    Violacao,
)
from orquestrador.ferramentas import arquivos as fa
from orquestrador.ferramentas.graphify import Graphify
from orquestrador.ferramentas.processo import ErroDeFerramenta
from orquestrador.excecoes import FalhaDeEstagio
from orquestrador.llm.estruturado import violacoes_de_validacao
from orquestrador.llm.mensagens import texto_da_mensagem, uso_das_mensagens
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.textos import extrair_json
from orquestrador.montagem import (
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
)

ESTAGIO = "mapeador"

# Quando o ReAct fica sem passos, o prebuilt do LangGraph **não** levanta
# GraphRecursionError: ele encerra o grafo devolvendo esta mensagem no lugar da
# resposta do modelo (langgraph/prebuilt/chat_agent_executor.py, `_are_more_steps_needed`).
# É um literal da biblioteca, então o acoplamento é de versão: se ele mudar, a
# detecção degrada para o comportamento antigo (falha por schema inválido), nunca
# para um crash. Revisar ao migrar para `langchain.agents.create_agent`.
SENTINELA_SEM_PASSOS = "Sorry, need more steps to process this request."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ArgsGraphifyQuery(BaseModel):
    simbolo: str = Field(
        description=(
            "Símbolo no vocabulário do CÓDIGO (CancellationController, Cancellation), "
            "não linguagem natural: o matcher é literal, sem stemming nem sinônimos."
        )
    )


class ArgsGraphifyAffected(BaseModel):
    entidade: str = Field(
        description=(
            "Nome da ENTIDADE (Cancellation), não do controller: a dependência que "
            "impede uma exclusão mora em outra entidade que aponta para esta."
        )
    )
    depth: int = Field(
        default=1,
        description="Profundidade da travessia. Use 1: o padrão 2 traz alcance transitivo.",
    )


class ArgsLerArquivo(BaseModel):
    caminho: str = Field(description="Caminho do arquivo, relativo à raiz do backend.")
    offset: int = Field(default=0, description="Primeira linha (0 = início do arquivo).")
    limit: int = Field(default=500, description="Quantas linhas ler a partir do offset.")


class ArgsListarDiretorio(BaseModel):
    caminho: str = Field(description="Caminho do diretório, relativo à raiz do backend.")


class ArgsBuscar(BaseModel):
    padrao: str = Field(description="Expressão regular procurada no conteúdo dos arquivos.")
    glob: str | None = Field(
        default=None, description='Filtro de nome de arquivo, ex.: "*.java", "*.ts".'
    )


def criar_ferramentas(config: Config) -> list[BaseTool]:
    """As cinco tools do mapeador, já ligadas à configuração desta execução."""
    grafo = Graphify(config)
    confinamento = fa.Confinamento(config.caminhos.backend)

    def _query(simbolo: str) -> str:
        try:
            return grafo.query(simbolo)
        except ErroDeFerramenta as erro:
            return f"ERRO: {erro}"

    def _affected(entidade: str, depth: int = 1) -> str:
        try:
            return grafo.affected(entidade, depth=depth)
        except ErroDeFerramenta as erro:
            return f"ERRO: {erro}"

    def _ler(caminho: str, offset: int = 0, limit: int = 500) -> str:
        try:
            return fa.ler_arquivo(
                confinamento,
                caminho,
                offset,
                limit,
                max_bytes=config.execucao.max_bytes_arquivo,
            )
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    def _listar(caminho: str) -> str:
        try:
            return fa.listar_diretorio(confinamento, caminho)
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    def _buscar(padrao: str, glob: str | None = None) -> str:
        try:
            return fa.buscar(
                confinamento,
                padrao,
                glob,
                max_resultados=config.execucao.max_resultados_busca,
                max_bytes=config.execucao.max_bytes_arquivo,
            )
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    return [
        StructuredTool.from_function(
            func=_query,
            name="graphify_query",
            args_schema=ArgsGraphifyQuery,
            description=(
                "Pergunta ao grafo estrutural O QUE UM SÍMBOLO USA: devolve controller, "
                "entidade, service, DAO e getters com ARQUIVO E LINHA, a superclasse e os "
                "métodos sobrescritos. Consulte o grafo ANTES de procurar no backend. "
                "Quando o alvo herda de um controller abstrato, consulte o símbolo "
                "específico (a entidade ou o método concreto): partindo do controller a "
                "travessia sobe na superclasse e desce em todos os irmãos, gastando o "
                "orçamento com outros recursos. O grafo LOCALIZA; a fonte confirma."
            ),
        ),
        StructuredTool.from_function(
            func=_affected,
            name="graphify_affected",
            args_schema=ArgsGraphifyAffected,
            description=(
                "Pergunta inversa: QUEM USA este símbolo. Use antes de concluir que "
                "ninguém depende do recurso. 'No unique node match' significa NOME "
                "AMBÍGUO — reconsulte com o nome exato da classe; o vazio real tem outra "
                "frase ('No affected nodes found'). Confira no cabeçalho da resposta o "
                "nome que o Graphify resolveu: quando ele difere do pedido, os "
                "dependentes são de outra classe."
            ),
        ),
        StructuredTool.from_function(
            func=_ler,
            name="ler_arquivo",
            args_schema=ArgsLerArquivo,
            description=(
                "Lê um trecho numerado de um arquivo do backend. É aqui que método, rota, "
                "campos e regras são confirmados — o grafo só aponta onde olhar."
            ),
        ),
        StructuredTool.from_function(
            func=_listar,
            name="listar_diretorio",
            args_schema=ArgsListarDiretorio,
            description="Lista o conteúdo de um diretório do backend.",
        ),
        StructuredTool.from_function(
            func=_buscar,
            name="buscar_no_backend",
            args_schema=ArgsBuscar,
            description=(
                "Busca textual por expressão regular no backend. ÚLTIMO RECURSO, não "
                "primeiro passo: o que o grafo responde, pergunta-se ao grafo. Nunca "
                "busca dentro do graph.json (dezenas de MB)."
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------


def _criar_agente(modelo: Any, ferramentas: list[BaseTool], instrucao: str) -> Any:
    """Cria o ReAct do LangGraph, tolerando a troca de nome do parâmetro de prompt.

    É o `create_react_agent` do LangGraph (function calling nativo), não o
    homônimo legado do LangChain clássico, que fazia parsing frágil de texto.

    O LangGraph 1.x marca este símbolo como depreciado e aponta o sucessor
    `langchain.agents.create_agent`, do pacote `langchain` — que não é dependência
    deste projeto. Enquanto o prebuilt existir (removido só na v2.0), ele é o
    caminho; quando sumir, o ImportError abaixo diz exatamente para onde migrar.
    """
    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError as erro:  # pragma: no cover - ambiente incompleto
        raise FalhaDeEstagio(
            "não foi possível importar langgraph.prebuilt.create_react_agent. "
            "Rode `pip install -r requirements.txt`; se o LangGraph já for 2.x, "
            "migre para `from langchain.agents import create_agent` (pacote langchain)."
        ) from erro

    parametros = inspect.signature(create_react_agent).parameters
    for nome in ("prompt", "state_modifier", "messages_modifier"):
        if nome in parametros:
            return create_react_agent(modelo, ferramentas, **{nome: instrucao})
    return create_react_agent(modelo, ferramentas)


def instrucao_do_estagio(config: Config, recurso: Recurso) -> str:
    """Instrução fixa do estágio: prompt-placeholder + contrato de saída."""
    return carregar_prompt(
        ESTAGIO,
        {
            "recurso": recurso.nome,
            "caminho_backend": str(config.caminhos.backend),
            "caminho_graph": str(config.caminhos.graph_abs),
            "caminho_recurso": str(recurso.caminho_testes),
            "schema_json": esquema_json(SaidaMapeador),
        },
        dir_prompts=config.caminhos.prompts,
    )


def entrada_inicial(config: Config, recurso: Recurso) -> str:
    return montar_entrada_inicial(
        {
            "Recurso alvo": recurso.nome,
            "Backend": str(config.caminhos.backend),
            "Grafo estrutural": str(config.caminhos.graph_abs),
            "Diretório do recurso no projeto de testes": str(recurso.caminho_testes),
        }
    )


def executar(
    config: Config,
    recurso: Recurso,
    *,
    modelo: Any,
    telemetria: Telemetria,
    registro: Any = None,
    tentativa: int = 1,
    delta: Delta | None = None,
    artefato_atual: str | None = None,
) -> SaidaMapeador:
    """Uma tentativa do mapeador para um recurso.

    Com `delta`, a entrada é apenas `instrucao_fixa + artefato_atual + violacoes`
    (princípio 2). O histórico das tentativas anteriores nunca é reenviado.
    """
    parametros = config.estagio(ESTAGIO)
    instrucao = instrucao_do_estagio(config, recurso)
    ferramentas = criar_ferramentas(config)
    agente = _criar_agente(modelo, ferramentas, instrucao)

    if delta is not None:
        entrada = montar_entrada_reparo(artefato_atual or "(artefato ausente)", delta)
    else:
        entrada = entrada_inicial(config, recurso)

    ultimo_texto = ""
    ultimas_violacoes: list[Violacao] = []

    for passo in range(1, parametros.max_tentativas_schema + 1):
        inicio = time.perf_counter()
        try:
            estado = agente.invoke(
                {"messages": [HumanMessage(content=entrada)]},
                config={"recursion_limit": parametros.limite_passos},
            )
        except GraphRecursionError as erro:
            # GraphRecursionError herda de RecursionError, não de FalhaDeEstagio: sem
            # esta conversão ele passa por cima dos `except` de pipeline.py e derruba a
            # execução inteira com traceback, em vez de falhar só este recurso.
            raise _sem_passos(parametros.limite_passos, recurso.nome, str(erro)) from erro

        mensagens = estado.get("messages", [])
        uso = uso_das_mensagens(mensagens)
        telemetria.registrar(
            RegistroDeChamada(
                estagio=ESTAGIO,
                recurso=recurso.nome,
                tentativa=tentativa,
                modelo=parametros.modelo,
                uso=uso or UsoDeTokens(),
                duracao_s=time.perf_counter() - inicio,
                simulado=getattr(modelo, "simulado", False),
                detalhe=f"schema:{passo}; mensagens:{len(mensagens)}",
                caracteres_instrucao=len(instrucao),
                caracteres_entrada=len(entrada),
            )
        )

        # Depois de registrar a telemetria: os tokens desta volta foram gastos de
        # verdade e precisam aparecer no relatório, mesmo que ela termine em falha.
        if _acabaram_os_passos(mensagens):
            # O caminho que realmente acontece no LangGraph 1.x (ver SENTINELA_SEM_PASSOS):
            # em vez de levantar, o agente encerra com uma mensagem de desculpa. Sem
            # reconhecê-la aqui, o pipeline gastaria todas as tentativas de schema tentando
            # parsear essa frase como JSON e falharia com um motivo que esconde a causa.
            raise _sem_passos(parametros.limite_passos, recurso.nome, SENTINELA_SEM_PASSOS)

        ultimo_texto = texto_da_mensagem(mensagens[-1] if mensagens else None)
        saida, ultimas_violacoes = _parsear(ultimo_texto)
        if saida is not None:
            return saida

        if registro:
            registro.evento(
                "delta",
                estagio="schema",
                de=ESTAGIO,
                recurso=recurso.nome,
                tentativa=passo,
                codigos=[violacao.codigo for violacao in ultimas_violacoes],
            )
        entrada = montar_entrada_reparo(
            ultimo_texto or "(vazio)",
            Delta(
                estagio="schema",
                recurso=recurso.nome,
                violacoes=ultimas_violacoes,
                tentativa=passo,
            ),
        )

    detalhes = "; ".join(violacao.render() for violacao in ultimas_violacoes)
    raise FalhaDeEstagio(
        f"mapeador não produziu SaidaMapeador válida para o recurso {recurso.nome!r} "
        f"em {parametros.max_tentativas_schema} tentativa(s) de schema. Violações: {detalhes}"
    )


def _sem_passos(limite: int, recurso: str, detalhe: str) -> FalhaDeEstagio:
    """A falha de "acabaram os passos", com as duas saídas possíveis na mensagem."""
    return FalhaDeEstagio(
        f"o mapeador estourou o limite de {limite} passos no recurso {recurso!r} "
        "sem chegar a uma resposta final. Ou aumente [estagios.mapeador].limite_passos "
        "na configuração, ou reduza o escopo do recurso (recurso grande e composto "
        f"vira sub-domínios). Detalhe do LangGraph: {detalhe}"
    )


def _acabaram_os_passos(mensagens: list[Any]) -> bool:
    """O ReAct encerrou por falta de passos, em vez de responder?"""
    if not mensagens:
        return False
    return texto_da_mensagem(mensagens[-1]).strip() == SENTINELA_SEM_PASSOS


def _parsear(texto: str) -> tuple[SaidaMapeador | None, list[Violacao]]:
    try:
        dados = extrair_json(texto)
    except ValueError as erro:  # JSONDecodeError herda de ValueError
        return None, [
            Violacao(
                codigo="QAORQ-011",
                mensagem=(
                    f"a mensagem final não é um objeto JSON válido ({erro}). "
                    "Termine respondendo APENAS com o JSON do contrato."
                ),
            )
        ]
    try:
        return SaidaMapeador.model_validate(dados), []
    except ValidationError as erro:
        return None, violacoes_de_validacao(erro)
