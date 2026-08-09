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
from orquestrador.config import Config
from orquestrador.dominio.artefatos import SUFIXO_SCHEMA, SaidaMapeador
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import FalhaDeEstagio
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    TentativaDeProvedor,
    chamar_com_retentativas,
    descrever_volta,
)
from orquestrador.llm.estruturado import exigir_resposta_inteira, violacoes_de_validacao
from orquestrador.llm.mensagens import texto_da_mensagem, uso_das_mensagens
from orquestrador.llm.montagem import (
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
        # de modelo por tentativa, e cada uma reenvia a exploração inteira do ReAct.
        exigir_folga(config, telemetria, estagio=ESTAGIO, recurso=recurso.nome)
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
        exigir_resposta_inteira(
            mensagens[-1] if mensagens else None, estagio=ESTAGIO, recurso=recurso.nome
        )
        if acabaram_os_passos(mensagens):
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
        entrada = montar_entrada_reparo_de_schema(
            entrada_da_tentativa,
            ultimo_texto,
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
