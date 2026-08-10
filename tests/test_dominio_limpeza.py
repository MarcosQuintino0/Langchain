"""O veredito de limpeza e as ausências: template dirigido pelo inventário.

O que está sob teste é a decisão pura: com DELETE sai receita (enriquecida pelo
dossiê quando ele existir); sem DELETE sai o aviso de massa permanente — nunca
um cleanup de fachada. Nenhum modelo participa.
"""

from __future__ import annotations

import pytest

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.limpeza import (
    AVISO_SEM_EXCLUSAO,
    metodos_de_escrita_ausentes,
    render_ausencias,
    render_limpeza,
)

pytestmark = pytest.mark.unit


def inventario(*metodos: str) -> Inventario:
    return Inventario.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [
                {
                    "metodo": metodo,
                    "rota": "/pedidos" if metodo in {"GET", "POST"} else "/pedidos/{id}",
                    "handler": f"PedidoController.{metodo.lower()}",
                    "arquivo": "src/PedidoController.java",
                    "linha": 10,
                }
                for metodo in metodos
            ],
        }
    )


def test_sem_delete_o_render_e_o_aviso_de_massa_permanente():
    texto = render_limpeza(inventario("GET", "POST"))

    assert AVISO_SEM_EXCLUSAO in texto
    assert "DELETE" not in texto


def test_com_delete_o_render_e_a_receita():
    texto = render_limpeza(inventario("GET", "POST", "DELETE"))

    assert "`DELETE /pedidos/{id}`" in texto
    assert "reportar" in texto, "a regra de não engolir falha sumiu da receita"
    assert AVISO_SEM_EXCLUSAO not in texto


def test_receita_carrega_regra_transversal_e_erros_da_exclusao_do_dossie():
    dossie = DossieDoRecurso.model_validate(
        {
            "recurso": "pedidos",
            "regras": [
                {
                    "id": "RN-01",
                    "resumo": "toda mutação exige If-Match",
                    "efeito": "DELETE sem If-Match responde 428 e o registro permanece",
                    "evidencias": [{"arquivo": "src/VersionPolicy.java", "linha": 8}],
                },
                {
                    "id": "RN-02",
                    "resumo": "regra só da criação",
                    "efeito": "POST duplicado responde 409",
                    "evidencias": [{"arquivo": "src/PedidoService.java", "linha": 30}],
                    "endpoints": ["POST /pedidos"],
                },
            ],
            "erros": [
                {
                    "endpoint": "DELETE /pedidos/{id}",
                    "respostas": [
                        {"status": 409, "codigo": "PEDIDO_EM_USO", "quando": "há dependente"}
                    ],
                }
            ],
        }
    )

    texto = render_limpeza(inventario("POST", "DELETE"), dossie)

    assert "RN-01" in texto, "regra transversal é pré-requisito da limpeza"
    assert "409 PEDIDO_EM_USO" in texto
    assert "RN-02" not in texto, "regra que não toca a exclusão não é pré-requisito"


def test_ausencias_derivam_do_inventario():
    texto = render_ausencias(inventario("GET", "POST"))

    assert "exatamente 2 endpoint(s)" in texto
    assert "`PUT`" in texto and "`PATCH`" in texto and "`DELETE`" in texto
    assert metodos_de_escrita_ausentes(inventario("GET", "POST")) == ["PUT", "PATCH", "DELETE"]
