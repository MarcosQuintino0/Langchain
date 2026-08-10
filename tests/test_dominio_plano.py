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
    cenarios_sem_prova_de_estado,
    conferir_plano,
    variacoes_excedentes,
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


def test_escrita_sem_prova_de_estado_e_apontada():
    """A régua do QAORQ-051: quem muda estado prova o estado; quem só lê, não."""
    parte = PlanoDoEndpoint(
        endpoint="POST /pedidos",
        cenarios=[
            Cenario(cat="CAT-01", nome="cego", entrada="POST com corpo x", espera="201"),
            Cenario(
                cat="CAT-01",
                nome="provado",
                entrada="POST com corpo x",
                espera="201; GET confirma que persistiu",
            ),
            Cenario(cat="CAT-10", nome="leitura", entrada="GET /pedidos", espera="200 e lista"),
        ],
    )

    assert cenarios_sem_prova_de_estado(parte) == ["cego"]


def test_rejeicao_com_estado_inalterado_conta_como_prova():
    parte = PlanoDoEndpoint(
        endpoint="POST /pedidos",
        cenarios=[
            Cenario(
                cat="CAT-02",
                nome="rejeitado",
                entrada="POST sem campo obrigatório",
                espera="400 VALIDATION_ERROR; nenhum registro criado",
            )
        ],
    )

    assert cenarios_sem_prova_de_estado(parte) == []


def test_variacoes_excedentes_por_campo_e_categoria():
    """A régua do QAORQ-052: acima do limite é repetição, não cobertura."""
    parte = PlanoDoEndpoint(
        endpoint="POST /pedidos",
        cenarios=[
            *(
                Cenario(
                    cat="CAT-03",
                    nome=f"email-{i}",
                    entrada="POST",
                    espera="400; nada criado",
                    campo="email",
                )
                for i in range(4)
            ),
            Cenario(
                cat="CAT-03", nome="nome-1", entrada="POST", espera="400; nada criado", campo="name"
            ),
        ],
    )

    excessos = variacoes_excedentes(parte)

    assert len(excessos) == 1
    assert "'email'" in excessos[0] and "4" in excessos[0]


def test_render_e_uma_linha_por_cenario_legivel():
    plano = parte("GET /pedidos", "CAT-01")

    texto = plano.render()

    assert "### GET /pedidos" in texto
    assert "- [CAT-01] caso-CAT-01 | entrada: POST com corpo x | espera: 201 e releitura" in texto
