"""Contratos de dados: forma do manifesto e das saídas dos estágios."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from orquestrador.contratos import (
    CATS,
    ArquivoGerado,
    Endpoint,
    EndpointManifesto,
    Inventario,
    Manifesto,
    SaidaExecutor,
    SaidaMapeador,
    normalizar_endpoint,
)


def manifesto_minimo(**extra) -> dict:
    return {
        "recurso": "pedidos",
        "endpoints": [
            {
                "endpoint": "GET /pedidos",
                "cats": ["CAT-01"],
                "naoAplica": {cat: "justificativa suficientemente longa" for cat in CATS[1:]},
            }
        ],
        **extra,
    }


def test_sao_doze_categorias():
    assert len(CATS) == 12
    assert CATS[0] == "CAT-01" and CATS[-1] == "CAT-12"


def test_manifesto_serializa_com_as_chaves_que_o_validador_espera():
    manifesto = Manifesto.model_validate(
        manifesto_minimo(profundidade="contrato", handlerCobertoPor="reason")
    )
    dados = json.loads(manifesto.para_json())

    assert dados["recurso"] == "pedidos"
    assert dados["handlerCobertoPor"] == "reason"
    assert "naoAplica" in dados["endpoints"][0]
    # exclude_none: campo não declarado não aparece no arquivo.
    assert "schemaEntrada" not in dados["endpoints"][0]
    assert "handlerCompartilhado" not in dados


def test_endpoint_fora_da_forma_canonica_e_recusado():
    # QAAPI-024 reprovaria isto no gate; recusar aqui vira delta de schema, mais barato.
    with pytest.raises(ValidationError, match="canônica"):
        EndpointManifesto(endpoint="GET  /pedidos")


def test_endpoint_sem_metodo_ou_sem_rota_e_recusado():
    with pytest.raises(ValidationError):
        EndpointManifesto(endpoint="/pedidos")
    with pytest.raises(ValidationError):
        EndpointManifesto(endpoint="GET pedidos")


def test_categoria_invalida_e_recusada():
    with pytest.raises(ValidationError):
        EndpointManifesto(endpoint="GET /pedidos", cats=["CAT-13"])


def test_manifesto_nao_duplica_endpoint():
    with pytest.raises(ValidationError, match="duplicado"):
        Manifesto(
            recurso="pedidos",
            endpoints=[
                EndpointManifesto(endpoint="GET /pedidos"),
                EndpointManifesto(endpoint="GET /pedidos"),
            ],
        )


def test_contabilidade_das_doze_nao_e_checada_aqui():
    # Decisão de projeto: quem reprova a contabilidade é o validar-suite-gerada.mjs
    # (princípio 4). Duplicar a regra aqui apagaria o Gate A do fluxo.
    incompleto = Manifesto.model_validate(
        {"recurso": "pedidos", "endpoints": [{"endpoint": "GET /pedidos", "cats": ["CAT-01"]}]}
    )
    assert incompleto.endpoints[0].cats == ["CAT-01"]


def test_campo_desconhecido_no_manifesto_e_recusado():
    with pytest.raises(ValidationError):
        Manifesto.model_validate(manifesto_minimo(profundidadee="completa"))


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
        Inventario(
            recurso="pedidos",
            endpoints=[
                {"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"},
                {"metodo": "GET", "rota": "/pedidos", "handler": "b", "arquivo": "y"},
            ],
        )


def test_saida_do_mapeador_exige_o_mesmo_recurso_nos_dois_artefatos():
    with pytest.raises(ValidationError, match="mesmo recurso"):
        SaidaMapeador(
            inventario=Inventario(
                recurso="pedidos",
                endpoints=[
                    {"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"}
                ],
            ),
            manifesto=Manifesto.model_validate(manifesto_minimo(recurso="outro")),
        )


@pytest.mark.parametrize("caminho", ["../fora.js", "/absoluto.js", "C:/absoluto.js", ""])
def test_arquivo_gerado_recusa_caminho_que_escapa_do_recurso(caminho: str):
    with pytest.raises(ValidationError):
        ArquivoGerado(caminho=caminho, conteudo="x")


def test_arquivo_gerado_normaliza_separador_do_windows():
    assert ArquivoGerado(caminho="_support\\api.js", conteudo="x").caminho == "_support/api.js"


def test_saida_do_executor_nao_repete_caminho():
    with pytest.raises(ValidationError, match="repetido"):
        SaidaExecutor(
            recurso="pedidos",
            arquivos=[
                ArquivoGerado(caminho="crud.cy.js", conteudo="a"),
                ArquivoGerado(caminho="crud.cy.js", conteudo="b"),
            ],
        )


def test_normalizar_endpoint_colapsa_espacos():
    assert normalizar_endpoint("  POST   /pedidos/:id  ") == "POST /pedidos/:id"
