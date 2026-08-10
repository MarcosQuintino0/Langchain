"""O gabarito de cobertura valida a forma, e só a forma.

A contabilidade das 12 categorias fica de fora de propósito — quem reprova isso é
o `validar-suite-gerada.mjs` (princípio 4). `test_contabilidade_das_doze_nao_e_checada_aqui`
fixa essa fronteira: se um dia o Pydantic passar a recusar manifesto incompleto, o
Gate A sai do fluxo sem ninguém perceber."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from orquestrador.dominio.manifesto import CATS, EndpointManifesto, Manifesto

pytestmark = pytest.mark.unit


def test_sao_doze_categorias():
    assert len(CATS) == 12
    assert CATS[0] == "CAT-01" and CATS[-1] == "CAT-12"


def test_manifesto_serializa_com_as_chaves_que_o_validador_espera(manifesto_minimo):
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


def test_campo_desconhecido_no_manifesto_e_recusado(manifesto_minimo):
    with pytest.raises(ValidationError):
        Manifesto.model_validate(manifesto_minimo(profundidadee="completa"))


def test_para_prompt_omite_justificativas_e_para_json_as_preserva():
    """A projeção de prompt tira o naoAplica; a serialização da skill, nunca.

    As justificativas existem para o relatório e para o gate — nos estágios de
    LLM elas eram 1-2k caracteres por chamada sem consumidor. O arquivo em disco
    (para_json) continua íntegro: é ele que o validador .mjs lê.
    """
    manifesto = Manifesto.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [
                {
                    "endpoint": "GET /pedidos",
                    "cats": ["CAT-01"],
                    "naoAplica": {"CAT-12": "a rota devolve JSON e nao recebe arquivo"},
                }
            ],
        }
    )

    assert "nao recebe arquivo" not in manifesto.para_prompt()
    assert "cats" in manifesto.para_prompt()
    assert "nao recebe arquivo" in manifesto.para_json()
