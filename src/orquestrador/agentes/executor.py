"""Bloco 2 — agente executor (LLM, sem tools), fatiado por arquivo.

Não é um agente ReAct: são chamadas de LLM com entrada estruturada. Recebe o
`cobertura.json` de **um recurso**, o plano de cenários do planejador, e emite os
arquivos `.cy.js` — **um arquivo por chamada**, nunca a suíte inteira numa
resposta.

Por que fatiado: o tamanho da resposta pedida é o que separa pensamento saudável
de espiral. Medido com o mesmo modelo e o mesmo recurso: a suíte inteira numa
chamada travou 4 de 7 vezes (65.536 tokens de raciocínio, resposta nunca
começada) e, quando saiu, veio resumida (22 testes); fatiada — o `_support/` e
depois um spec por vez — foram 139 testes, zero travamentos, raciocínio sempre
com folga. O trabalho é mecânico se o plano for bom, e o Gate B pega os erros de
forma determinística — por isso o modelo deste estágio pode ser mais barato que o
do mapeador.

**A fatia é a operação**, não o grupo de categorias. O arquivo passou a ser um por
endpoint porque é assim que alguém procura um defeito ("criar cliente quebrou"),
e a fatia acompanhou o arquivo: cada chamada escreve um spec inteiro, com todas
as categorias daquele endpoint. O nome do arquivo é derivado do endpoint por
`nomes_dos_specs` — quem nomeia é o código, e é isso que faz o filtro de fatia
valer e o reparo achar o dono de uma violação.

O reparo regenera **apenas os arquivos que as violações apontam**: reescrever a
suíte inteira por causa de um `expect` sem mensagem é reabrir a porta da resposta
gigante que o fatiamento fechou.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from orquestrador.agentes.guarda_de_orcamento import exigir_folga
from orquestrador.config import Config
from orquestrador.dominio.artefatos import ArquivoGerado, SaidaExecutor, nomes_dos_specs
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.limpeza import render_ausencias, render_limpeza
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.plano import PlanoDeTestes
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.superficie import SuperficieDoProjeto
from orquestrador.dominio.veredito import Delta
from orquestrador.ferramentas.publicacao import AreaDeStaging
from orquestrador.llm.cliente import PoliticaDeRetentativa
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.llm.montagem import (
    LIMITE_PADRAO,
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
    recortar_por_violacoes,
)
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

ESTAGIO = "executor"

# A norma de escrita do código gerado, em `prompts/`. Não é um estágio: é conteúdo
# que o `executor.md` injeta e que o auditor consumirá pelo mesmo nome.
NORMA_DE_CODIGO = "padrao-de-codigo-cypress"

# O código da categoria dentro da mensagem de uma violação QAORQ-030.
_CAT_NA_MENSAGEM = re.compile(r"CAT-(?:0[1-9]|1[0-2])")

PREFIXO_SUPPORT = "_support/"


def instrucao_do_estagio(
    config: Config, recurso: Recurso, superficie: SuperficieDoProjeto | None = None
) -> str:
    """Instrução fixa do estágio.

    A superfície do projeto entra **aqui**, não na entrada da tentativa: ela é
    constante durante toda a execução, então mantém a instrução idêntica entre
    tentativas (princípio 2 e cache de prompt). Pela entrada, seria reenviada a cada
    reparo — inflando justamente o que o delta existe para enxugar.

    A fatia NÃO entra aqui pelo mesmo motivo, só que invertido: ela muda a cada
    chamada, e instrução que muda invalida o prefixo do cache para todas as outras.

    A norma de código é arquivo separado, injetada inteira. Ela não vive dentro do
    `executor.md` porque o auditor semântico julga contra a MESMA norma: regra que
    mora dentro do prompt de um estágio só pode ser lida por aquele estágio, e a
    segunda cópia diverge da primeira sem ninguém notar.
    """
    return carregar_prompt(
        ESTAGIO,
        {
            "recurso": recurso.nome,
            "caminho_recurso": str(recurso.caminho_testes),
            "caminho_projeto": str(config.caminhos.projeto_testes),
            "superficie_do_projeto": (
                superficie.render() if superficie else "(superfície não extraída)"
            ),
            "padrao_de_codigo": carregar_prompt(
                NORMA_DE_CODIGO, dir_prompts=config.caminhos.prompts
            ),
            "schema_json": esquema_json(SaidaExecutor),
        },
        dir_prompts=config.caminhos.prompts,
    )


def entrada_inicial(recurso: Recurso, manifesto: Manifesto) -> str:
    return montar_entrada_inicial(
        {
            "Recurso alvo": recurso.nome,
            "Diretório do recurso": str(recurso.caminho_testes),
            # `para_prompt`, não `para_json`: as justificativas de naoAplica não
            # têm consumidor aqui, e viajavam em cada uma das 4 fatias.
            "Gabarito do recurso (endpoints e categorias)": (
                f"```json\n{manifesto.para_prompt().strip()}\n```"
            ),
        }
    )


def executar(
    config: Config,
    recurso: Recurso,
    manifesto: Manifesto,
    *,
    modelo: BaseChatModel,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None = None,
    tentativa: int = 1,
    delta: Delta | None = None,
    artefato_atual: str | None = None,
    superficie: SuperficieDoProjeto | None = None,
    plano: PlanoDeTestes | None = None,
    dossie: DossieDoRecurso | None = None,
    inventario: Inventario | None = None,
) -> SaidaExecutor:
    """Uma tentativa do executor para um recurso. Sem histórico algum.

    Três caminhos, pela mesma razão de sempre — o tamanho da resposta:

    * `delta` presente → **reparo**: uma chamada que reescreve só os arquivos
      apontados pelas violações. A `SaidaExecutor` devolvida pode ser parcial; o
      staging acumula, e o gate mede o diretório inteiro.
    * `plano` presente → **geração fatiada**: `_support/` primeiro (a fundação),
      depois um spec por chamada, cada um com a fatia do plano que lhe cabe.
    * nenhum dos dois → o caminho antigo de chamada única. Existe para quem invoca
      o estágio isolado (testes, ferramentas) sem plano; o pipeline sempre planeja.
    """
    parametros = config.estagio(ESTAGIO)
    gerador = GeradorEstruturado(
        modelo=modelo,
        estagio=ESTAGIO,
        parametros=parametros,
        telemetria=telemetria,
        registro=registro,
        # Sem isto o orçamento de retentativa configurado em `[openrouter]` não chega
        # ao gerador e vale o padrão da classe — que hoje coincide, mas passaria a
        # divergir em silêncio no dia em que alguém mudasse o arquivo.
        politica=PoliticaDeRetentativa.do_config(config),
        antes_de_chamar=lambda: exigir_folga(
            config, telemetria, estagio=ESTAGIO, recurso=recurso.nome
        ),
    )
    instrucao = instrucao_do_estagio(config, recurso, superficie)

    if delta is not None:
        return _reparar(gerador, instrucao, recurso, delta, artefato_atual, tentativa, plano)
    if plano is not None:
        return _gerar_fatiado(
            gerador, instrucao, recurso, manifesto, plano, tentativa, dossie, inventario
        )

    return gerador.gerar(
        SaidaExecutor,
        instrucao=instrucao,
        entrada=entrada_inicial(recurso, manifesto),
        recurso=recurso.nome,
        tentativa=tentativa,
        fatia="recurso_inteiro",
    )


def _gerar_fatiado(
    gerador: GeradorEstruturado,
    instrucao: str,
    recurso: Recurso,
    manifesto: Manifesto,
    plano: PlanoDeTestes,
    tentativa: int,
    dossie: DossieDoRecurso | None = None,
    inventario: Inventario | None = None,
) -> SaidaExecutor:
    base = entrada_inicial(recurso, manifesto)
    arquivos: list[ArquivoGerado] = []

    # Fatia 1 — `_support/`: recebe o plano INTEIRO porque factories, helpers e
    # asserts servem a todos os specs; é a visão global que decide o que extrair.
    # O protocolo e a receita de limpeza entram AQUI, e não nos specs: é no
    # `_support/` que nasce o helper de cleanup, e um DELETE cego que ignora o
    # pré-requisito de versão "limpa" sem apagar nada.
    suporte = _fatia(
        gerador,
        instrucao,
        recurso,
        base + "\n## Fatia desta chamada: SOMENTE os arquivos `_support/`\n\n"
        "Gere api.js e, quando a arquitetura dos arquivos pedir, factories.js, "
        "helpers.js e asserts.js. O plano completo abaixo é o contexto do que os "
        "specs vão consumir. Os specs serão gerados em chamadas próprias.\n\n"
        "### Plano de cenários do recurso\n\n"
        + plano.render()
        + _contexto_do_suporte(dossie, inventario),
        tentativa=tentativa,
        aceitos=lambda caminho: caminho.startswith(PREFIXO_SUPPORT),
        rotulo="_support/",
        endpoint=",".join(parte.endpoint for parte in plano.endpoints),
    )
    # Fatia vazia não interrompe: quem reprova é o Gate B, que mede o diretório e
    # acusa o spec-base ausente (QAAPI-002) com um delta que o reparo sabe atender.
    # Interromper aqui trocaria uma reprovação reparável por falha de estágio.
    arquivos += suporte
    suporte_texto = (
        "\n\n".join(f"--- {a.caminho} ---\n{a.conteudo}" for a in suporte)
        or "(nenhum arquivo de _support foi gerado nesta tentativa)"
    )

    # Fatias 2..n — um spec por OPERAÇÃO. O nome do arquivo é derivado do endpoint
    # por código, e não escolhido pelo modelo: é o que faz o filtro `aceitos` valer
    # (duas fatias jamais disputam o mesmo caminho) e o que deixa o reparo achar o
    # dono de uma violação de cobertura.
    nomes = nomes_dos_specs([parte.endpoint for parte in plano.endpoints])
    for parte in plano.endpoints:
        nome = nomes[parte.endpoint]
        gerados = _fatia(
            gerador,
            instrucao,
            recurso,
            base + f"\n## Fatia desta chamada: SOMENTE `{nome}`\n\n"
            f"Este arquivo cobre a operação `{parte.endpoint}`, e só ela. Transcreva "
            "os cenários do plano abaixo em `it`s — cada linha vira um caso, na "
            "ordem, com a tag da categoria — agrupados em `context` por "
            "circunstância, como manda a norma. Os arquivos `_support/` JÁ EXISTEM "
            "com o conteúdo mostrado; importe deles.\n\n"
            f"### Plano desta fatia\n\n{parte.render()}"
            + _regras_da_fatia(dossie, parte.regras_citadas())
            + f"\n\n### _support já gerado\n\n{suporte_texto}",
            tentativa=tentativa,
            aceitos=lambda caminho, nome=nome: caminho == nome,
            rotulo=nome,
            endpoint=parte.endpoint,
        )
        arquivos += gerados

    return SaidaExecutor(recurso=manifesto.recurso, arquivos=arquivos)


def _contexto_do_suporte(dossie: DossieDoRecurso | None, inventario: Inventario | None) -> str:
    """As seções do dossiê que o `_support/` precisa e os specs não repetem.

    Regras transversais (protocolo de versão, autenticação) viram helper; a
    receita de limpeza vira o cleanup; as ausências impedem helper para rota
    imaginária. Tudo determinístico ou já pago pelo mapeador — zero token novo.
    """
    partes: list[str] = []
    if dossie is not None and dossie.regras_transversais:
        partes.append(
            "## Protocolo do recurso — regras transversais lidas da fonte\n\n"
            + "\n\n".join(regra.render() for regra in dossie.regras_transversais)
        )
    if inventario is not None:
        partes.append(render_limpeza(inventario, dossie))
        partes.append(render_ausencias(inventario))
    if not partes:
        return ""
    return "\n\n" + "\n\n".join(partes)


def _regras_da_fatia(dossie: DossieDoRecurso | None, ids: list[str]) -> str:
    """As regras do dossiê que os cenários desta fatia citam — e só elas.

    A fatia diz o que transcrever; a regra diz o que o `espera` está provando e
    com que evidência. Reenviar o dossiê inteiro em cada fatia pagaria o custo uma
    vez por operação; regra não citada por cenário nenhum não entra.
    """
    if dossie is None or not ids:
        return ""
    citadas = dossie.regras_por_id(ids)
    if not citadas:
        return ""
    return "\n\n### Regras do dossiê citadas por esta fatia\n\n" + "\n\n".join(
        regra.render() for regra in citadas
    )


def _fatia(
    gerador: GeradorEstruturado,
    instrucao: str,
    recurso: Recurso,
    entrada: str,
    *,
    tentativa: int,
    aceitos: Callable[[str], bool],
    rotulo: str,
    endpoint: str = "",
) -> list[ArquivoGerado]:
    """Uma chamada de fatia, com o filtro que faz o contrato dela valer.

    O filtro descarta o que a chamada devolver fora da própria fatia — modelo que
    "aproveita" para reescrever outro arquivo criaria caminho duplicado no merge e
    tornaria a ordem das fatias significativa. Descartar é seguro: o arquivo
    pertence à chamada dona dele.
    """
    saida = gerador.gerar(
        SaidaExecutor,
        instrucao=instrucao,
        entrada=entrada
        + "\n\nResponda APENAS com o JSON do contrato, contendo SOMENTE os arquivos "
        f"desta fatia ({rotulo}).",
        recurso=recurso.nome,
        tentativa=tentativa,
        endpoint=endpoint,
        fatia=rotulo,
    )
    return [arquivo for arquivo in saida.arquivos if aceitos(arquivo.caminho)]


def _reparar(
    gerador: GeradorEstruturado,
    instrucao: str,
    recurso: Recurso,
    delta: Delta,
    artefato_atual: str | None,
    tentativa: int,
    plano: PlanoDeTestes | None = None,
) -> SaidaExecutor:
    """Reparo dirigido: reescreve os arquivos que as violações apontam.

    A fórmula continua `instrução + artefato_atual + violações` (princípio 2); o
    que muda é o pedido — devolver só o que precisa mudar. `SaidaExecutor` parcial
    é suficiente porque o staging acumula entre tentativas e o gate mede o disco.
    """
    implicados = {
        nome
        for violacao in delta.violacoes
        if violacao.arquivo
        for nome in [_nome_de_suite(violacao.arquivo)]
        if nome
    }
    # QAORQ-030 aponta o MANIFESTO (onde a categoria é declarada), não o spec que
    # deveria ter o teste — medido: sem esta tradução, o reparo nunca mirava o
    # arquivo certo e as três tentativas passavam sem escrever o `it` cobrado.
    # Com o spec por operação quem sabe onde o teste mora é o PLANO: foi ele que
    # decidiu qual endpoint cobre qual categoria, e o endpoint decide o arquivo.
    # É mais preciso do que a tabela que existia aqui, que implicava um spec
    # inteiro — e todos os endpoints dele — por causa de uma categoria só.
    cats_faltantes: set[str] = set()
    for violacao in delta.violacoes:
        if violacao.codigo != "QAORQ-030":
            continue
        for casamento in _CAT_NA_MENSAGEM.finditer(violacao.mensagem):
            cats_faltantes.add(casamento.group(0))
    if plano is not None and cats_faltantes:
        nomes = nomes_dos_specs([parte.endpoint for parte in plano.endpoints])
        implicados.update(nomes[endpoint] for endpoint in plano.endpoints_das_cats(cats_faltantes))

    ordenados = sorted(implicados)
    if ordenados:
        alvo = (
            "Reescreva SOMENTE os arquivos apontados pelas violações: "
            + ", ".join(f"`{nome}`" for nome in ordenados)
            + ". Devolva apenas eles; os demais permanecem como estão no disco."
        )
    else:
        alvo = (
            "Reescreva apenas os arquivos necessários para sanar as violações e "
            "devolva somente os que mudou; os demais permanecem como estão no disco."
        )

    # A fatia do plano das categorias cobradas volta junto: a violação diz O QUE
    # falta, o plano diz COMO era para ser — sem ele, o modelo re-decide o cenário
    # que o planejador já tinha decidido.
    if plano is not None and cats_faltantes:
        fatia = plano.cenarios_das_cats(tuple(sorted(cats_faltantes)))
        if fatia.strip():
            alvo += "\n\n### Cenários do plano para as categorias cobradas\n\n" + fatia

    return gerador.gerar(
        SaidaExecutor,
        instrucao=instrucao,
        entrada=montar_entrada_reparo(artefato_atual or "(artefato ausente)", delta)
        + "\n\n"
        + alvo,
        recurso=recurso.nome,
        tentativa=tentativa,
        fatia="reparo_gate",
    )


def _nome_de_suite(caminho: str) -> str | None:
    """O caminho da violação reduzido ao arquivo da suíte, ou `None`.

    As violações chegam com três convenções de raiz (skill, eslint, orquestrador);
    o que identifica o arquivo dentro do recurso é o sufixo `*.cy.js` ou
    `_support/<nome>.js`. `cobertura.json` não é reescrevível pelo executor — ele
    pertence ao mapeador — então violação sobre ele não implica arquivo nenhum.
    """
    normalizado = caminho.replace("\\", "/")
    nome = normalizado.rsplit("/", 1)[-1]
    if nome == "cobertura.json":
        return None
    if nome.endswith(".cy.js"):
        return nome
    if nome.endswith(".js") and "/_support/" in f"/{normalizado}":
        return f"{PREFIXO_SUPPORT}{nome}"
    return None


def escrever(area: AreaDeStaging, saida: SaidaExecutor) -> list[Path]:
    """Materializa os arquivos na área de staging — o handoff é o disco.

    A escrita vai para o staging da execução, nunca para o diretório do recurso: é
    o gate que decide se aquilo chega ao projeto de quem nos contratou, e ele só
    decide depois de rodar. Quem publica é `ferramentas.publicacao`, e é lá que
    mora também o confinamento — `AreaDeStaging.escrever` chama `confinar`, então a
    comparação textual que vivia aqui (e aceitava `.../pedidos-antigos` por
    `.../pedidos`) não voltou por outra porta.
    """
    return [area.escrever(arquivo.caminho, arquivo.conteudo) for arquivo in saida.arquivos]


def artefato_em_disco(
    dir_recurso: Path,
    saida: SaidaExecutor,
    delta: Delta | None = None,
    *,
    limite: int = LIMITE_PADRAO,
) -> str:
    """Texto do artefato atual para o prompt de reparo (o que está no staging).

    Lê do disco, e não da saída do modelo, porque é o disco que o gate mediu — e
    varre o diretório além de `saida.arquivos` porque, com o reparo devolvendo
    `SaidaExecutor` parcial, a saída da última tentativa deixou de enumerar a
    suíte inteira. O `cobertura.json` fica de fora: ele é artefato do mapeador, e
    o executor não pode reescrevê-lo. A escolha do que cabe é de
    `llm.montagem.recortar_por_violacoes`: aqui só o I/O.
    """
    arquivos: dict[str, str] = {}
    for arquivo in saida.arquivos:
        caminho = dir_recurso / arquivo.caminho
        arquivos[arquivo.caminho] = (
            caminho.read_text(encoding="utf-8") if caminho.is_file() else arquivo.conteudo
        )
    if dir_recurso.is_dir():
        for caminho in sorted(dir_recurso.rglob("*.js")):
            relativo = caminho.relative_to(dir_recurso).as_posix()
            if relativo not in arquivos:
                arquivos[relativo] = caminho.read_text(encoding="utf-8")
    return recortar_por_violacoes(arquivos, delta.violacoes if delta else [], limite=limite)
