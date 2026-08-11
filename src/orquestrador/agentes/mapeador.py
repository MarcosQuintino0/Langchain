"""Bloco 1 — agente mapeador (LLM com tools), em duas fases.

**Fase 1 — explorar.** Agente ReAct lê o backend com tools e raciocínio livre, e
termina em **notas de descoberta**: texto, não JSON. Prender a exploração num
contrato Pydantic era o que concentrava o raciocínio na serialização — medido em
2026-08-10, 60% da saída do estágio era pensamento gasto emitindo e reemitindo um
JSON gigante, e um único erro de validação custava 7,5 minutos reemitindo tudo.

**Fase 2 — serializar.** As notas viram os quatro artefatos em fatias
independentes: o **inventário sai por código** (parser determinístico da seção
combinada das notas, com fallback de modelo para backend fora da matriz
estática); manifesto, dossiê e schemas saem em chamadas pequenas e paralelas, uma
por artefato. Resposta pequena é o que mantém o raciocínio saudável — a mesma
medição que curou o planejador — e reparo, de schema ou de gate, reemite **só a
fatia dona da violação**.

O julgamento acontece inteiro na fase 1; a fase 2 transcreve. É a divisão do
princípio 4 aplicada à emissão: o que o script sabe fazer (inventário, montagem,
conferência), o script faz.

Processa **um recurso por vez** e zera o histórico entre recursos (princípio 3).
As notas são artefato em disco entre as fases e entre tentativas (princípio 1).
Nada aqui depende de provedor: paralelismo é do cliente, saída estruturada usa o
`modo_estruturado` configurado, e nenhum corpo específico de roteador é montado.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from orquestrador.agentes.ferramentas_do_mapeador import criar_ferramentas
from orquestrador.agentes.grafo_react import (
    SENTINELA_SEM_PASSOS,
    EstadoDoReAct,
    acabaram_os_passos,
    criar_agente,
)
from orquestrador.agentes.guarda_de_orcamento import exigir_folga
from orquestrador.analise_estatica.extrator_de_endpoints import arquivos_do_grafo, extrair
from orquestrador.config import Config
from orquestrador.dominio.artefatos import SUFIXO_SCHEMA, FatiaDeSchemas, SaidaMapeador
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.notas import (
    endpoints_das_notas,
    endpoints_nao_citados,
    rotas_dinamicas_das_notas,
    schemas_das_notas,
)
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import FalhaDeEstagio, GrafoNaoPreparado
from orquestrador.ferramentas.arquivos import CaminhoForaDaRaiz
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    TentativaDeProvedor,
    chamar_com_retentativas,
    descrever_volta,
)
from orquestrador.llm.estruturado import GeradorEstruturado, exigir_resposta_inteira
from orquestrador.llm.mensagens import texto_da_mensagem
from orquestrador.llm.montagem import (
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
)
from orquestrador.observabilidade.medidas import RegistroDeChamada, UsoDeTokens
from orquestrador.observabilidade.provedor import ObservadorDeProvedor
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

ESTAGIO = "mapeador"

# As fatias que passam pelo modelo, na ordem canônica de montagem. O inventário
# está fora porque o caminho normal dele é código puro; ele vira fatia de modelo
# apenas como fallback, quando as notas não têm a seção combinada.
FATIAS_DE_MODELO: tuple[str, ...] = ("manifesto", "dossie", "schemas")

_TIPO_DA_FATIA: dict[str, type[Manifesto] | type[DossieDoRecurso] | type[FatiaDeSchemas]] = {
    "manifesto": Manifesto,
    "dossie": DossieDoRecurso,
    "schemas": FatiaDeSchemas,
}

# Violação → fatia dona. Reemitir tudo por causa de um erro localizado foi o
# defeito medido que motivou o fatiamento; código desconhecido cai no conjunto
# inteiro de fatias de modelo — reemissão a mais é custo, reemissão a menos é
# reparo que não repara.
_FATIAS_DO_CODIGO: dict[str, frozenset[str]] = {
    "QAORQ-002": frozenset({"manifesto"}),
    "QAORQ-003": frozenset({"manifesto"}),
    "QAORQ-040": frozenset({"schemas"}),
    "QAORQ-060": frozenset({"dossie"}),
    "QAORQ-061": frozenset({"dossie", "manifesto"}),
    "QAORQ-062": frozenset({"dossie"}),
    "QAORQ-063": frozenset({"dossie"}),
    "QAAPI-027": frozenset({"schemas", "manifesto"}),
}
_TODAS_AS_FATIAS = frozenset(FATIAS_DE_MODELO)


@dataclass(frozen=True)
class MapeamentoProduzido:
    """O que uma tentativa do Bloco 1 devolve: o contrato e a memória que o gerou.

    As notas viajam junto porque são artefato (princípio 1): o pipeline as
    persiste, o reparo da tentativa seguinte parte delas em vez de re-explorar, e
    um humano lê nelas o que o modelo leu no backend.
    """

    saida: SaidaMapeador
    notas: str


def instrucao_do_estagio(config: Config, recurso: Recurso) -> str:
    """Instrução fixa da fase de exploração."""
    return carregar_prompt(
        ESTAGIO,
        {
            "recurso": recurso.nome,
            "caminho_backend": str(config.caminhos.backend),
            "caminho_graph": str(config.caminhos.graph_abs),
            "caminho_recurso": str(recurso.caminho_testes),
        },
        dir_prompts=config.caminhos.prompts,
    )


def instrucao_da_fatia(config: Config, recurso: Recurso, fatia: str) -> str:
    """Instrução fixa de uma fatia de serialização.

    Uma por fatia, estável entre tentativas: é a primeira parcela do prompt de
    reparo daquela fatia (princípio 2), e instrução que muda invalida o cache.
    """
    tipo = _TIPO_DA_FATIA.get(fatia, Inventario)
    return carregar_prompt(
        "mapeador-fatias",
        {
            "recurso": recurso.nome,
            "fatia": fatia,
            "schema_json": esquema_json(tipo),
        },
        dir_prompts=config.caminhos.prompts,
    )


def entrada_inicial(config: Config, recurso: Recurso) -> str:
    secoes = {
        "Recurso alvo": recurso.nome,
        "Backend": str(config.caminhos.backend),
        "Grafo estrutural": str(config.caminhos.graph_abs),
        "Diretório do recurso no projeto de testes": str(recurso.caminho_testes),
    }
    semente = _semente_estatica(config)
    if semente:
        secoes["Endpoints extraídos estaticamente do backend (ponto de partida verificado)"] = (
            semente
        )
    arvore = _arvore_de_fontes(config)
    if arvore:
        secoes["Árvore de fontes do backend (do grafo estrutural)"] = arvore
    return montar_entrada_inicial(secoes)


# Acima disto, a árvore vira resumo por diretório. O teto protege o prefixo, não o
# modelo: uma lista de milhares de caminhos custa em toda volta do ReAct, enquanto
# o resumo continua respondendo "onde ficam as coisas" com vinte linhas.
TETO_DE_ARQUIVOS_NA_ARVORE = 400


def _arvore_de_fontes(config: Config) -> str:
    """Os arquivos-fonte do backend, direto do `graph.json`, para a entrada.

    A fase mais cara da exploração medida não era ler arquivo — era DESCOBRIR que
    arquivo existe: até 18 chamadas de `listar_diretorio`, cada uma devolvendo
    poucas dezenas de caracteres e custando uma volta inteira de histórico
    reenviado. A lista completa já mora no grafo que o Bloco 0 validou; entregá-la
    na entrada eliminou quase todas as listagens (medido: 18 → 1-4, com -37% a
    -47% de tokens de entrada no estágio).

    É informação, nunca restrição: a tool de listagem continua disponível e o
    modelo continua obrigado a ler a fonte. Backend acima do teto vira resumo por
    diretório — informação a menos, nunca errada. Falha na leitura do grafo vira
    árvore vazia, pelo mesmo motivo da semente: o mapeador sabe explorar sem ela.
    """
    try:
        arquivos = arquivos_do_grafo(config.caminhos.graph_abs)
    except (GrafoNaoPreparado, OSError):
        return ""
    if not arquivos:
        return ""

    cabecalho = (
        "Todos os arquivos-fonte que o grafo conhece, relativos à raiz do backend. "
        "Use-a para navegar direto ao arquivo: não gaste voltas listando diretórios "
        "um a um."
    )
    if len(arquivos) <= TETO_DE_ARQUIVOS_NA_ARVORE:
        return cabecalho + "\n\n" + "\n".join(f"- {caminho}" for caminho in arquivos)

    por_diretorio: dict[str, int] = {}
    for caminho in arquivos:
        diretorio = caminho.rpartition("/")[0] or "."
        por_diretorio[diretorio] = por_diretorio.get(diretorio, 0) + 1
    resumo = "\n".join(
        f"- {diretorio}/ ({quantidade} arquivo(s))"
        for diretorio, quantidade in sorted(por_diretorio.items())
    )
    return (
        cabecalho + f"\n\nO backend tem {len(arquivos)} arquivos; a árvore está resumida por "
        "diretório. Use `listar_diretorio` para detalhar os que interessarem.\n\n" + resumo
    )


def _semente_estatica(config: Config) -> str:
    """Os endpoints que a análise estática já conhece, prontos para a entrada.

    O Gate A cruza o manifesto com `extrator_de_endpoints` DEPOIS do modelo
    trabalhar — o orquestrador sempre soube o universo de endpoints e só o usava
    para reprovar. Entregar a mesma lista na entrada corta a fase de descoberta
    do ReAct (medido: -33% a -76% de tokens de entrada no estágio) sem afrouxar
    nada: o denominador do gate continua sendo a extração, e as decisões que só
    o modelo toma (categorias, campos, schemas) continuam exigindo ler a fonte.

    O formato de cada linha é o MESMO que a seção "Endpoints do recurso" das
    notas exige: confirmar um endpoint é copiar a linha, e `dominio/notas.py`
    parseia de volta o que este render escreveu.

    Falha na extração vira semente vazia, nunca erro: o mapeador sabe explorar
    sem ela, e é o Bloco 0/Gate A quem responde por grafo ausente ou defasado.
    A ordem é fixa (classes por nome) porque a entrada da tentativa é prefixo de
    cache e diff de log — texto que muda de ordem entre execuções custa nos dois.
    """
    try:
        backend_lido = extrair(graph=config.caminhos.graph_abs, backend=config.caminhos.backend)
    except (GrafoNaoPreparado, CaminhoForaDaRaiz, OSError):
        return ""
    if not backend_lido.endpoints:
        return ""

    linhas = [
        "A lista abaixo foi extraída deterministicamente do código-fonte, com evidência "
        "de arquivo e linha. Use-a como ponto de partida do inventário: NÃO gaste voltas "
        "redescobrindo rotas — confirme na fonte apenas o que precisar para as decisões "
        "de categoria, campos e schemas (entidades, DTOs, validações, migrações), e "
        "registre nas notas qualquer rota que encontrar além destas. O escopo do seu "
        "recurso é o que você declarar nas notas; as outras classes estão listadas para "
        "você reconhecer dependências e vizinhança.",
        "",
    ]
    for classe in sorted(backend_lido.classes, key=lambda c: c.classe):
        if not classe.endpoints and not classe.nao_resolvidas:
            continue
        linhas.append(f"- {classe.classe} ({classe.arquivo}):")
        for endpoint in classe.endpoints:
            linhas.append(
                f"    - {endpoint.canonico} | handler {endpoint.handler} | "
                f"{endpoint.arquivo}:{endpoint.linha}"
            )
        for rota in classe.nao_resolvidas:
            linhas.append(f"    (não resolvida) {rota.expressao} | {rota.motivo}")
    return "\n".join(linhas)


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
    notas_anteriores: str | None = None,
    saida_anterior: SaidaMapeador | None = None,
    modelo_da_fatia: Callable[[str], BaseChatModel] | None = None,
) -> MapeamentoProduzido:
    """Uma tentativa do mapeador para um recurso: explorar e serializar.

    Com `delta` de gate e as notas da tentativa anterior, a exploração é
    reaproveitada e só as fatias donas das violações são reemitidas — a fórmula
    do princípio 2 vale por fatia: `instrução_da_fatia + [notas + fatia_atual] +
    violações`, e as notas contam como artefato porque estão em disco.

    `modelo_da_fatia` existe para o dry-run dar a cada fatia o próprio roteiro;
    na execução real as fatias usam o mesmo modelo da exploração.
    """
    politica = PoliticaDeRetentativa.do_config(config)

    if delta is None or notas_anteriores is None:
        # Tentativa nova — ou um reparo que chegou sem as notas (retomada fria):
        # sem a memória da exploração não há o que serializar, então explora-se de
        # novo. Nunca acontece no fluxo do pipeline, que guarda as notas.
        notas = _explorar(
            config,
            recurso,
            modelo=modelo,
            telemetria=telemetria,
            registro=registro,
            tentativa=tentativa,
            politica=politica,
            delta=delta,
            artefato_atual=artefato_atual,
        )
        alvo = _TODAS_AS_FATIAS
        base = None
    else:
        notas = notas_anteriores
        alvo = _fatias_do_delta(delta)
        base = saida_anterior

    saida = _serializar(
        config,
        recurso,
        notas=notas,
        fatias_alvo=alvo,
        base=base,
        delta=delta,
        modelo=modelo,
        modelo_da_fatia=modelo_da_fatia,
        telemetria=telemetria,
        registro=registro,
        tentativa=tentativa,
        politica=politica,
    )
    return MapeamentoProduzido(saida=saida, notas=notas)


# ---------------------------------------------------------------------------
# Fase 1 — explorar
# ---------------------------------------------------------------------------


def _explorar(
    config: Config,
    recurso: Recurso,
    *,
    modelo: BaseChatModel,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None,
    tentativa: int,
    politica: PoliticaDeRetentativa,
    delta: Delta | None = None,
    artefato_atual: str | None = None,
) -> str:
    """O ReAct com tools, terminando em notas de descoberta.

    Notas são texto: não há parse nem validação Pydantic aqui, e portanto não há
    reparo de schema na resposta grande — o modo de falha que custava 7,5 minutos
    ficou estruturalmente impossível nesta fase. O que se exige é o mínimo
    conferível: resposta não vazia e não truncada.
    """
    parametros = config.estagio(ESTAGIO)
    instrucao = instrucao_do_estagio(config, recurso)
    ferramentas = criar_ferramentas(
        config,
        estagio=ESTAGIO,
        telemetria=telemetria,
        recurso=recurso.nome,
        tentativa=tentativa,
    )
    agente = criar_agente(modelo, ferramentas, instrucao)

    entrada = entrada_inicial(config, recurso)
    if delta is not None:
        # Reparo frio (sem notas anteriores): o agente re-explora já sabendo o que
        # a tentativa anterior errou, com o artefato dela à vista quando existir.
        entrada = montar_entrada_reparo(artefato_atual or entrada, delta)

    for passo in range(1, parametros.max_tentativas_schema + 1):
        observador = ObservadorDeProvedor(
            telemetria=telemetria,
            registro=registro,
            estagio=ESTAGIO,
            recurso=recurso.nome,
            tentativa=tentativa,
            modelo=parametros.modelo,
            fatia="exploracao",
            detalhe=f"notas:{passo}; mensagens:por_request",
            simulado=getattr(modelo, "simulado", False),
            antes_de_chamar=lambda: exigir_folga(
                config, telemetria, estagio=ESTAGIO, recurso=recurso.nome
            ),
        )

        def invocar(
            entrada: str = entrada, observador: ObservadorDeProvedor = observador
        ) -> EstadoDoReAct:
            return agente.invoke(
                {"messages": [HumanMessage(content=entrada)]},
                config={
                    "recursion_limit": parametros.limite_passos,
                    "callbacks": [observador],
                },
            )

        def perdeu_a_volta(
            volta: TentativaDeProvedor, passo: int = passo, entrada: str = entrada
        ) -> None:
            """Volta perdida por indisponibilidade também custou tempo e dinheiro.

            Aqui a perda é maior que nas fatias: o que se joga fora é a exploração
            inteira do ReAct, com todas as respostas de tool já pagas.
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
                    detalhe=descrever_volta(volta, politica, prefixo=f"notas:{passo}"),
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
        resposta = mensagens[-1] if mensagens else None
        exigir_resposta_inteira(resposta, estagio=ESTAGIO, recurso=recurso.nome)
        if acabaram_os_passos(mensagens):
            raise _sem_passos(parametros.limite_passos, recurso.nome, SENTINELA_SEM_PASSOS)

        notas = texto_da_mensagem(resposta)
        if notas.strip():
            return notas
        # Resposta vazia é raríssima e não tem delta a montar: a exploração é
        # re-executada do zero, dentro do mesmo orçamento de tentativas de schema.

    raise FalhaDeEstagio(
        f"o mapeador terminou a exploração sem notas para o recurso {recurso.nome!r} "
        f"em {parametros.max_tentativas_schema} tentativa(s)."
    )


