"""O contrato do dossiê e as perguntas de completude que ele responde.

O dossiê existe para o planejador afirmar código de erro exato sem tools; estas
invariantes protegem as duas coisas que dão credibilidade a isso: nenhuma regra
entra sem evidência de fonte, e nada citado escapa da grafia canônica que a
fatia por endpoint usa para encontrar o que é de quem.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orquestrador.dominio.dossie import (
    ASPECTOS_NEGATIVOS,
    ConsultaDoEndpoint,
    DossieDoRecurso,
    ErrosDoEndpoint,
    Evidencia,
    Incerteza,
    ParametroDeConsulta,
    RegraDeNegocio,
    RespostaDeErro,
    VerificacaoNegativa,
    aspectos_nao_verificados,
    endpoints_desconhecidos,
)

pytestmark = pytest.mark.unit


def evidencia() -> Evidencia:
    return Evidencia(arquivo="CustomerService.java", linha=30)


def regra(id: str = "RN-01", endpoints: list[str] | None = None, **extras) -> RegraDeNegocio:
    return RegraDeNegocio(
        id=id,
        resumo="externalCode é único no tenant, ignorando maiúsculas",
        efeito="POST com código já existente responde 409 e nada é gravado",
        evidencias=[evidencia()],
        endpoints=endpoints or [],
        **extras,
    )


def erros(endpoint: str = "POST /clientes") -> ErrosDoEndpoint:
    return ErrosDoEndpoint(
        endpoint=endpoint,
        respostas=[
            RespostaDeErro(
                status=409, codigo="CUSTOMER_CODE_EXISTS", quando="código já existe no tenant"
            )
        ],
    )


def test_regra_sem_evidencia_nao_valida():
    with pytest.raises(ValidationError, match="evidencias"):
        RegraDeNegocio(
            id="RN-01",
            resumo="regra sem lastro",
            efeito="qualquer efeito",
            evidencias=[],
        )


@pytest.mark.parametrize("citado", ["POST  /clientes", "post /clientes", "POST clientes"])
def test_endpoint_citado_fora_da_forma_canonica_nao_valida(citado: str):
    """Grafia divergente faria a fatia por endpoint nunca entregar a regra."""
    with pytest.raises(ValidationError):
        regra(endpoints=[citado])


def test_regra_transversal_vale_para_qualquer_endpoint():
    dossie = DossieDoRecurso(
        recurso="clientes",
        regras=[
            regra("RN-01", endpoints=["POST /clientes"]),
            regra("RN-02"),  # transversal: protocolo do recurso inteiro
            regra("RN-03", endpoints=["DELETE /clientes/{id}"]),
        ],
    )

    do_post = dossie.regras_do_endpoint("POST /clientes")

    assert [r.id for r in do_post] == ["RN-01", "RN-02"]
    assert [r.id for r in dossie.regras_transversais] == ["RN-02"]


def test_id_de_regra_repetido_nao_valida():
    with pytest.raises(ValidationError, match="id de regra repetido"):
        DossieDoRecurso(recurso="clientes", regras=[regra("RN-01"), regra("RN-01")])


def test_contrato_de_erro_com_endpoint_repetido_nao_valida():
    with pytest.raises(ValidationError, match="endpoint repetido"):
        DossieDoRecurso(recurso="clientes", erros=[erros(), erros()])


def test_status_de_sucesso_nao_e_contrato_de_erro():
    with pytest.raises(ValidationError):
        RespostaDeErro(status=201, codigo=None, quando="criação bem-sucedida")


def test_fatia_do_endpoint_filtra_o_que_nao_e_dele():
    dossie = DossieDoRecurso(
        recurso="clientes",
        regras=[
            regra("RN-01", endpoints=["POST /clientes"], codigos_de_erro=["CUSTOMER_CODE_EXISTS"]),
            regra("RN-02", endpoints=["DELETE /clientes/{id}"]),
        ],
        erros=[erros("POST /clientes")],
        incertezas=[Incerteza(descricao="comportamento de campo desconhecido no corpo")],
    )

    fatia = dossie.render_para_endpoint("POST /clientes")

    assert "RN-01" in fatia
    assert "409 CUSTOMER_CODE_EXISTS" in fatia
    assert "comportamento de campo desconhecido" in fatia, "incerteza transversal sumiu da fatia"
    assert "RN-02" not in fatia, "regra de outro endpoint vazou para a fatia"


def test_fatia_sem_nada_e_string_vazia():
    """`""` é o contrato com o chamador: seção omitida, não seção vazia."""
    dossie = DossieDoRecurso(recurso="clientes")

    assert dossie.render_para_endpoint("GET /clientes") == ""


def test_consulta_declara_a_lista_como_exaustiva():
    consulta = ConsultaDoEndpoint(
        endpoint="GET /clientes",
        parametros=[
            ParametroDeConsulta(
                nome="size",
                comportamento="tamanho da página; ausente vale 20",
                erro_quando_invalido="400 INVALID_PAGE_SIZE fora de 1..100",
            )
        ],
        parametro_desconhecido="ignorado em silêncio; a resposta vem sem o filtro",
    )

    texto = consulta.render()

    assert "size: tamanho da página" in texto
    assert "inválido: 400 INVALID_PAGE_SIZE" in texto
    assert "não há outro parâmetro" in texto


def test_aspectos_nao_verificados_aponta_o_que_falta_na_ordem_canonica():
    vazio = DossieDoRecurso(recurso="clientes")
    completo = DossieDoRecurso(
        recurso="clientes",
        verificacoes_negativas=[
            VerificacaoNegativa(aspecto=aspecto, resultado="nenhum; vasculhado o serviço")
            for aspecto in ASPECTOS_NEGATIVOS
        ],
    )

    assert aspectos_nao_verificados(vazio) == list(ASPECTOS_NEGATIVOS)
    assert aspectos_nao_verificados(completo) == []


def test_endpoints_desconhecidos_acusa_rota_imaginaria():
    dossie = DossieDoRecurso(
        recurso="clientes",
        regras=[regra("RN-01", endpoints=["POST /clientes", "PUT /clientes/{id}"])],
        erros=[erros("POST /clientes")],
    )

    fantasmas = endpoints_desconhecidos(dossie, ["POST /clientes", "GET /clientes"])

    assert fantasmas == ["PUT /clientes/{id}"]


def test_endpoint_do_gabarito_sem_regra_nao_reprova():
    """Recurso sem regra existe; a assimetria é a mesma do resto do projeto."""
    dossie = DossieDoRecurso(
        recurso="clientes", regras=[regra("RN-01", endpoints=["POST /clientes"])]
    )

    assert endpoints_desconhecidos(dossie, ["POST /clientes", "GET /clientes"]) == []


def test_render_carrega_id_efeito_e_evidencia():
    texto = regra("RN-01", endpoints=["POST /clientes"]).render()

    assert "### RN-01 — externalCode é único" in texto
    assert "- efeito: POST com código já existente responde 409" in texto
    assert "- evidência: CustomerService.java:30" in texto


def test_aliases_camel_case_na_entrada_e_na_saida():
    """O contrato JSON que o LLM emite usa camelCase, como o manifesto."""
    dossie = DossieDoRecurso.model_validate(
        {
            "recurso": "clientes",
            "regras": [
                {
                    "id": "RN-01",
                    "resumo": "código único",
                    "efeito": "409 na duplicidade",
                    "evidencias": [{"arquivo": "CustomerService.java", "linha": 30}],
                    "endpoints": ["POST /clientes"],
                    "codigosDeErro": ["CUSTOMER_CODE_EXISTS"],
                }
            ],
            "verificacoesNegativas": [
                {"aspecto": "campos-derivados", "resultado": "nenhum; resposta espelha o gravado"}
            ],
        }
    )

    assert dossie.regras[0].codigos_de_erro == ["CUSTOMER_CODE_EXISTS"]
    despejo = dossie.model_dump(by_alias=True, exclude_none=True)
    assert despejo["regras"][0]["codigosDeErro"] == ["CUSTOMER_CODE_EXISTS"]
    assert "verificacoesNegativas" in despejo
