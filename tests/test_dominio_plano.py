"""O contrato do plano de cenários e a conferência de completude.

O plano existe para o executor transcrever em vez de decidir; estas invariantes
protegem as duas pontas: nenhuma categoria do gabarito some do plano em silêncio,
e a fatia que cada spec recebe contém exatamente as categorias dele.
"""

from __future__ import annotations

import pytest

from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.plano import (
    Cenario,
    PlanoDeTestes,
    PlanoDoEndpoint,
    cenarios_faltantes,
    conferir_plano,
)

pytestmark = pytest.mark.unit


def cenario(cat: str, nome: str = "caso") -> Cenario:
    return Cenario(cat=cat, nome=nome, entrada="POST com corpo x", espera="201 e releitura")


def parte(endpoint: str, *cats: str) -> PlanoDoEndpoint:
    return PlanoDoEndpoint(
        endpoint=endpoint, cenarios=[cenario(cat, f"caso-{cat}") for cat in cats]
    )


def manifesto_de(*endpoints: tuple[str, list[str]]) -> Manifesto:
    return Manifesto.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [
                {
                    "endpoint": nome,
                    "cats": cats,
                    "naoAplica": {
                        f"CAT-{i:02d}": "não há o que testar aqui, comprovadamente"
                        for i in range(1, 13)
                        if f"CAT-{i:02d}" not in cats
                    },
                }
                for nome, cats in endpoints
            ],
        }
    )


def test_categoria_do_gabarito_sem_cenario_e_apontada():
    plano = parte("GET /pedidos", "CAT-01", "CAT-10")

    assert cenarios_faltantes(plano, ["CAT-01", "CAT-08", "CAT-10"]) == ["CAT-08"]


def test_cenario_a_mais_nao_reprova():
    """Planejar a mais custa um teste; planejar a menos apaga cobertura."""
    plano = parte("GET /pedidos", "CAT-01", "CAT-06", "CAT-10")

    assert cenarios_faltantes(plano, ["CAT-01"]) == []


def test_conferir_plano_acusa_endpoint_sem_secao():
    manifesto = manifesto_de(("GET /pedidos", ["CAT-01"]), ("POST /pedidos", ["CAT-01"]))
    plano = PlanoDeTestes(recurso="pedidos", endpoints=[parte("GET /pedidos", "CAT-01")])

    defeitos = conferir_plano(plano, manifesto)

    assert defeitos == ["endpoint 'POST /pedidos' não tem seção no plano"]


def test_fatia_por_categoria_seleciona_so_o_que_e_do_spec():
    plano = PlanoDeTestes(
        recurso="pedidos",
        endpoints=[
            parte("GET /pedidos", "CAT-01", "CAT-02", "CAT-08"),
            parte("POST /pedidos", "CAT-03"),
        ],
    )

    fatia = plano.cenarios_das_cats(("CAT-02", "CAT-03"))

    assert "caso-CAT-02" in fatia
    assert "caso-CAT-03" in fatia
    assert "caso-CAT-01" not in fatia, "cenário de outro spec vazou para a fatia"
    assert "caso-CAT-08" not in fatia
    # Endpoint sem cenário na fatia não ganha seção vazia.
    assert fatia.count("### ") == 2


def test_render_e_uma_linha_por_cenario_legivel():
    plano = parte("GET /pedidos", "CAT-01")

    texto = plano.render()

    assert "### GET /pedidos" in texto
    assert "- [CAT-01] caso-CAT-01 | entrada: POST com corpo x | espera: 201 e releitura" in texto
