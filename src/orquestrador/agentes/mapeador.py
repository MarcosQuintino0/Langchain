"""Bloco 1 — agente mapeador (LLM com tools).

Agente ReAct que explora o backend incrementalmente e emite dois artefatos:
`Inventario` e `Manifesto`. Processa **um recurso por vez** e zera o histórico
entre recursos (princípio 3) — cada tentativa é uma invocação nova, com lista de
mensagens nova.

O que muda este módulo é **a unidade de trabalho do estágio**: o que entra numa
tentativa, o que sai dela, e o que acontece quando a saída não valida contra o
contrato. As tools que o modelo usa moram em `ferramentas_do_mapeador.py` e o
acoplamento com o LangGraph, em `grafo_react.py` — os três mudam por razões
diferentes, e é por isso que são três arquivos.
"""

from __future__ import annotations

import time
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
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
from orquestrador.dominio.artefatos import SUFIXO_SCHEMA, SaidaMapeador
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import FalhaDeEstagio, GrafoNaoPreparado
from orquestrador.ferramentas.arquivos import CaminhoForaDaRaiz
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    TentativaDeProvedor,
    chamar_com_retentativas,
    descrever_volta,
)
from orquestrador.llm.estruturado import exigir_resposta_inteira, violacoes_de_validacao
from orquestrador.llm.mensagens import texto_da_mensagem, uso_da_mensagem, uso_das_mensagens
from orquestrador.llm.montagem import (
    LIMITE_PADRAO,
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
    montar_entrada_reparo_de_schema,
)
from orquestrador.observabilidade.medidas import RegistroDeChamada, UsoDeTokens
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

ESTAGIO = "mapeador"


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
        "registre em `rotas_dinamicas_nao_resolvidas` qualquer rota que encontrar além "
        "destas. O escopo do seu recurso é o do inventário; as outras classes estão "
        "listadas para você reconhecer dependências e vizinhança.",
        "",
    ]
    for classe in sorted(backend_lido.classes, key=lambda c: c.classe):
        if not classe.endpoints and not classe.nao_resolvidas:
            continue
        linhas.append(f"- {classe.classe} ({classe.arquivo}):")
        for endpoint in classe.endpoints:
            linhas.append(
                f"    {endpoint.canonico} | handler {endpoint.handler} | "
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
) -> SaidaMapeador:
    """Uma tentativa do mapeador para um recurso.

    Com `delta`, a entrada é apenas `instrucao_fixa + artefato_atual + violacoes`
    (princípio 2). O histórico das tentativas anteriores nunca é reenviado.
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

    if delta is not None:
        entrada = montar_entrada_reparo(artefato_atual or "(artefato ausente)", delta)
    else:
        entrada = entrada_inicial(config, recurso)

    # Guardada antes do laço: é a tarefa, e o reparo de schema tem de mandá-la de
    # volta. Sem isso a volta seguinte recebia só o fragmento malformado.
    entrada_da_tentativa = entrada
    ultimo_texto = ""
    ultimas_violacoes: list[Violacao] = []

    politica = PoliticaDeRetentativa.do_config(config)

    for passo in range(1, parametros.max_tentativas_schema + 1):
        # Dentro do laço, e não antes dele: o mini-loop de schema dá várias voltas
        # de modelo por tentativa, e a primeira delas é a exploração inteira do ReAct.
        exigir_folga(config, telemetria, estagio=ESTAGIO, recurso=recurso.nome)
        inicio = time.perf_counter()

        def invocar(entrada: str = entrada) -> EstadoDoReAct:
            return agente.invoke(
                {"messages": [HumanMessage(content=entrada)]},
                config={"recursion_limit": parametros.limite_passos},
            )

        def invocar_direto(entrada: str = entrada) -> BaseMessage:
            return modelo.invoke([SystemMessage(content=instrucao), HumanMessage(content=entrada)])

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

        if passo == 1:
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
            resposta = mensagens[-1] if mensagens else None
            detalhe = f"schema:{passo}; mensagens:{len(mensagens)}"
        else:
            # Reparo de schema é defeito de FORMA — a saída não validou contra o
            # contrato. Não há nada a descobrir no backend, então o reparo NÃO passa
            # pelo agente com tools: reenviar a tarefa a ele fazia o modelo re-explorar
            # obedientemente (medido: 16-20 tool calls e 150-230 mil tokens por reparo,
            # às vezes sem convergir). A chamada direta com instrução + artefato inteiro
            # + violações converge numa volta de ~10 mil tokens e devolve o artefato
            # idêntico fora dos pontos reclamados.
            resposta = chamar_com_retentativas(
                invocar_direto,
                politica=politica,
                estagio=ESTAGIO,
                recurso=recurso.nome,
                ao_falhar=perdeu_a_volta,
            )
            mensagens = [resposta]
            uso = uso_da_mensagem(resposta)
            detalhe = f"schema:{passo}; reparo-direto"

        telemetria.registrar(
            RegistroDeChamada(
                estagio=ESTAGIO,
                recurso=recurso.nome,
                tentativa=tentativa,
                modelo=parametros.modelo,
                uso=uso or UsoDeTokens(),
                duracao_s=time.perf_counter() - inicio,
                simulado=getattr(modelo, "simulado", False),
                detalhe=detalhe,
                caracteres_instrucao=len(instrucao),
                caracteres_entrada=len(entrada),
            )
        )

        # Depois de registrar a telemetria: os tokens desta volta foram gastos de
        # verdade e precisam aparecer no relatório, mesmo que ela termine em falha.
        exigir_resposta_inteira(resposta, estagio=ESTAGIO, recurso=recurso.nome)
        if passo == 1 and acabaram_os_passos(mensagens):
            # O caminho que realmente acontece no LangGraph 1.x (ver SENTINELA_SEM_PASSOS):
            # em vez de levantar, o agente encerra com uma mensagem de desculpa. Sem
            # reconhecê-la aqui, o pipeline gastaria todas as tentativas de schema tentando
            # parsear essa frase como JSON e falharia com um motivo que esconde a causa.
            raise _sem_passos(parametros.limite_passos, recurso.nome, SENTINELA_SEM_PASSOS)

        ultimo_texto = texto_da_mensagem(resposta)
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
        # `limite=LIMITE_PADRAO`, e não o padrão de 2.000 do executor: a resposta do
        # mapeador tem dezenas de KB, e cortá-la a 2.000 entregava ao reparo ~5% do
        # próprio trabalho — ou ele re-explorava tudo, ou reconstruía errado e o
        # estágio inteiro era perdido. O artefato malformado É o material do reparo
        # aqui; o teto de 60 KB é o mesmo da projeção de artefato do reparo de gate.
        entrada = montar_entrada_reparo_de_schema(
            entrada_da_tentativa,
            ultimo_texto,
            Delta(
                estagio="schema",
                recurso=recurso.nome,
                violacoes=ultimas_violacoes,
                tentativa=passo,
            ),
            limite=LIMITE_PADRAO,
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