# ---------------------------------------------------------------------------
# Fase 2 — serializar
# ---------------------------------------------------------------------------


def _serializar(
    config: Config,
    recurso: Recurso,
    *,
    notas: str,
    fatias_alvo: frozenset[str],
    base: SaidaMapeador | None,
    delta: Delta | None,
    modelo: BaseChatModel,
    modelo_da_fatia: Callable[[str], BaseChatModel] | None,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None,
    tentativa: int,
    politica: PoliticaDeRetentativa,
) -> SaidaMapeador:
    parametros = config.estagio(ESTAGIO)

    def mesmo_modelo(_fatia: str) -> BaseChatModel:
        return modelo

    fabrica: Callable[[str], BaseChatModel] = modelo_da_fatia or mesmo_modelo

    def gerador(fatia: str) -> GeradorEstruturado:
        return GeradorEstruturado(
            modelo=fabrica(fatia),
            estagio=ESTAGIO,
            parametros=parametros,
            telemetria=telemetria,
            registro=registro,
            politica=politica,
            antes_de_chamar=lambda: exigir_folga(
                config, telemetria, estagio=ESTAGIO, recurso=recurso.nome
            ),
        )

    def gerar(fatia: str) -> Manifesto | DossieDoRecurso | FatiaDeSchemas:
        atual = _fatia_atual(base, fatia)
        if delta is not None and atual is not None:
            recorte = _delta_da_fatia(delta, fatia)
            entrada = montar_entrada_reparo(_artefato_da_fatia(notas, atual), recorte)
        else:
            entrada = montar_entrada_inicial(
                {
                    "Recurso alvo": recurso.nome,
                    "Notas de descoberta da exploração": notas,
                }
            )
        return gerador(fatia).gerar(
            _TIPO_DA_FATIA[fatia],
            instrucao=instrucao_da_fatia(config, recurso, fatia),
            entrada=entrada,
            recurso=recurso.nome,
            tentativa=tentativa,
            fatia=f"fatia:{fatia}",
        )

    # Schemas por código, como o inventário: o contrato das notas manda colar o
    # conteúdo JSON completo, e pagar o modelo para redigitá-lo era desperdício
    # medido (9,5k tokens de raciocínio numa amostra, transcrevendo dois arquivos
    # já escritos). Só no caminho sem delta: reparo de gate sobre schemas
    # significa que o que estava nas notas não bastou, e aí é o modelo que
    # trabalha, com as violações à vista.
    schemas_de_codigo: FatiaDeSchemas | None = None
    if delta is None:
        colados = schemas_das_notas(notas)
        if colados is not None:
            try:
                schemas_de_codigo = FatiaDeSchemas(schemas=colados)
            except ValidationError:
                schemas_de_codigo = None

    pendentes = [
        fatia
        for fatia in FATIAS_DE_MODELO
        if (base is None or fatia in fatias_alvo)
        and not (fatia == "schemas" and schemas_de_codigo is not None)
    ]
    # Mesmo arranjo do planejador: tarefas independentes por construção, cópia de
    # contexto POR TAREFA (thread nova nasce com contexto vazio e o span do
    # recurso não atravessaria), resultados na ordem das tarefas.
    if parametros.paralelismo > 1 and len(pendentes) > 1:

        def gerar_no_contexto(
            par: tuple[contextvars.Context, str],
        ) -> Manifesto | DossieDoRecurso | FatiaDeSchemas:
            contexto, fatia = par
            return contexto.run(gerar, fatia)

        pares = [(contextvars.copy_context(), fatia) for fatia in pendentes]
        with ThreadPoolExecutor(max_workers=parametros.paralelismo) as fila:
            resultados = dict(zip(pendentes, fila.map(gerar_no_contexto, pares), strict=True))
    else:
        resultados = {fatia: gerar(fatia) for fatia in pendentes}

    manifesto = resultados.get("manifesto") or (base.manifesto if base else None)
    dossie = resultados.get("dossie") or (base.dossie if base else None)
    fatia_schemas = resultados.get("schemas") or schemas_de_codigo
    schemas = (
        fatia_schemas.schemas
        if isinstance(fatia_schemas, FatiaDeSchemas)
        else (base.schemas if base else [])
    )
    if not isinstance(manifesto, Manifesto):
        raise FalhaDeEstagio(
            f"a serialização do mapeador não produziu manifesto para {recurso.nome!r}"
        )

    inventario = _inventario(config, recurso, notas, gerador, tentativa=tentativa)

    def remontar(correcoes: dict[str, object]) -> SaidaMapeador:
        return SaidaMapeador(
            inventario=inventario,
            manifesto=correcoes.get("manifesto") or manifesto,  # type: ignore[arg-type]
            schemas=(
                fatia.schemas
                if isinstance(fatia := correcoes.get("schemas"), FatiaDeSchemas)
                else schemas
            ),
            dossie=correcoes.get("dossie") or dossie,  # type: ignore[arg-type]
        )

    try:
        saida = remontar({})
    except ValidationError as erro:
        # A validação cruzada reprovou a montagem — schemaEntrada sem arquivo,
        # recurso divergente. É defeito de coerência entre fatias: uma volta de
        # reparo nas duas fatias do par cruzado, e só uma — coerência que não
        # fecha com as violações à vista é falha do estágio.
        violacoes = [
            Violacao(codigo="QAORQ-010", mensagem=str(item.get("msg", "montagem inválida")))
            for item in erro.errors()
        ]
        recorte = Delta(
            estagio="schema", recurso=recurso.nome, violacoes=violacoes, tentativa=tentativa
        )
        correcoes: dict[str, object] = {}
        for fatia in ("manifesto", "schemas"):
            atual = {"manifesto": manifesto, "schemas": FatiaDeSchemas(schemas=schemas)}[fatia]
            correcoes[fatia] = gerador(fatia).gerar(
                _TIPO_DA_FATIA[fatia],
                instrucao=instrucao_da_fatia(config, recurso, fatia),
                entrada=montar_entrada_reparo(_artefato_da_fatia(notas, atual), recorte),
                recurso=recurso.nome,
                tentativa=tentativa,
                fatia=f"remontagem:{fatia}",
            )
        try:
            saida = remontar(correcoes)
        except ValidationError as segunda:
            raise FalhaDeEstagio(
                f"as fatias do mapeador não montaram um SaidaMapeador coerente para "
                f"{recurso.nome!r} mesmo após reparo: {segunda.errors()[0].get('msg', segunda)}"
            ) from segunda

    _exigir_fidelidade(saida, notas, registro)
    return saida


