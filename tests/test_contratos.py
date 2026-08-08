"""Contratos de dados: forma do manifesto e das saídas dos estágios."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from orquestrador.contratos import (
    CATS,
    ArquivoGerado,
    Endpoint,
    EndpointManifesto,
    Inventario,
    Manifesto,
    Recurso,
    SaidaExecutor,
    SaidaMapeador,
    caminho_de_schema,
    normalizar_endpoint,
)


def manifesto_minimo(**extra) -> dict:
    return {
        "recurso": "pedidos",
        "endpoints": [
            {
                "endpoint": "GET /pedidos",
                "cats": ["CAT-01"],
                "naoAplica": dict.fromkeys(CATS[1:], "justificativa suficientemente longa"),
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
                endpoints=[{"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"}],
            ),
            manifesto=Manifesto.model_validate(manifesto_minimo(recurso="outro")),
        )


def saida_do_mapeador(schema_entrada: str | None, schemas: list[dict]) -> SaidaMapeador:
    """Saída com um POST que declara (ou não) `schemaEntrada`."""
    endpoint = {"endpoint": "POST /pedidos"}
    if schema_entrada is not None:
        endpoint["schemaEntrada"] = schema_entrada
    return SaidaMapeador(
        inventario=Inventario(
            recurso="pedidos",
            endpoints=[{"metodo": "POST", "rota": "/pedidos", "handler": "criar", "arquivo": "x"}],
        ),
        manifesto=Manifesto.model_validate({"recurso": "pedidos", "endpoints": [endpoint]}),
        schemas=schemas,
    )


SCHEMA_PEDIDO = {"caminho": "pedidos/entidade.schema.json", "conteudo": "{}"}


def test_schema_declarado_sem_arquivo_emitido_e_recusado():
    # QAAPI-027 reprovaria isto no Gate A; recusar aqui vira delta de schema, e o
    # mapeador é o único estágio capaz de consertar.
    with pytest.raises(ValidationError, match="não está"):
        saida_do_mapeador("entidade", [])


def test_nome_simples_resolve_na_pasta_do_recurso():
    saida = saida_do_mapeador("entidade", [SCHEMA_PEDIDO])
    assert saida.schemas[0].caminho == "pedidos/entidade.schema.json"


def test_referencia_ja_pontilhada_pelo_recurso_resolve_no_mesmo_arquivo():
    assert saida_do_mapeador("pedidos/entidade", [SCHEMA_PEDIDO]).schemas


def test_ponteiro_json_escolhe_o_no_nao_o_arquivo():
    # "entidade#/properties/entity" continua exigindo pedidos/entidade.schema.json.
    assert saida_do_mapeador("entidade#/properties/entity", [SCHEMA_PEDIDO]).schemas
    with pytest.raises(ValidationError, match="não está"):
        saida_do_mapeador("entidade#/properties/entity", [])


def test_schema_repetido_e_recusado():
    with pytest.raises(ValidationError, match="repetido"):
        saida_do_mapeador("entidade", [SCHEMA_PEDIDO, dict(SCHEMA_PEDIDO, conteudo="{ }")])


def test_endpoint_sem_schema_entrada_nao_exige_nada():
    assert saida_do_mapeador(None, []).schemas == []


@pytest.mark.parametrize(
    ("referencia", "esperado"),
    [
        ("entidade", "pedidos/entidade.schema.json"),
        ("pedidos/entidade", "pedidos/entidade.schema.json"),
        ("entidade.schema.json", "pedidos/entidade.schema.json"),
        ("entidade#/properties/entity", "pedidos/entidade.schema.json"),
        ("  entidade  ", "pedidos/entidade.schema.json"),
    ],
)
def test_caminho_de_schema_espelha_a_resolucao_da_skill(referencia: str, esperado: str):
    assert caminho_de_schema(referencia, "pedidos") == esperado


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


# ---------------------------------------------------------------------------
# Nome de recurso
# ---------------------------------------------------------------------------
#
# O nome vem cru da CLI (ou da saída de um LLM) e vira diretório por concatenação.
# Sem tipo, `--recurso ..\..\node_modules` é um caminho válido.


@pytest.mark.parametrize("nome", ["pedidos", "nota-fiscal", "v2.pedidos", "nf_e", "a", "com"])
def test_nome_de_recurso_aceita_slug(nome: str):
    assert Recurso(nome=nome, caminho_testes=Path("x")).nome == nome


@pytest.mark.parametrize(
    "nome",
    [
        "..",
        ".",
        "../fora",
        "..\\fora",
        "pedidos/sub",
        "pedidos\\sub",
        "C:/pedidos",
        "Pedidos",
        "nota fiscal",
        "-pedidos",
        ".oculto",
        "pedidos.",
        "pedidos ",
        "",
        "pedidos\x00",
    ],
)
def test_nome_de_recurso_recusa_o_que_vira_caminho(nome: str):
    with pytest.raises(ValidationError):
        Recurso(nome=nome, caminho_testes=Path("x"))


@pytest.mark.parametrize("nome", ["con", "nul", "com1", "lpt9", "aux.json", "prn.schema.json"])
def test_nome_de_recurso_recusa_dispositivo_reservado_do_windows(nome: str):
    # Abrir "NUL" não cria arquivo: o Win32 desvia para o dispositivo, e o erro que
    # sai disso não fala de recurso nem de diretório.
    with pytest.raises(ValidationError, match="reservado"):
        Recurso(nome=nome, caminho_testes=Path("x"))


def test_nome_de_recurso_tambem_vale_para_o_que_o_modelo_emite():
    # `manifesto.recurso` volta ao disco como diretório de schema, e quem o preenche
    # é um LLM — a mesma superfície, um estágio depois.
    with pytest.raises(ValidationError):
        Manifesto.model_validate(manifesto_minimo(recurso="../fora"))
    with pytest.raises(ValidationError):
        Inventario(
            recurso="..",
            endpoints=[{"metodo": "GET", "rota": "/x", "handler": "a", "arquivo": "x"}],
        )
    with pytest.raises(ValidationError):
        SaidaExecutor(recurso="nul", arquivos=[ArquivoGerado(caminho="crud.cy.js", conteudo="x")])
