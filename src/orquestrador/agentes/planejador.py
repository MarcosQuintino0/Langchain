"""Estágio planejador — expande o gabarito em cenários concretos, um endpoint por vez.

Fica entre o Gate A e o executor. O manifesto aprovado diz *que categorias* cada
endpoint testa; este estágio decide *quais casos* — e é aqui que mora o julgamento
que antes o executor tinha de improvisar no meio da escrita. Separar decidir de
escrever é o que deixa o pensamento do modelo saudável nos dois lados: medido, o
mesmo modelo que travava planejando-e-escrevendo tudo numa resposta (65.536 tokens
de raciocínio, saída vazia) planeja um endpoint em 2-6 mil tokens de raciocínio e
escreve um arquivo por vez sem chegar perto do teto.

**Uma chamada por endpoint × grupo de categorias, nunca o recurso inteiro numa
resposta.** O tamanho da resposta pedida é a variável que separa pensamento útil
de espiral — não o tipo da tarefa. É a mesma razão do fatiamento do executor,
aplicada ao planejamento, e a partição é a MESMA (`GRUPOS_DE_CATS` em
`dominio/plano.py`): medido em 2026-08-10, o endpoint de listagem inteiro numa
chamada estourava o teto de saída do provedor com a entrada enriquecida pelo
dossiê; por grupo, cada resposta volta à faixa saudável.

Sem tools: o planejador não lê o backend. Tudo que ele pode saber está no gabarito
e nos schemas que o mapeador emitiu — se falta informação aí, o defeito é do
mapeador, e dar tools ao planejador esconderia isso re-explorando.

A completude (toda categoria de `cats` tem cenário) é conferida por código puro em
`dominio/plano.py` e reparada no mini-loop do próprio estágio, na mesma moeda do
reparo de schema: `instrução + plano atual + violações` (QAORQ-050).
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

from langchain_core.language_models import BaseChatModel

from orquestrador.agentes.guarda_de_orcamento import exigir_folga
from orquestrador.config import Config
from orquestrador.dominio.artefatos import ArquivoSchema
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.manifesto import EndpointManifesto, Manifesto
from orquestrador.dominio.plano import (
    GRUPOS_DE_CATS,
    Cenario,
    PlanoDeTestes,
    PlanoDoEndpoint,
    cenarios_faltantes,
    cenarios_sem_prova_de_estado,
    variacoes_excedentes,
)
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import ErroDeConfiguracao, FalhaDeEstagio, RespostaTruncada
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


def _oraculo_do_grupo(oraculo: str, cats: list[str]) -> str:
    """As linhas da tabela-oráculo das categorias pedidas, com o cabeçalho.

    A seleção é pelo prefixo ``| `CAT-`` — formato que `planejador-oraculo.md`
    declara como contrato. Enviar só as linhas do grupo é o que tirou a tabela
    inteira da instrução fixa: menos ~1k tokens por chamada e menos restrições
    simultâneas, que é o gatilho medido das espirais.
    """
    linhas = [linha for linha in oraculo.splitlines() if linha.startswith("|")]
    pedidas = tuple(f"| `{cat}`" for cat in cats)
    return "\n".join(linhas[:2] + [linha for linha in linhas[2:] if linha.startswith(pedidas)])


def entrada_do_endpoint(
    item: EndpointManifesto,
    schemas: list[ArquivoSchema],
    dossie: DossieDoRecurso | None = None,
    cats_da_chamada: list[str] | None = None,
    oraculo_do_grupo: str | None = None,
) -> str:
    """A entrada de uma chamada: um endpoint do gabarito + os schemas do recurso.

    Os schemas vão inteiros (não só o declarado pelo endpoint) porque cenários de
    relacionamento e de segurança citam campos de outras entidades do recurso — e
    porque o conjunto é pequeno e constante entre as chamadas, o que mantém o
    prefixo do prompt estável para o cache.

    O dossiê, ao contrário, vai **fatiado por endpoint**: as regras transversais se
    repetem entre as chamadas (prefixo estável), e as específicas de outros
    endpoints seriam só custo — a fatia é o que paga o dossiê sem reenviá-lo N
    vezes.
    """
    secoes = {
        # `exclude nao_aplica`: a justificativa de por que uma categoria NÃO será
        # testada não participa de nenhuma decisão daqui — o planejador só planeja
        # `cats` —, e eram 1-2k caracteres repetidos em cada chamada.
        "Endpoint do gabarito": (
            "```json\n"
            + item.model_dump_json(by_alias=True, indent=1, exclude={"nao_aplica"})
            + "\n```"
        ),
    }
    if cats_da_chamada:
        secoes["Categorias desta chamada"] = (
            "Planeje cenários SOMENTE para: "
            + ", ".join(cats_da_chamada)
            + ". As demais categorias do gabarito serão planejadas em chamadas "
            "próprias — não as inclua nesta resposta."
        )
    if oraculo_do_grupo:
        secoes["Oráculo das categorias desta chamada"] = oraculo_do_grupo
    if schemas:
        corpo = "\n\n".join(f"--- {s.caminho} ---\n{s.conteudo.strip()}" for s in schemas)
        secoes["Schemas de entrada do recurso"] = corpo
    if dossie is not None:
        fatia = dossie.render_para_endpoint(item.endpoint)
        if fatia.strip():
            secoes["Dossiê do recurso — o que a leitura do backend estabeleceu"] = fatia
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
    dossie: DossieDoRecurso | None = None,
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
        antes_de_chamar=lambda: exigir_folga(
            config, telemetria, estagio=ESTAGIO, recurso=recurso.nome
        ),
    )

    oraculo = carregar_prompt("planejador-oraculo", dir_prompts=config.caminhos.prompts)
    tarefas: list[tuple[EndpointManifesto, str, list[str]]] = []
    for item in manifesto.endpoints:
        for chave, grupo in GRUPOS_DE_CATS:
            cats_do_grupo = [cat for cat in item.cats if cat in grupo]
            if cats_do_grupo:
                tarefas.append((item, chave, cats_do_grupo))

    def planejar(tarefa: tuple[EndpointManifesto, str, list[str]]) -> list[Cenario]:
        item, chave, cats_do_grupo = tarefa
        return _planejar_grupo(
            gerador,
            instrucao,
            recurso,
            item,
            schemas,
            dossie,
            cats_do_grupo,
            chave=chave,
            tentativa=tentativa,
            registro=registro,
            oraculo=_oraculo_do_grupo(oraculo, cats_do_grupo),
        )

    # As tarefas são independentes por construção (nenhuma lê a saída de outra;
    # princípio 3), então paralelizar muda só o relógio: mesmos tokens, mesmas
    # entradas, montagem na MESMA ordem canônica — `map` preserva a ordem das
    # tarefas, nunca a de chegada. O limite vem da config porque o gargalo é o
    # provedor (429), não a arquitetura.
    if parametros.paralelismo > 1 and len(tarefas) > 1:
        # Uma cópia do contexto POR TAREFA, tirada aqui na thread que chama:
        # thread nova nasce com contexto vazio, e sem isto o span do recurso não
        # atravessa — medido, as chamadas deste estágio saíam órfãs na raiz do
        # trace. A cópia é por tarefa porque um mesmo `Context` não pode ser
        # entrado por duas threads ao mesmo tempo.
        def planejar_no_contexto(
            par: tuple[contextvars.Context, tuple[EndpointManifesto, str, list[str]]],
        ) -> list[Cenario]:
            contexto, tarefa = par
            return contexto.run(planejar, tarefa)

        pares = [(contextvars.copy_context(), tarefa) for tarefa in tarefas]
        with ThreadPoolExecutor(max_workers=parametros.paralelismo) as fila:
            resultados = list(fila.map(planejar_no_contexto, pares))
    else:
        resultados = [planejar(tarefa) for tarefa in tarefas]

    # A montagem é indexada pelo endpoint do GABARITO (não pela grafia que o
    # modelo devolver), senão a fatia do executor não a encontra.
    por_endpoint: dict[str, list[Cenario]] = {}
    for (item, _, _), cenarios_do_grupo in zip(tarefas, resultados, strict=True):
        por_endpoint.setdefault(item.endpoint, []).extend(cenarios_do_grupo)
    partes = [
        PlanoDoEndpoint(endpoint=item.endpoint, cenarios=por_endpoint[item.endpoint])
        for item in manifesto.endpoints
    ]
    return PlanoDeTestes(recurso=manifesto.recurso, endpoints=partes)