def _inventario(
    config: Config,
    recurso: Recurso,
    notas: str,
    gerador: Callable[[str], GeradorEstruturado],
    *,
    tentativa: int,
) -> Inventario:
    """O inventário: código quando as notas têm a seção combinada, modelo se não.

    O caminho de código é o normal — as linhas da seção são as da semente
    estática, e pagar o modelo para copiá-las de volta era parte do custo medido.
    O fallback existe para backend fora da matriz estática e para notas que
    fugiram da forma: gerar por modelo é mais caro e menos confiável, nunca
    impossível.
    """
    endpoints = endpoints_das_notas(notas)
    if endpoints:
        try:
            return Inventario(
                recurso=recurso.nome,
                endpoints=endpoints,
                rotas_dinamicas_nao_resolvidas=rotas_dinamicas_das_notas(notas),
            )
        except ValidationError:
            pass
    return gerador("inventario").gerar(
        Inventario,
        instrucao=instrucao_da_fatia(config, recurso, "inventario"),
        entrada=montar_entrada_inicial(
            {
                "Recurso alvo": recurso.nome,
                "Notas de descoberta da exploração": notas,
            }
        ),
        recurso=recurso.nome,
        tentativa=tentativa,
        fatia="fatia:inventario",
    )


def _exigir_fidelidade(
    saida: SaidaMapeador, notas: str, registro: RegistradorDeEventos | None
) -> None:
    """Endpoint no manifesto que as notas nem citam é invenção da serialização.

    Aviso, não reprovação: quem tem autoridade para reprovar endpoint é o diff
    do Gate A, contra a extração estática — esta checagem só existe para o log
    apontar a fatia culpada quando o gate reprovar em seguida.
    """
    if registro is None:
        return
    ausentes = endpoints_nao_citados(notas, [item.endpoint for item in saida.manifesto.endpoints])
    for endpoint in ausentes:
        registro.aviso(
            f"manifesto declara {endpoint} que as notas de descoberta não citam — "
            "a serialização pode ter inventado o endpoint (o diff do Gate A decide)"
        )


