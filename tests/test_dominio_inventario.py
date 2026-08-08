"""O inventário normaliza o que veio do backend e recusa endpoint repetido."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orquestrador.dominio.inventario import Endpoint, Inventario

pytestmark = pytest.mark.unit


def test_endpoint_do_inventario_normaliza_o_metodo_e_exige_rota_completa():
    endpoint = Endpoint(
        metodo="get", rota="/pedidos", handler="listar", arquivo="Controller.java", linha=12
    )
    assert endpoint.canonico == "GET /pedidos"
    with pytest.raises(ValidationError):
        Endpoint(metodo="GET", rota="pedidos", handler="x", arquivo="y")
    with pytest.raises(ValidationError):
        Endpoint(metodo="FETCH", rota="/pedidos", handler="x", arquivo="y")


def test_inventario_nao_duplica_endpoint():
    with pytest.raises(ValidationError, match="duplicado"):
        Inventario.model_validate(
            {
                "recurso": "pedidos",
                "endpoints": [
                    {"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"},
                    {"metodo": "GET", "rota": "/pedidos", "handler": "b", "arquivo": "y"},
                ],
            }
        )
