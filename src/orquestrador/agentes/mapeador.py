"""Bloco 1 — agente mapeador (LLM com tools).

Agente ReAct que explora o backend incrementalmente e emite dois artefatos:
`Inventario` e `Manifesto`. Processa **um recurso por vez** e zera o histórico
entre recursos (princípio 3) — cada tentativa é uma invocação nova, com lista de
mensagens nova.

O conteúdo da instrução do estágio é Fase 2; aqui só a estrutura, as tools e o
contrato de saída.
"""

from __future__ import annotations

import functools
import inspect
import itertools
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field, ValidationError

from orquestrador.config import Config
from orquestrador.contratos import (
    SUFIXO_SCHEMA,
    Delta,
    Recurso,
    RegistroDeChamada,
    RegistroDeTool,
    SaidaMapeador,
    UsoDeTokens,
    Violacao,
)
from orquestrador.excecoes import ErroDeFerramenta, FalhaDeEstagio
from orquestrador.ferramentas import arquivos as fa
from orquestrador.ferramentas.graphify import Graphify
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    TentativaDeProvedor,
    chamar_com_retentativas,
    descrever_volta,
)
from orquestrador.llm.estruturado import violacoes_de_validacao
from orquestrador.llm.mensagens import texto_da_mensagem, uso_das_mensagens
from orquestrador.llm.montagem import (
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
)
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

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
    budget: int | None = Field(
        default=None,
        description=(
            "Orçamento da resposta em tokens (padrão ~2000). Use APENAS quando a "
            "resposta avisar que truncou E o alvo não tiver aparecido. Prefira "
            "reconsultar o símbolo específico: resposta maior é reenviada em toda "
            "volta seguinte, então o custo dela se multiplica."
        ),
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
    relacao: str | None = Field(
        default=None,
        description=(
            "Filtro de tipo de aresta. NUNCA use na primeira consulta: o vocabulário "
            "depende do extrator da linguagem, e um nome adivinhado devolve 'No "
            "affected nodes found' — vazio com cara de resposta legítima, que se lê "
            "como 'não há dependente'. Rode sem filtro, leia os rótulos entre "
            "colchetes da saída, e só então filtre por um deles. Em Java a herança "
            "sai como 'inherits', e é assim que se confirma quem herda de um "
            "controller abstrato sem depender do 'extends' da primeira linha."
        ),
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


def criar_ferramentas(
    config: Config,
    *,
    telemetria: Telemetria | None = None,
    recurso: str = "",
    tentativa: int = 0,
) -> list[BaseTool]:
    """As cinco tools do mapeador, já ligadas à configuração desta execução.

    Com `telemetria`, cada chamada vira um `RegistroDeTool`. A instrumentação é
    opcional para que as tools continuem construtíveis isoladamente em teste, mas o
    pipeline sempre a liga: é dela que sai a resposta para "o grafo foi consultado
    antes de ler arquivo?".
    """
    grafo = Graphify(config)
    confinamento = fa.Confinamento(config.caminhos.backend)
    # Escopo por invocação de `criar_ferramentas`, que é por tentativa do estágio —
    # inclusive as voltas do mini-loop de schema, que são a mesma tentativa.
    contador = itertools.count(1)

    def observado(nome: str, funcao: Callable[..., str]) -> Callable[..., str]:
        """Envolve uma tool para medi-la sem tocar no que ela devolve ao modelo."""
        assinatura = inspect.signature(funcao)

        @functools.wraps(funcao)
        def envolvida(*posicionais: Any, **nomeados: Any) -> str:
            inicio = time.perf_counter()
            saida = funcao(*posicionais, **nomeados)
            if telemetria is not None:
                argumentos = assinatura.bind(*posicionais, **nomeados)
                argumentos.apply_defaults()
                telemetria.registrar_tool(
                    RegistroDeTool(
                        estagio=ESTAGIO,
                        recurso=recurso,
                        tentativa=tentativa,
                        ordem=next(contador),
                        nome=nome,
                        argumentos=dict(argumentos.arguments),
                        caracteres=len(saida),
                        duracao_s=time.perf_counter() - inicio,
                        # As tools engolem a exceção e devolvem "ERRO: ..." como texto
                        # normal, para o modelo poder se corrigir. Este é o único
                        # lugar onde a falha vira dado.
                        erro=saida.startswith("ERRO:"),
                    )
                )
            return saida

        return envolvida

    def _query(simbolo: str, budget: int | None = None) -> str:
        try:
            return grafo.query(simbolo, budget=budget)
        except ErroDeFerramenta as erro:
            return f"ERRO: {erro}"

    def _affected(entidade: str, depth: int = 1, relacao: str | None = None) -> str:
        try:
            return grafo.affected(entidade, depth=depth, relacao=relacao)
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
            func=observado("graphify_query", _query),
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
            func=observado("graphify_affected", _affected),
            name="graphify_affected",
            args_schema=ArgsGraphifyAffected,
            description=(
                "Pergunta inversa: QUEM USA este símbolo. Use antes de concluir que "
                "ninguém depende do recurso. 'No unique node match' significa NOME "
                "AMBÍGUO — reconsulte com o nome exato da classe; o vazio real tem outra "
                "frase ('No affected nodes found'). Confira no cabeçalho da resposta o "
                "nome que o Graphify resolveu: quando ele difere do pedido, os "
                "dependentes são de outra classe. Rode SEM `relacao` primeiro e leia os "
                'rótulos da saída antes de filtrar; `relacao="inherits"` (em Java) '
                "lista quem herda de um controller abstrato."
            ),
        ),
        StructuredTool.from_function(
            func=observado("ler_arquivo", _ler),
            name="ler_arquivo",
            args_schema=ArgsLerArquivo,
            description=(
                "Lê um trecho numerado de um arquivo do backend. É aqui que método, rota, "
                "campos e regras são confirmados — o grafo só aponta onde olhar."
            ),
        ),
        StructuredTool.from_function(
            func=observado("listar_diretorio", _listar),
            name="listar_diretorio",
            args_schema=ArgsListarDiretorio,
            description="Lista o conteúdo de um diretório do backend.",
        ),
        StructuredTool.from_function(
            func=observado("buscar_no_backend", _buscar),
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


class EstadoDoReAct(TypedDict, total=False):
    """O estado que o grafo ReAct devolve, na parte que este módulo lê.

    `total=False` porque o dicionário do LangGraph carrega mais chaves do que estas
    (e mais a cada versão), e porque `messages` pode faltar num encerramento
    anômalo. O que a declaração compra é a única coisa que interessa aqui: que o
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


def _criar_agente(modelo: BaseChatModel, ferramentas: list[BaseTool], instrucao: str) -> GrafoReAct:
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
    modelo: BaseChatModel,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None = None,
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
    ferramentas = criar_ferramentas(
        config, telemetria=telemetria, recurso=recurso.nome, tentativa=tentativa
    )
    agente = _criar_agente(modelo, ferramentas, instrucao)

    if delta is not None:
        entrada = montar_entrada_reparo(artefato_atual or "(artefato ausente)", delta)
    else:
        entrada = entrada_inicial(config, recurso)

    ultimo_texto = ""
    ultimas_violacoes: list[Violacao] = []

    politica = PoliticaDeRetentativa.do_config(config)

    for passo in range(1, parametros.max_tentativas_schema + 1):
        inicio = time.perf_counter()

        def invocar(entrada: str = entrada) -> EstadoDoReAct:
            return agente.invoke(
                {"messages": [HumanMessage(content=entrada)]},
                config={"recursion_limit": parametros.limite_passos},
            )

        # `passo` e `entrada` viajam como padrão porque os dois mudam a cada volta do
        # laço: capturados por referência, a telemetria mediria o que a tentativa
        # SEGUINTE vai enviar, não o que esta enviou.
        def perdeu_a_volta(
            volta: TentativaDeProvedor, passo: int = passo, entrada: str = entrada
        ) -> None:
            """Volta perdida por indisponibilidade também custou tempo e dinheiro.

            Aqui a perda é maior que no executor: o que se joga fora é a exploração
            inteira do ReAct, com todas as respostas de tool já pagas. Sem este
            registro, o gasto sumiria do relatório e reapareceria só na fatura.
            """
            telemetria.registrar(
                RegistroDeChamada(
                    estagio=ESTAGIO,
                    recurso=recurso.nome,
                    tentativa=tentativa,
                    modelo=parametros.modelo,
                    uso=UsoDeTokens(),
                    duracao_s=volta.duracao_s,
                    simulado=getattr(modelo, "simulado", False),
                    detalhe=descrever_volta(volta, politica, prefixo=f"schema:{passo}"),
                    caracteres_instrucao=len(instrucao),
                    caracteres_entrada=len(entrada),
                )
            )

        try:
            estado = chamar_com_retentativas(
                invocar,
                politica=politica,
                estagio=ESTAGIO,
                recurso=recurso.nome,
                ao_falhar=perdeu_a_volta,
            )
        except GraphRecursionError as erro:
            # GraphRecursionError herda de RecursionError, não de FalhaDeEstagio: sem
            # esta conversão ele passa por cima dos `except` de pipeline.py e derruba a
            # execução inteira com traceback, em vez de falhar só este recurso.
            raise _sem_passos(parametros.limite_passos, recurso.nome, str(erro)) from erro

        mensagens = estado.get("messages") or []
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


def artefato_em_disco(*, manifesto: Path, inventario: Path, dir_schemas: Path, recurso: str) -> str:
    """O artefato **inteiro** do Bloco 1, lido do staging, para o prompt de reparo.

    `SaidaMapeador` tem três partes — inventário, manifesto e schemas — e o reparo
    recebia só o manifesto. Uma violação sobre campo (`QAAPI-025`) ou sobre schema
    ausente (`QAAPI-027`) fala de um arquivo que não estava à vista: o modelo
    reescrevia o manifesto adivinhando o que o schema declara, e o gate reprovava
    de novo pelo mesmo motivo.

    Isto **não** afrouxa o princípio 2. A fórmula continua
    `instrução_fixa + artefato_atual + delta.violacoes`; o que muda é que "artefato
    atual" passou a significar o artefato, e não uma fatia arbitrária dele. Nada de
    histórico, de tentativa anterior nem de raciocínio entra aqui — e o bundle é
    função apenas do estado do disco, então repetir a tentativa não o faz crescer.

    A ordem das seções é fixa e os schemas saem ordenados por caminho: bundle que
    muda de ordem entre tentativas invalida cache de prompt e faz diff de log
    parecer mudança de conteúdo.
    """
    partes = [
        _secao("inventario.json", inventario),
        _secao("_support/cobertura.json", manifesto),
    ]
    raiz_do_recurso = dir_schemas / recurso
    if raiz_do_recurso.is_dir():
        for arquivo in sorted(raiz_do_recurso.rglob(f"*{SUFIXO_SCHEMA}")):
            partes.append(_secao(f"schemas/{arquivo.relative_to(dir_schemas).as_posix()}", arquivo))
    return "\n\n".join(partes)


def _secao(rotulo: str, arquivo: Path) -> str:
    conteudo = arquivo.read_text(encoding="utf-8") if arquivo.is_file() else "(ausente)"
    return f"--- {rotulo} ---\n{conteudo.rstrip()}"


def _sem_passos(limite: int, recurso: str, detalhe: str) -> FalhaDeEstagio:
    """A falha de "acabaram os passos", com as duas saídas possíveis na mensagem."""
    return FalhaDeEstagio(
        f"o mapeador estourou o limite de {limite} passos no recurso {recurso!r} "
        "sem chegar a uma resposta final. Ou aumente [estagios.mapeador].limite_passos "
        "na configuração, ou reduza o escopo do recurso (recurso grande e composto "
        f"vira sub-domínios). Detalhe do LangGraph: {detalhe}"
    )


def _acabaram_os_passos(mensagens: list[BaseMessage]) -> bool:
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
