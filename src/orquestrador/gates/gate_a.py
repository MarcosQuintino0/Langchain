"""Gate A — verificação do plano (determinístico).

Duas checagens:

1. `validar-suite-gerada.mjs <recurso> --so-manifesto --json` — a contabilidade das
   12 categorias e as justificativas de `naoAplica`.
2. **Diff grafo × manifesto** — compara os endpoints que o backend expõe com os que
   o `cobertura.json` declara.

Por que a segunda checagem existe: o validador da skill enxerga apenas o projeto de
testes, nunca o backend — limite deliberado, documentado em
`skills/qa-api/scripts/cobertura/handlers.mjs:15`. Ele prova "entreguei o que
planejei", nunca "planejei tudo que existe". O diff é o que fecha esse elo, e é a
única checagem do projeto cujo denominador não passa por LLM nenhum: o manifesto e
o inventário saem do mapeador, mas os endpoints do diff saem do fonte do backend.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.analise_estatica.extrator_de_endpoints import (
    EndpointsDoBackend,
    chave_de_endpoint,
    extrair,
    matriz_em_texto,
)
from orquestrador.config import Config
from orquestrador.dominio.inventario import Endpoint, Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import ResultadoGate, Violacao
from orquestrador.excecoes import GrafoNaoPreparado
from orquestrador.ferramentas.scripts_qa import Validador
from orquestrador.gates.saidas import resultado_do_validador

NOME = "gate_a"

CODIGO_INCERTEZA = "QAORQ-001"
CODIGO_FALTA_NO_MANIFESTO = "QAORQ-002"
CODIGO_ROTA_INVENTADA = "QAORQ-003"

# Quantas incertezas nomear antes de resumir. Aviso é para ser lido; parede de texto
# no console é o mesmo que aviso nenhum.
LIMITE_DE_AVISOS = 20


def executar(
    config: Config,
    recurso: Recurso,
    *,
    dir_recurso: Path,
    dir_schemas: Path,
    inventario: Inventario | None = None,
    manifesto: Manifesto | None = None,
) -> ResultadoGate:
    """Roda as duas checagens do Gate A sobre o artefato em disco.

    `dir_recurso` e `dir_schemas` são obrigatórios e apontam para o **staging** da
    execução, não para o destino final. Não têm valor padrão de propósito: um
    padrão que caísse no diretório do recurso faria o gate aprovar o que está
    publicado enquanto o loop de reparo trabalha em outro lugar — validar um
    artefato e publicar outro é o defeito que o staging existe para fechar.
    """
    flags = list(config.gate("a").flags)
    if "--so-manifesto" not in flags:
        flags.insert(0, "--so-manifesto")

    saida = Validador(config).executar(dir_recurso, flags, schemas=dir_schemas)
    manifesto_ok = resultado_do_validador(saida, gate=NOME)
    diff = diff_grafo_manifesto(
        graph=config.caminhos.graph_abs,
        backend=config.caminhos.backend,
        inventario=inventario,
        manifesto=manifesto,
        recurso=recurso,
    )
    # `exigir_veredito` interrompe quando o validador não se comportou como o
    # contrato dele diz: sem veredito confiável não há o que mandar ao mapeador.
    return ResultadoGate.combinar([manifesto_ok, diff], gate=NOME).exigir_veredito()


def diff_grafo_manifesto(
    *,
    graph: Path,
    backend: Path,
    inventario: Inventario | None,
    manifesto: Manifesto | None,
    recurso: Recurso,
) -> ResultadoGate:
    """Cruza os endpoints do backend com os do gabarito.

    * `QAORQ-002` — endpoint que o backend expõe e o gabarito não declara. É o
      "planejei menos do que existe" que o validador da skill não alcança.
    * `QAORQ-003` — endpoint declarado sem correspondente no backend: rota
      inventada.
    * `QAORQ-001` — aviso: um trecho que o extrator **não** conseguiu resolver. Não
      reprova, e não some.

    Escopo
    ------
    O gabarito é de um recurso; o backend tem todos. Comparar os dois conjuntos
    inteiros faria cada recurso reprovar por causa dos endpoints dos outros. Então o
    escopo do `QAORQ-002` são as **classes controladoras que o próprio gabarito já
    tocou**: se ele declara um endpoint de `ProductController`, os outros quatro
    daquela classe passam a ser cobrança devida. É a forma exata do defeito de
    origem — amostrar três de cinco no mesmo controlador — e é decidível sem
    adivinhar a que recurso pertence uma classe que o gabarito ignorou por inteiro.

    Fora da matriz de suporte
    -------------------------
    Quando nenhum adaptador consegue ler um endpoint sequer do backend, o resultado
    é `ERRO_DA_FERRAMENTA`, não aprovação e não reprovação — a mesma escolha de
    `gates/lacunas.py` quando o contador não vem. Aprovar seria declarar cobertura
    completa sem ter o denominador, que é precisamente o defeito que este diff
    existe para fechar; reprovar mandaria o mapeador consertar um artefato correto
    por causa de uma linguagem que ninguém implementou aqui, e o loop queimaria as
    três tentativas sem chance de convergir.
    """
    if manifesto is None:
        return ResultadoGate.erro_da_ferramenta(
            "diff grafo × manifesto sem manifesto em memória: não há o que cruzar com "
            "o backend. O Gate A precisa receber o manifesto do mapeador.",
            gate=NOME,
        )

    try:
        backend_lido = extrair(graph=graph, backend=backend)
    except GrafoNaoPreparado as erro:
        return ResultadoGate.erro_da_ferramenta(str(erro), gate=NOME)

    if not backend_lido.endpoints:
        return ResultadoGate.erro_da_ferramenta(_sem_denominador(backend_lido, backend), gate=NOME)

    arquivo = f"{recurso.nome}/_support/cobertura.json"
    do_backend = {
        chave_de_endpoint(endpoint.canonico): endpoint for endpoint in backend_lido.endpoints
    }
    do_gabarito = {chave_de_endpoint(item.endpoint): item.endpoint for item in manifesto.endpoints}

    violacoes = [
        *_rotas_inventadas(do_gabarito, do_backend, backend_lido, arquivo),
        *_faltando_no_gabarito(do_gabarito, backend_lido, arquivo),
    ]
    avisos = _avisos(do_gabarito, do_backend, backend_lido, inventario, arquivo)

    if violacoes:
        return ResultadoGate.reprovado_por(violacoes, avisos=avisos, gate=NOME)
    return ResultadoGate.aprovado_por(avisos=avisos, gate=NOME)


def _sem_denominador(backend_lido: EndpointsDoBackend, backend: Path) -> str:
    return (
        "diff grafo × manifesto sem denominador: nenhum adaptador da matriz de suporte "
        f"encontrou endpoint no backend em {backend}.\n"
        f"Matriz de hoje: {matriz_em_texto()}.\n"
        f"O grafo declara {backend_lido.arquivos_no_grafo} arquivo(s); "
        f"{backend_lido.arquivos_analisados} foram analisados, "
        f"{backend_lido.arquivos_de_teste} são fonte de teste e o resto ficou de fora "
        f"por extensão ({backend_lido.resumo_do_ignorado()}).\n"
        "Isto não é aprovação nem reprovação: sem saber o que o backend expõe, o Gate A "
        'volta a provar só "entreguei o que planejei". O que fazer: implemente o '
        "adaptador da linguagem/framework deste backend em "
        "`analise_estatica/extrator_de_endpoints.py` (a matriz fica lá, declarada), ou "
        "confira se `[caminhos].backend` e o `graph.json` do Bloco 0 apontam para o "
        "projeto certo."
    )


def _faltando_no_gabarito(
    do_gabarito: dict[str, str],
    backend_lido: EndpointsDoBackend,
    arquivo: str,
) -> list[Violacao]:
    em_escopo = {
        classe.classe
        for chave in do_gabarito
        if (classe := backend_lido.classe_de(chave)) is not None
    }
    violacoes: list[Violacao] = []
    for classe in backend_lido.classes:
        if classe.classe not in em_escopo:
            continue
        for endpoint in classe.endpoints:
            chave = chave_de_endpoint(endpoint.canonico)
            if chave in do_gabarito:
                continue
            violacoes.append(
                Violacao(
                    codigo=CODIGO_FALTA_NO_MANIFESTO,
                    arquivo=arquivo,
                    mensagem=(
                        f'"{endpoint.canonico}" existe no backend '
                        f"({endpoint.handler} em "
                        f"{endpoint.arquivo}:{endpoint.linha}) e não está em `endpoints` do "
                        f"gabarito, que já cobre outros endpoints de {classe.classe}. "
                        "Acrescente a entrada com a contabilidade das 12 categorias em "
                        "`cats`/`naoAplica` — ou, se ela pertence a outro recurso, o "
                        "gabarito deste recurso não deveria tocar essa classe."
                    ),
                )
            )
    return violacoes


def _rotas_inventadas(
    do_gabarito: dict[str, str],
    do_backend: dict[str, Endpoint],
    backend_lido: EndpointsDoBackend,
    arquivo: str,
) -> list[Violacao]:
    fora_da_matriz = ""
    if backend_lido.extensoes_ignoradas:
        fora_da_matriz = (
            f" (o extrator leu {backend_lido.arquivos_analisados} arquivo(s) da matriz "
            f"{matriz_em_texto()}; ficaram de fora {backend_lido.resumo_do_ignorado()})"
        )
    return [
        Violacao(
            codigo=CODIGO_ROTA_INVENTADA,
            arquivo=arquivo,
            mensagem=(
                f'"{declarado}" está em `endpoints` do gabarito e nenhum controlador do '
                f"backend declara essa rota{fora_da_matriz}. Corrija o método ou o "
                "caminho a partir do fonte, ou remova a entrada: teste escrito contra "
                "rota inexistente não cobre nada."
            ),
        )
        for chave, declarado in do_gabarito.items()
        if chave not in do_backend
    ]


def _avisos(
    do_gabarito: dict[str, str],
    do_backend: dict[str, Endpoint],
    backend_lido: EndpointsDoBackend,
    inventario: Inventario | None,
    arquivo: str,
) -> list[Violacao]:
    """Tudo que o diff não conseguiu afirmar — e que não pode desaparecer.

    Rota dinâmica não resolvida, do extrator ou do inventário, é **registro de
    incerteza, não ausência**: o extrator não sabe qual endpoint é, então não há
    endpoint a cobrar com `QAORQ-002`, e o mapeador não teria o que consertar. Vira
    aviso — o desfecho fica visível no log e no manifesto da execução, em vez de o
    buraco virar cobertura completa por omissão.

    O caminho oposto seria reprovar por incerteza, e ele é pior do que parece:
    tornaria "não sei ler esta rota" indistinguível de "você esqueceu este
    endpoint", e o modelo passaria a inventar entradas de manifesto para calar o
    gate.
    """
    incertezas = [
        Violacao(
            codigo=CODIGO_INCERTEZA,
            arquivo=rota.arquivo,
            linha=rota.linha,
            mensagem=(
                f"{rota.motivo}. O diff não cobre esta declaração: ela não gera "
                f"QAORQ-002 nem QAORQ-003. Expressão: {rota.expressao}"
            ),
        )
        for rota in backend_lido.nao_resolvidas
    ]
    if inventario is not None:
        incertezas += [
            Violacao(
                codigo=CODIGO_INCERTEZA,
                arquivo=rota.arquivo,
                linha=rota.linha,
                mensagem=(
                    f"o inventário registrou rota não resolvida: {rota.motivo}. "
                    f"Expressão: {rota.expressao}"
                ),
            )
            for rota in inventario.rotas_dinamicas_nao_resolvidas
        ]

    if backend_lido.extensoes_ignoradas or backend_lido.arquivos_ausentes:
        incertezas.append(
            Violacao(
                codigo=CODIGO_INCERTEZA,
                arquivo=arquivo,
                mensagem=(
                    f"o diff leu {backend_lido.arquivos_analisados} de "
                    f"{backend_lido.arquivos_no_grafo} arquivo(s) do grafo "
                    f"({matriz_em_texto()}); ficaram fora da matriz "
                    f"{backend_lido.resumo_do_ignorado()}"
                    + (
                        f" e {len(backend_lido.arquivos_ausentes)} arquivo(s) do grafo "
                        "não estão em disco (mapa defasado: rode o Bloco 0)"
                        if backend_lido.arquivos_ausentes
                        else ""
                    )
                    + ". Endpoint declarado fora da matriz não é enxergado por este gate."
                ),
            )
        )

    incertezas += _grafias_divergentes(do_gabarito, do_backend, arquivo)

    if len(incertezas) <= LIMITE_DE_AVISOS:
        return incertezas
    return [
        *incertezas[:LIMITE_DE_AVISOS],
        Violacao(
            codigo=CODIGO_INCERTEZA,
            arquivo=arquivo,
            mensagem=(
                f"e mais {len(incertezas) - LIMITE_DE_AVISOS} registro(s) de incerteza "
                f"do diff, de {len(incertezas)} no total"
            ),
        ),
    ]


def _grafias_divergentes(
    do_gabarito: dict[str, str],
    do_backend: dict[str, Endpoint],
    arquivo: str,
) -> list[Violacao]:
    """Mesmo endpoint, nome de variável de caminho diferente do que o backend usa."""
    avisos: list[Violacao] = []
    for chave, declarado in do_gabarito.items():
        real = do_backend.get(chave)
        if real is not None and real.canonico != declarado:
            avisos.append(
                Violacao(
                    codigo=CODIGO_INCERTEZA,
                    arquivo=arquivo,
                    mensagem=(
                        f'"{declarado}" casa com "{real.canonico}" do backend, mas escreve a '
                        "variável de caminho com outro nome. Não é reprovação — o teste "
                        "chama a mesma rota —, e alinhar as duas grafias evita que a "
                        "próxima leitura pareça divergência."
                    ),
                )
            )
    return avisos