def _gerar_tolerando_um_corte(
    gerador: GeradorEstruturado,
    registro: RegistradorDeEventos | None,
    **chamada: object,
) -> PlanoDoEndpoint:
    """Repete a chamada UMA vez quando o provedor corta a resposta pelo teto.

    Exceção deliberada e mínima ao veto de "repetição em resposta truncada",
    aprovada pelo usuário em 2026-08-10 e restrita a este estágio: a espiral de
    raciocínio da causa 2 virou risco estocástico (~1 chamada em 10 corre ao teto
    com 100% de pensamento, independentemente do conteúdo — a mesma entrada
    completa em 2-11k na maioria das amostras). Uma repetição é um dado novo, não
    um loop: o segundo corte seguido sobe como a falha operacional que sempre foi.
    """
    try:
        return gerador.gerar(PlanoDoEndpoint, **chamada)  # type: ignore[arg-type]
    except RespostaTruncada as erro:
        if registro:
            registro.aviso(
                f"resposta cortada pelo provedor em {chamada.get('fatia')}; "
                f"repetindo a chamada uma única vez. Detalhe: {str(erro).splitlines()[0]}"
            )
        return gerador.gerar(PlanoDoEndpoint, **chamada)  # type: ignore[arg-type]


def _planejar_grupo(
    gerador: GeradorEstruturado,
    instrucao: str,
    recurso: Recurso,
    item: EndpointManifesto,
    schemas: list[ArquivoSchema],
    dossie: DossieDoRecurso | None,
    cats_do_grupo: list[str],
    *,
    chave: str,
    tentativa: int,
    registro: RegistradorDeEventos | None = None,
    oraculo: str | None = None,
) -> list[Cenario]:
    """Uma chamada do planejador: um endpoint, um grupo de categorias.

    Cenário devolvido com categoria de OUTRO grupo é descartado, como no filtro
    de fatia do executor: a categoria pertence à chamada dona dela, e mantê-lo
    aqui duplicaria o caso quando a chamada dona o planejasse de novo.
    """
    parte = _gerar_tolerando_um_corte(
        gerador,
        registro,
        instrucao=instrucao,
        entrada=entrada_do_endpoint(item, schemas, dossie, cats_do_grupo, oraculo),
        recurso=recurso.nome,
        tentativa=tentativa,
        endpoint=item.endpoint,
        fatia=f"plano:{chave}",
    )
    violacoes = _violacoes_do_grupo(parte, cats_do_grupo, item.endpoint)
    if violacoes:
        # Mesma moeda do reparo de schema: o plano atual volta inteiro com as
        # violações. Uma volta só — plano que não fecha nem sabendo o que
        # falta é falha do estágio, não caso para insistir.
        delta = Delta(
            estagio="schema",
            recurso=recurso.nome,
            violacoes=violacoes,
            tentativa=tentativa,
        )
        reparada = _gerar_tolerando_um_corte(
            gerador,
            registro,
            instrucao=instrucao,
            entrada=montar_entrada_reparo(parte.model_dump_json(by_alias=True, indent=1), delta),
            recurso=recurso.nome,
            tentativa=tentativa,
            endpoint=item.endpoint,
            fatia=f"reparo_plano:{chave}",
        )
        faltantes_depois = cenarios_faltantes(reparada, cats_do_grupo)
        if faltantes_depois and not cenarios_faltantes(parte, cats_do_grupo):
            # O reparo APAGOU cobertura que já existia. Medido em 2026-08-10: uma
            # volta pedida por QAORQ-051 devolveu 193 tokens no lugar de 1.846, e
            # CAT-08/CAT-09 sumiram de um grupo que estava completo.
            #
            # Cobertura é a invariante; qualidade é melhor-esforço. Reparo que
            # troca uma pendência heurística por uma categoria a menos é um mau
            # negócio, e a assimetria do projeto já diz de que lado ficar:
            # planejar a mais custa um teste, planejar a menos apaga cobertura
            # sem deixar rastro. Fica o original, com a pendência registrada.
            if registro:
                registro.aviso(
                    f"{item.endpoint}: o reparo do grupo {chave} perdeu "
                    f"{faltantes_depois} — mantido o plano anterior, que cobria tudo"
                )
        elif faltantes_depois:
            # Só a cobertura é fatal: categoria sem cenário apaga cobertura em
            # silêncio. As checagens heurísticas (QAORQ-051/052) que sobrarem
            # após o reparo viram aviso — falso positivo de palavra-chave não
            # pode custar o estágio inteiro.
            raise FalhaDeEstagio(
                f"o planejador não cobriu as categorias {faltantes_depois} do endpoint "
                f"{item.endpoint!r} mesmo depois do reparo (QAORQ-050)."
            )
        else:
            parte = reparada
        if registro:
            for violacao in _violacoes_do_grupo(parte, cats_do_grupo, item.endpoint):
                registro.aviso(f"plano aceito com pendência: {violacao.render()}")
    return [cenario for cenario in parte.cenarios if cenario.cat in cats_do_grupo]


