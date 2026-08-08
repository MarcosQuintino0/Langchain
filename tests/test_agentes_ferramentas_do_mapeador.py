"""Toda chamada de tool do mapeador vira medida.

O Graphify existe para localizar código sem gastar token varrendo o backend, e a
instrução do estágio manda consultá-lo antes de ler arquivo. Sem este registro,
"ele obedeceu?" e "que fatia da entrada veio de resposta de tool?" seriam dedução —
e é sobre elas que se decide a otimização do mapeador, onde mora quase todo o custo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.agentes.ferramentas_do_mapeador import criar_ferramentas
from orquestrador.ferramentas import graphify
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


@pytest.fixture
def backend(config_falso) -> Path:
    raiz = config_falso.caminhos.backend
    (raiz / "src").mkdir(parents=True, exist_ok=True)
    (raiz / "src" / "PedidoController.java").write_text(
        "class PedidoController {}\n", encoding="utf-8"
    )
    return raiz


def tools(config_falso, telemetria: Telemetria | None, tentativa: int = 1) -> dict:
    criadas = criar_ferramentas(
        config_falso,
        estagio="mapeador",
        telemetria=telemetria,
        recurso="pedidos",
        tentativa=tentativa,
    )
    return {ferramenta.name: ferramenta for ferramenta in criadas}


def test_chamada_vira_registro_com_argumentos_e_tamanho(config_falso, backend):
    telemetria = Telemetria()

    saida = tools(config_falso, telemetria)["listar_diretorio"].invoke({"caminho": "src"})

    assert len(telemetria.tools) == 1
    registrada = telemetria.tools[0]
    assert registrada.nome == "listar_diretorio"
    assert registrada.argumentos == {"caminho": "src"}
    assert registrada.caracteres == len(saida)
    assert registrada.estagio == "mapeador"
    assert registrada.recurso == "pedidos"
    assert registrada.tentativa == 1
    assert registrada.erro is False


def test_o_envelope_nao_altera_o_que_a_tool_devolve(config_falso, backend):
    # O modelo precisa receber o texto intacto: medir não pode mudar o que ele lê.
    sem = tools(config_falso, None)["listar_diretorio"].invoke({"caminho": "src"})
    com = tools(config_falso, Telemetria())["listar_diretorio"].invoke({"caminho": "src"})
    assert sem == com


def test_argumentos_omitidos_aparecem_com_o_padrao(config_falso, backend):
    # Sem os defaults, "leu o arquivo inteiro ou só um trecho?" ficaria sem resposta.
    telemetria = Telemetria()

    tools(config_falso, telemetria)["ler_arquivo"].invoke({"caminho": "src/PedidoController.java"})

    assert telemetria.tools[0].argumentos == {
        "caminho": "src/PedidoController.java",
        "offset": 0,
        "limit": 500,
    }


def test_falha_devolvida_como_texto_e_marcada_como_erro(config_falso, backend):
    # As tools engolem a exceção e devolvem "ERRO: ..." para o modelo se corrigir.
    # Sem a marca, grafo quebrado passaria por exploração bem-sucedida no relatório.
    telemetria = Telemetria()

    saida = tools(config_falso, telemetria)["listar_diretorio"].invoke(
        {"caminho": "../fora-da-raiz"}
    )

    assert saida.startswith("ERRO:")
    assert telemetria.tools[0].erro is True


def test_ordem_reconstroi_a_sequencia_de_exploracao(config_falso, backend):
    # É a ordem, não o total, que responde "consultou o grafo antes de ler arquivo?".
    telemetria = Telemetria()
    criadas = tools(config_falso, telemetria)

    criadas["listar_diretorio"].invoke({"caminho": "src"})
    criadas["ler_arquivo"].invoke({"caminho": "src/PedidoController.java"})
    criadas["listar_diretorio"].invoke({"caminho": "src"})

    assert [(t.ordem, t.nome) for t in telemetria.tools] == [
        (1, "listar_diretorio"),
        (2, "ler_arquivo"),
        (3, "listar_diretorio"),
    ]


def test_sem_telemetria_as_tools_continuam_funcionando(config_falso, backend):
    # A instrumentação é opcional para que as tools sigam construtíveis isoladamente.
    saida = tools(config_falso, None)["listar_diretorio"].invoke({"caminho": "src"})
    assert "PedidoController.java" in saida


def test_agregacao_soma_por_nome_e_conta_erro(config_falso, backend):
    telemetria = Telemetria()
    criadas = tools(config_falso, telemetria)

    criadas["listar_diretorio"].invoke({"caminho": "src"})
    criadas["listar_diretorio"].invoke({"caminho": "../fora-da-raiz"})
    criadas["ler_arquivo"].invoke({"caminho": "src/PedidoController.java"})

    por_nome = telemetria.tools_por_nome()
    assert por_nome["listar_diretorio"].chamadas == 2
    assert por_nome["listar_diretorio"].erros == 1
    assert por_nome["ler_arquivo"].erros == 0
    assert telemetria.caracteres_de_tools() == sum(t.caracteres for t in telemetria.tools)


def test_resumo_para_log_carrega_o_agregado_de_tools(config_falso, backend):
    telemetria = Telemetria()
    tools(config_falso, telemetria)["listar_diretorio"].invoke({"caminho": "src"})

    resumo = telemetria.resumo_para_log()

    assert resumo["tools"] == 1
    assert resumo["por_tool"]["listar_diretorio"]["chamadas"] == 1
    assert resumo["caracteres_de_tools"] > 0


# ---------------------------------------------------------------------------
# Parâmetros do Graphify expostos ao modelo
# ---------------------------------------------------------------------------
#
# `--relation` e `--budget` existiam nos wrappers mas não no schema das tools, então
# o modelo não tinha como usá-los. `--relation inherits` é o caminho determinístico
# para confirmar quem herda de um controller abstrato — a decisão de
# `handlerCompartilhado` dependia de reparar no `extends` da primeira linha.


@pytest.fixture
def grafo(config_falso) -> Path:
    """O wrapper exige o graph.json em disco antes de montar qualquer comando."""
    caminho = config_falso.caminhos.graph_abs
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text('{"nodes": []}', encoding="utf-8")
    return caminho


def capturar_argv(monkeypatch) -> list[list[str]]:
    """Intercepta a montagem do comando, sem chamar o Graphify de verdade."""
    chamadas: list[list[str]] = []

    def falso(argumentos, **_k):
        chamadas.append(argumentos)
        return type("Saida", (), {"codigo": 0, "stdout": "ok", "stderr": "", "texto": "ok"})()

    monkeypatch.setattr(graphify, "executar", falso)
    return chamadas


def test_query_repassa_budget_para_o_cli(config_falso, grafo, monkeypatch):
    chamadas = capturar_argv(monkeypatch)

    tools(config_falso, None)["graphify_query"].invoke(
        {"simbolo": "PedidoController", "budget": 6000}
    )

    assert "--budget" in chamadas[0]
    assert chamadas[0][chamadas[0].index("--budget") + 1] == "6000"


def test_query_sem_budget_nao_passa_a_flag(config_falso, grafo, monkeypatch):
    chamadas = capturar_argv(monkeypatch)
    tools(config_falso, None)["graphify_query"].invoke({"simbolo": "PedidoController"})
    assert "--budget" not in chamadas[0]


def test_affected_repassa_relacao_para_o_cli(config_falso, grafo, monkeypatch):
    chamadas = capturar_argv(monkeypatch)

    tools(config_falso, None)["graphify_affected"].invoke(
        {"entidade": "Pedido", "relacao": "inherits"}
    )

    assert "--relation" in chamadas[0]
    assert chamadas[0][chamadas[0].index("--relation") + 1] == "inherits"


def test_affected_sem_relacao_nao_filtra(config_falso, grafo, monkeypatch):
    # A primeira consulta precisa sair sem filtro: nome de aresta adivinhado devolve
    # vazio com cara de "não há dependente".
    chamadas = capturar_argv(monkeypatch)
    tools(config_falso, None)["graphify_affected"].invoke({"entidade": "Pedido"})
    assert "--relation" not in chamadas[0]
    assert "--depth" in chamadas[0]


def test_os_novos_argumentos_aparecem_na_telemetria(config_falso, grafo, monkeypatch):
    capturar_argv(monkeypatch)
    telemetria = Telemetria()

    tools(config_falso, telemetria)["graphify_affected"].invoke(
        {"entidade": "Pedido", "relacao": "inherits"}
    )

    assert telemetria.tools[0].argumentos == {
        "entidade": "Pedido",
        "depth": 1,
        "relacao": "inherits",
    }
