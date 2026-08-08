"""O regime de um adaptador: fixture real, golden, e os buracos escritos.

Por que este arquivo existe
---------------------------
`MATRIZ_DE_SUPORTE` é uma tupla. Acrescentar uma linha nela é a coisa mais fácil
do repositório, e é também a afirmação mais cara que ele faz: "suportamos esta
linguagem" significa que existe um **denominador determinístico** — alguém que
sabe contar o que o backend expõe e reprovar quando o gabarito planeja menos.

Sem este arquivo, a linha nova custava zero e a afirmação valia o mesmo. A
tentação está descrita no README: acrescentar uma frase ao prompt dizendo "suporte
também FastAPI" e chamar isso de suporte. É o defeito de origem do projeto com
outra roupa — um LLM sempre devolve *alguma* coisa; o que falta não é a capacidade
de escrever teste, é a régua.

O que passa a ser exigido de todo adaptador
-------------------------------------------
1. **um projeto-fixture real** sob `fixtures/backends/<nome>/`, com o `graph.json`
   ao lado — não um trecho de código numa string de teste;
2. **um golden** dos endpoints extraídos daquele fixture;
3. **falsos negativos declarados**, não vazios.

O terceiro é o que mais incomoda e o mais importante. Um adaptador que declara
lista vazia está afirmando cobertura total de um framework inteiro, e isso nunca é
verdade — o que a lista vazia significa é que ninguém procurou os buracos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.analise_estatica.extrator_de_endpoints import (
    MATRIZ_DE_SUPORTE,
    Adaptador,
    extrair,
)
from orquestrador.raiz import DIR_FIXTURES

pytestmark = pytest.mark.unit

DIR_GOLDENS = Path(__file__).resolve().parent / "goldens"
DIR_BACKENDS = DIR_FIXTURES / "backends"


def apelido(adaptador: Adaptador) -> str:
    """`java-spring` — o nome do fixture e do golden deste adaptador."""
    return f"{adaptador.linguagem}-{adaptador.framework.split()[0]}".lower().replace(" ", "-")


@pytest.mark.parametrize("adaptador", MATRIZ_DE_SUPORTE, ids=apelido)
def test_todo_adaptador_declara_o_que_nao_pega(adaptador: Adaptador):
    """Lista vazia é uma afirmação de cobertura total, e ela nunca é verdadeira."""
    assert adaptador.falsos_negativos, (
        f"o adaptador {adaptador.linguagem}/{adaptador.framework} não declara nenhum "
        "falso negativo.\n"
        "Todo parser de rota tem buraco: rota montada por concatenação, controlador "
        "registrado programaticamente, herança de fora do backend indexado. Escrever "
        "os que você conhece é o que separa 'suportamos X' de 'rodou uma vez'.\n"
        "Se você procurou e não achou nenhum, declare isso — a lista existe para "
        "registrar a procura, não só o resultado."
    )


@pytest.mark.parametrize("adaptador", MATRIZ_DE_SUPORTE, ids=apelido)
def test_todo_adaptador_tem_projeto_fixture_com_grafo(adaptador: Adaptador):
    """Projeto real, e não trecho numa string.

    Um trecho de código dentro do teste prova o parser; um projeto com `graph.json`
    prova a **cadeia** — o grafo aponta os arquivos, o confinamento os resolve, a
    matriz escolhe o adaptador, e só então o parser roda. É nessa cadeia que os
    defeitos aparecem: arquivo do grafo que não existe mais no disco, extensão que
    a matriz não reconhece, caminho de teste que infla o denominador.
    """
    projeto = DIR_BACKENDS / apelido(adaptador)

    assert (projeto / "graph.json").is_file(), (
        f"o adaptador {adaptador.linguagem}/{adaptador.framework} não tem "
        f"projeto-fixture em {projeto.relative_to(DIR_FIXTURES.parent)}.\n"
        "Ele precisa de um `graph.json` e dos arquivos que o grafo cita — é o mínimo "
        "para exercitar a cadeia inteira, e não só o parser."
    )


@pytest.mark.parametrize("adaptador", MATRIZ_DE_SUPORTE, ids=apelido)
def test_o_que_o_adaptador_extrai_do_fixture_esta_congelado(
    pytestconfig: pytest.Config, adaptador: Adaptador
):
    """O golden por adaptador: quantos endpoints, quais, e o que ficou de fora.

    É ele que responde "a precisão caiu?" depois de mexer no parser. Sem ele, um
    regex ajustado para pegar um caso novo pode deixar de pegar três antigos, e a
    suíte continua verde porque nenhum teste olhava o conjunto.
    """
    nome = f"endpoints-{apelido(adaptador)}.json"
    projeto = DIR_BACKENDS / apelido(adaptador)
    if not (projeto / "graph.json").is_file():
        pytest.skip("sem projeto-fixture; o teste anterior é quem cobra isso")

    backend = extrair(graph=projeto / "graph.json", backend=projeto)
    atual = (
        json.dumps(
            {
                "classes": [
                    {
                        "classe": classe.classe,
                        "arquivo": classe.arquivo,
                        "endpoints": [
                            f"{endpoint.metodo} {endpoint.rota}" for endpoint in classe.endpoints
                        ],
                        "nao_resolvidas": [rota.expressao for rota in classe.nao_resolvidas],
                    }
                    for classe in sorted(backend.classes, key=lambda c: c.classe)
                ],
                "arquivos_no_grafo": backend.arquivos_no_grafo,
                "arquivos_analisados": backend.arquivos_analisados,
                "arquivos_de_teste": backend.arquivos_de_teste,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

    if pytestconfig.getoption("--mostrar-golden") == nome:
        print(f"\n----- {nome} -----\n{atual}----- fim -----")
        pytest.skip(f"--mostrar-golden={nome}: conteúdo impresso, nada gravado")

    arquivo = DIR_GOLDENS / nome
    assert arquivo.is_file(), (
        f"o adaptador {adaptador.linguagem}/{adaptador.framework} não tem golden.\n"
        f"Gere-o com:\n"
        f"  pytest tests/test_analise_estatica_extrator_de_endpoints.py -s "
        f"--mostrar-golden={nome}\n"
        f"e escreva {arquivo.with_suffix('.md').name} dizendo o que ele prova."
    )
    assert atual == arquivo.read_text(encoding="utf-8"), (
        f"o golden {nome} divergiu — o adaptador passou a extrair outra coisa do "
        "mesmo fixture.\n"
        "Se você acrescentou um caso ao parser, o diff deve **crescer**. Endpoint "
        "que some é regressão até prova em contrário: o denominador do Gate A "
        "encolheu, e cobertura sobe quando a régua encolhe."
    )


@pytest.mark.parametrize("adaptador", MATRIZ_DE_SUPORTE, ids=apelido)
def test_a_precisao_do_adaptador_esta_publicada(adaptador: Adaptador):
    """ "27 de 27" precisa estar num lugar que o usuário lê antes de instalar.

    A matriz de suporte é o documento que responde "serve para o meu projeto?". Uma
    matriz que diz "Tier A" sem dizer quanto o extrator acha está pedindo confiança
    em vez de dar evidência.
    """
    pagina = (
        Path(__file__).resolve().parent.parent / "docs" / "referencia" / "matriz-de-suporte.md"
    ).read_text(encoding="utf-8")

    assert apelido(adaptador).split("-")[0] in pagina.lower(), (
        f"{adaptador.linguagem} não aparece em docs/referencia/matriz-de-suporte.md.\n"
        "Adaptador que existe no código e não na matriz é suporte que ninguém "
        "consegue descobrir antes de instalar."
    )
    assert "precisão medida" in pagina.lower(), (
        "a matriz de suporte não publica a precisão medida de nenhum adaptador.\n"
        "É o número que transforma 'Tier A' de promessa em evidência."
    )
