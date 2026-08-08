"""As saídas dos dois estágios: coerência entre as partes e confinamento de caminho.

`caminho_de_schema` espelha `localizarArquivoDeSchema` de
`scripts/cobertura/campos/schema.mjs` na skill. As duas resoluções têm de
coincidir, ou o Gate A cobra um arquivo que o mapeador escreveu com outro nome."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orquestrador.dominio.artefatos import (
    ArquivoGerado,
    ArquivoSchema,
    SaidaExecutor,
    SaidaMapeador,
    caminho_de_schema,
)
from orquestrador.dominio.endpoint import normalizar_endpoint
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto

pytestmark = pytest.mark.unit


def test_saida_do_mapeador_exige_o_mesmo_recurso_nos_dois_artefatos(manifesto_minimo):
    with pytest.raises(ValidationError, match="mesmo recurso"):
        SaidaMapeador(
            inventario=Inventario.model_validate(
                {
                    "recurso": "pedidos",
                    "endpoints": [
                        {"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"}
                    ],
                }
            ),
            manifesto=Manifesto.model_validate(manifesto_minimo(recurso="outro")),
        )


def saida_do_mapeador(schema_entrada: str | None, schemas: list[ArquivoSchema]) -> SaidaMapeador:
    """Saída com um POST que declara (ou não) `schemaEntrada`."""
    endpoint = {"endpoint": "POST /pedidos"}
    if schema_entrada is not None:
        endpoint["schemaEntrada"] = schema_entrada
    return SaidaMapeador(
        inventario=Inventario.model_validate(
            {
                "recurso": "pedidos",
                "endpoints": [
                    {"metodo": "POST", "rota": "/pedidos", "handler": "criar", "arquivo": "x"}
                ],
            }
        ),
        manifesto=Manifesto.model_validate({"recurso": "pedidos", "endpoints": [endpoint]}),
        schemas=schemas,
    )


SCHEMA_PEDIDO = ArquivoSchema(caminho="pedidos/entidade.schema.json", conteudo="{}")


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
        saida_do_mapeador(
            "entidade", [SCHEMA_PEDIDO, SCHEMA_PEDIDO.model_copy(update={"conteudo": "{ }"})]
        )


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
