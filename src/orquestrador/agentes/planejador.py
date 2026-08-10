"""Estágio planejador — expande o gabarito em cenários concretos, um endpoint por vez.

Fica entre o Gate A e o executor. O manifesto aprovado diz *que categorias* cada
endpoint testa; este estágio decide *quais casos* — e é aqui que mora o julgamento
que antes o executor tinha de improvisar no meio da escrita. Separar decidir de
escrever é o que deixa o pensamento do modelo saudável nos dois lados: medido, o
mesmo modelo que travava planejando-e-escrevendo tudo numa resposta (65.536 tokens
de raciocínio, saída vazia) planeja um endpoint em 2-6 mil tokens de raciocínio e
escreve um arquivo por vez sem chegar perto do teto.

**Uma chamada por endpoint, nunca o recurso inteiro numa resposta.** O tamanho da
resposta pedida é a variável que separa pensamento útil de espiral — não o tipo da
tarefa. É a mesma razão do fatiamento do executor, aplicada ao planejamento.

Sem tools: o planejador não lê o backend. Tudo que ele pode saber está no gabarito
e nos schemas que o mapeador emitiu — se falta informação aí, o defeito é do
mapeador, e dar tools ao planejador esconderia isso re-explorando.

A completude (toda categoria de `cats` tem cenário) é conferida por código puro em
`dominio/plano.py` e reparada no mini-loop do próprio estágio, na mesma moeda do
reparo de schema: `instrução + plano atual + violações` (QAORQ-050).
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from orquestrador.config import Config
from orquestrador.dominio.artefatos import ArquivoSchema
from orquestrador.dominio.manifesto import EndpointManifesto, Manifesto
from orquestrador.dominio.plano import PlanoDeTestes, PlanoDoEndpoint, cenarios_faltantes
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import ErroDeConfiguracao, FalhaDeEstagio
from orquestrador.llm.cliente import PoliticaDeRetentativa
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.llm.montagem import (
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
)
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

ESTAGIO = "planejador"


def instrucao_do_estagio(config: Config, recurso: Recurso) -> str:
    """Instrução fixa do estágio: prompt editorial + contrato de saída."""
    return carregar_prompt(
        ESTAGIO,
        {
            "recurso": recurso.nome,
            "schema_json": esquema_json(PlanoDoEndpoint),
        },
        dir_prompts=config.caminhos.prompts,
    )


def entrada_do_endpoint(item: EndpointManifesto, schemas: list[ArquivoSchema]) -> str:
    """A entrada de uma chamada: um endpoint do gabarito + os schemas do recurso.

    Os schemas vão inteiros (não só o declarado pelo endpoint) porque cenários de
    relacionamento e de segurança citam campos de outras entidades do recurso — e
    porque o conjunto é pequeno e constante entre as chamadas, o que mantém o
    prefixo do prompt estável para o cache.
    """
    secoes = {
        "Endpoint do gabarito": f"```json\n{item.model_dump_json(by_alias=True, indent=1)}\n```",
    }
    if schemas:
        corpo = "\n\n".join(f"--- {s.caminho} ---\n{s.conteudo.strip()}" for s in schemas)
        secoes["Schemas de entrada do recurso"] = corpo
    return montar_entrada_inicial(secoes)


def executar(
    config: Config,
    recurso: Recurso,
    manifesto: Manifesto,
    schemas: list[ArquivoSchema],
    *,
    modelo: BaseChatModel,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None = None,
    tentativa: int = 1,
) -> PlanoDeTestes:
    """O plano do recurso, montado endpoint a endpoint."""
    try:
        parametros = config.estagio(ESTAGIO)
    except ErroDeConfiguracao:
        # Config anterior a este estágio. Cair para os parâmetros do mapeador — e
        # não para os do executor — porque planejar é tarefa de julgamento, e é o
        # mapeador que a configuração manda equipar com o modelo mais capaz.
        # Interromper aqui quebraria toda config existente por uma seção nova.
        parametros = config.estagio("mapeador")
        if registro:
            registro.aviso(
                "[estagios.planejador] não configurado; usando os parâmetros de "
                "[estagios.mapeador]. Declare a seção para escolher modelo próprio."
            )
    instrucao = instrucao_do_estagio(config, recurso)
    gerador = GeradorEstruturado(
        modelo=modelo,
        estagio=ESTAGIO,
        parametros=parametros,
        telemetria=telemetria,
        registro=registro,
        politica=PoliticaDeRetentativa.do_config(config),
    )

    partes: list[PlanoDoEndpoint] = []
    for item in manifesto.endpoints:
        parte = gerador.gerar(
            PlanoDoEndpoint,
            instrucao=instrucao,
            entrada=entrada_do_endpoint(item, schemas),
            recurso=recurso.nome,
            tentativa=tentativa,
        )
        faltantes = cenarios_faltantes(parte, list(item.cats))
        if faltantes:
            # Mesma moeda do reparo de schema: o plano atual volta inteiro com as
            # violações. Uma volta só — plano que não fecha nem sabendo o que
            # falta é falha do estágio, não caso para insistir.
            delta = Delta(
                estagio="schema",
                recurso=recurso.nome,
                violacoes=[
                    Violacao(
                        codigo="QAORQ-050",
                        mensagem=(
                            f"{item.endpoint}: a categoria {cat} está em `cats` no "
                            "gabarito e não tem nenhum cenário no plano"
                        ),
                    )
                    for cat in faltantes
                ],
                tentativa=tentativa,
            )
            parte = gerador.gerar(
                PlanoDoEndpoint,
                instrucao=instrucao,
                entrada=montar_entrada_reparo(
                    parte.model_dump_json(by_alias=True, indent=1), delta
                ),
                recurso=recurso.nome,
                tentativa=tentativa,
            )
            faltantes = cenarios_faltantes(parte, list(item.cats))
            if faltantes:
                raise FalhaDeEstagio(
                    f"o planejador não cobriu as categorias {faltantes} do endpoint "
                    f"{item.endpoint!r} mesmo depois do reparo (QAORQ-050)."
                )
        if parte.endpoint != item.endpoint:
            # O modelo às vezes reescreve a grafia da rota; o plano é indexado pelo
            # endpoint do GABARITO, senão a fatia do executor não o encontra.
            parte = parte.model_copy(update={"endpoint": item.endpoint})
        partes.append(parte)

    return PlanoDeTestes(recurso=manifesto.recurso, endpoints=partes)
