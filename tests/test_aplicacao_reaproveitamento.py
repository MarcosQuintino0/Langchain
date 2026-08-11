"""Retomar de uma execução anterior: o que basta, o que falta e o que não combina.

Todo caminho de erro daqui existe para falhar ANTES da primeira chamada de
modelo. Reaproveitar mal e descobrir depois custaria o Bloco 2 inteiro para
produzir uma suíte contra o gabarito errado — que é pior do que não rodar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.aplicacao.reaproveitamento import carregar, resolver_execucao
from orquestrador.dominio.recurso import Recurso
from orquestrador.excecoes import ErroDeConfiguracao

pytestmark = pytest.mark.unit


def manifesto_de(recurso: str) -> dict:
    return {
        "recurso": recurso,
        "endpoints": [
            {
                "endpoint": "GET /pedidos",
                "cats": ["CAT-01"],
                "naoAplica": {
                    f"CAT-{i:02d}": "não há o que testar aqui, comprovadamente"
                    for i in range(2, 13)
                },
            }
        ],
    }


def plano_de(recurso: str) -> dict:
    return {
        "recurso": recurso,
        "endpoints": [
            {
                "endpoint": "GET /pedidos",
                "cenarios": [
                    {
                        "cat": "CAT-01",
                        "nome": "devolve a coleção",
                        "entrada": "GET /pedidos",
                        "espera": "200 com a lista",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def execucao(tmp_path: Path) -> Path:
    destino = tmp_path / "execucoes" / "20260811-000000-1-abc" / "artefatos" / "pedidos"
    destino.mkdir(parents=True)
    (destino / "manifesto.json").write_text(json.dumps(manifesto_de("pedidos")), encoding="utf-8")
    (destino / "plano.json").write_text(json.dumps(plano_de("pedidos")), encoding="utf-8")
    return destino.parent.parent


@pytest.fixture
def recurso(tmp_path: Path) -> Recurso:
    return Recurso(nome="pedidos", caminho_testes=tmp_path / "projeto" / "pedidos")


def test_carrega_o_que_basta_para_o_executor(execucao: Path, recurso: Recurso):
    decisoes = carregar(execucao, recurso)

    assert decisoes.manifesto.recurso == "pedidos"
    assert decisoes.plano.total_de_cenarios() == 1
    # Dossiê e inventário são opcionais: sem eles o executor roda com menos
    # contexto, e o gate continua cobrando o que sempre cobrou.
    assert decisoes.dossie is None
    assert decisoes.inventario is None


def test_gabarito_antigo_e_lido_do_projeto_publicado(execucao: Path, recurso: Recurso):
    # Execuções anteriores a esta versão só gravavam o gabarito no `_support/`
    # publicado. Sem o fallback, nenhuma delas seria reaproveitável.
    (execucao / "artefatos" / "pedidos" / "manifesto.json").unlink()
    publicado = recurso.caminho_testes / "_support"
    publicado.mkdir(parents=True)
    (publicado / "cobertura.json").write_text(json.dumps(manifesto_de("pedidos")), encoding="utf-8")

    assert carregar(execucao, recurso).manifesto.recurso == "pedidos"


def test_plano_ausente_e_erro(execucao: Path, recurso: Recurso):
    (execucao / "artefatos" / "pedidos" / "plano.json").unlink()
    with pytest.raises(ErroDeConfiguracao, match="obrigatório ausente"):
        carregar(execucao, recurso)


def test_plano_e_gabarito_de_recursos_diferentes_e_erro(execucao: Path, recurso: Recurso):
    # O sintoma seria mudo: o executor receberia cenários de um recurso e o
    # gabarito de outro, e produziria uma suíte que nenhum gate sabe julgar.
    (execucao / "artefatos" / "pedidos" / "plano.json").write_text(
        json.dumps(plano_de("clientes")), encoding="utf-8"
    )
    with pytest.raises(ErroDeConfiguracao, match="execuções diferentes"):
        carregar(execucao, recurso)


def test_artefato_corrompido_nao_vira_ausencia_silenciosa(execucao: Path, recurso: Recurso):
    (execucao / "artefatos" / "pedidos" / "plano.json").write_text("{ não é json", encoding="utf-8")
    with pytest.raises(ErroDeConfiguracao, match="não pôde ser lido"):
        carregar(execucao, recurso)


def test_recurso_que_a_execucao_nao_rodou_e_erro(execucao: Path, tmp_path: Path):
    outro = Recurso(nome="clientes", caminho_testes=tmp_path / "projeto" / "clientes")
    with pytest.raises(ErroDeConfiguracao, match="não tem artefatos do recurso"):
        carregar(execucao, outro)


@pytest.mark.parametrize("run_id", ["../fora", "sub/dir", "", "C:/absoluto"])
def test_run_id_nao_pode_escapar_da_raiz_de_saida(tmp_path: Path, run_id: str):
    with pytest.raises(ErroDeConfiguracao):
        resolver_execucao(tmp_path, run_id)


def test_run_id_inexistente_diz_onde_procurou(tmp_path: Path):
    with pytest.raises(ErroDeConfiguracao, match="execucoes listar"):
        resolver_execucao(tmp_path, "20260101-000000-0-nao-existe")