def _fatia_atual(
    base: SaidaMapeador | None, fatia: str
) -> Manifesto | DossieDoRecurso | FatiaDeSchemas | None:
    if base is None:
        return None
    if fatia == "manifesto":
        return base.manifesto
    if fatia == "dossie":
        return base.dossie
    return FatiaDeSchemas(schemas=base.schemas)


def _artefato_da_fatia(
    notas: str, atual: Manifesto | DossieDoRecurso | FatiaDeSchemas | None
) -> str:
    """O "artefato atual" de um reparo de fatia: as notas e a fatia como está.

    As notas entram porque são artefato em disco (princípio 1) e porque são o
    material do reparo: sem elas o modelo consertaria a forma re-inventando o
    conteúdo. Nada de histórico, de outras fatias nem de tentativa anterior.
    """
    corpo = (
        atual.model_dump_json(by_alias=True, exclude_none=True, indent=1) if atual else "(ausente)"
    )
    return f"--- notas-de-descoberta.md ---\n{notas.rstrip()}\n\n--- fatia atual ---\n{corpo}"


def _fatias_do_delta(delta: Delta) -> frozenset[str]:
    alvo: set[str] = set()
    for violacao in delta.violacoes:
        alvo |= _FATIAS_DO_CODIGO.get(violacao.codigo, _TODAS_AS_FATIAS)
    return frozenset(alvo) or _TODAS_AS_FATIAS