def _violacoes_do_grupo(
    parte: PlanoDoEndpoint, cats_do_grupo: list[str], endpoint: str
) -> list[Violacao]:
    """O que o mini-loop cobra de uma resposta do planejador, em três moedas.

    QAORQ-050 (categoria sem cenário) é o único fatal; QAORQ-051 (escrita sem
    prova de estado) e QAORQ-052 (variações excedentes) nasceram da medição de
    2026-08-10 — releitura caiu de 55% para 25% e o plano inflou 50% com uma
    mudança de ênfase no prompt. Exigência que vive só em prosa flutua; aqui as
    duas viram régua que o script aplica e o modelo repara.
    """
    violacoes = [
        Violacao(
            codigo="QAORQ-050",
            mensagem=(
                f"{endpoint}: a categoria {cat} está em `cats` no gabarito e não "
                "tem nenhum cenário no plano"
            ),
        )
        for cat in cenarios_faltantes(parte, cats_do_grupo)
    ]
    violacoes += [
        Violacao(
            codigo="QAORQ-051",
            mensagem=(
                f"{endpoint}: o cenário {nome!r} usa método de escrita e o `espera` "
                "não prova o estado — acrescente a releitura (sucesso confirma o que "
                "persistiu; rejeição confirma que nada mudou)"
            ),
        )
        for nome in cenarios_sem_prova_de_estado(parte)
    ]
    violacoes += [
        Violacao(codigo="QAORQ-052", mensagem=f"{endpoint}: {mensagem}")
        for mensagem in variacoes_excedentes(parte)
    ]
    return violacoes