def _delta_da_fatia(delta: Delta, fatia: str) -> Delta:
    """Só as violações que pertencem à fatia — o resto é ruído para ela."""
    proprias = [
        violacao
        for violacao in delta.violacoes
        if fatia in _FATIAS_DO_CODIGO.get(violacao.codigo, _TODAS_AS_FATIAS)
    ]
    return Delta(
        estagio=delta.estagio,
        recurso=delta.recurso,
        violacoes=proprias or delta.violacoes,
        tentativa=delta.tentativa,
    )


# ---------------------------------------------------------------------------
# Apoio ao pipeline
# ---------------------------------------------------------------------------


def artefato_em_disco(
    *,
    manifesto: Path,
    inventario: Path,
    dir_schemas: Path,
    recurso: str,
    dossie: Path | None = None,
    notas: Path | None = None,
) -> str:
    """O artefato **inteiro** do Bloco 1, lido do staging, para diagnóstico e log.

    O reparo fatiado não reenvia este bundle ao modelo — cada fatia recebe as
    notas e a si mesma —, mas o ciclo de reparo continua registrando o artefato
    da tentativa, e é daqui que ele sai. A ordem das seções é fixa e os schemas
    saem ordenados por caminho: bundle que muda de ordem entre tentativas faz
    diff de log parecer mudança de conteúdo.
    """
    partes: list[str] = []
    if notas is not None:
        partes.append(_secao("notas-de-descoberta.md", notas))
    partes += [
        _secao("inventario.json", inventario),
        _secao("_support/cobertura.json", manifesto),
    ]
    if dossie is not None:
        partes.append(_secao("dossie.json", dossie))
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
