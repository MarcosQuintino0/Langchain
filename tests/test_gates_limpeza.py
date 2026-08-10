"""A limpeza gerada é conferida, não presumida.

Era o último trecho da promessa "cobertura provada" que seguia sendo esperança:
a receita saía determinística, o executor recebia, e ninguém olhava o código.
Cleanup cego é o defeito mais caro desta suíte porque ele parece funcionar — a
massa se acumula até a unicidade estourar num teste sem relação com a causa.

O eixo destes testes é a assimetria: falso negativo (deixar passar código muito
indireto) é aceito; falso positivo (reprovar suíte correta) não é, porque ele
queima as tentativas do loop de reparo pedindo o que já está lá.
"""

from __future__ import annotations

import pytest

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.gates.limpeza import (
    CODIGO_LIMPEZA_CEGA,
    CODIGO_SEM_LIMPEZA,
    CODIGO_SEM_PRECONDICAO,
    conferir_limpeza,
)

pytestmark = pytest.mark.unit


def inventario(*metodos: str) -> Inventario:
    return Inventario.model_validate(
        {
            "recurso": "customers",
            "endpoints": [
                {
                    "metodo": metodo,
                    "rota": "/customers" if metodo in {"GET", "POST"} else "/customers/{id}",
                    "handler": f"CustomerController.{metodo.lower()}",
                    "arquivo": "src/CustomerController.java",
                    "linha": 10,
                }
                for metodo in metodos
            ],
        }
    )


def dossie_com_precondicao() -> DossieDoRecurso:
    return DossieDoRecurso.model_validate(
        {
            "recurso": "customers",
            "erros": [
                {
                    "endpoint": "DELETE /customers/{id}",
                    "respostas": [
                        {
                            "status": 428,
                            "codigo": "IF_MATCH_REQUIRED",
                            "quando": "If-Match ausente",
                        },
                        {"status": 409, "codigo": "CUSTOMER_IN_USE", "quando": "tem pedido"},
                    ],
                }
            ],
        }
    )


LIMPEZA_CORRETA = """
export function limparCliente(id) {
  return cy.request(`/customers/${id}`).then((leitura) => {
    return cy.request({
      method: 'DELETE',
      url: `/customers/${id}`,
      headers: { 'If-Match': leitura.headers.etag },
      failOnStatusCode: false,
    });
  }).then((resposta) => {
    expect(resposta.status, 'a limpeza precisa confirmar a exclusão').to.eq(204);
  });
}
"""


def test_limpeza_correta_aprova():
    resultado = conferir_limpeza(
        {"api.js": LIMPEZA_CORRETA}, inventario("POST", "DELETE"), dossie_com_precondicao()
    )

    assert resultado.aprovado


def test_recurso_sem_exclusao_nao_cobra_limpeza():
    """Sem rota de exclusão, a receita é o aviso de massa permanente — não código."""
    resultado = conferir_limpeza({"api.js": "export function criar() {}"}, inventario("POST"), None)

    assert resultado.aprovado


def test_exclusao_existe_e_suporte_nao_apaga_nada():
    resultado = conferir_limpeza(
        {"api.js": "export function criar(corpo) { return cy.request({method:'POST'}); }"},
        inventario("POST", "DELETE"),
        None,
    )

    assert resultado.codigos == [CODIGO_SEM_LIMPEZA]


def test_delete_em_comentario_nao_conta_como_limpeza():
    """`// TODO: apagar com DELETE` não implementa nada."""
    resultado = conferir_limpeza(
        {"api.js": "// TODO: limpar com method: 'DELETE' e If-Match\nexport function criar() {}"},
        inventario("POST", "DELETE"),
        None,
    )

    assert resultado.codigos == [CODIGO_SEM_LIMPEZA]


def test_cleanup_com_catch_vazio_e_reprovado():
    codigo = """
    export function limpar(id) {
      try {
        cy.request({ method: 'DELETE', url: `/customers/${id}`, headers: { 'If-Match': '0' } });
      } catch (erro) {}
    }
    """

    resultado = conferir_limpeza({"api.js": codigo}, inventario("DELETE"), None)

    assert CODIGO_LIMPEZA_CEGA in resultado.codigos


def test_cleanup_que_desarma_a_falha_sem_asserção_e_reprovado():
    codigo = """
    export function limpar(id) {
      return cy.request({
        method: 'DELETE',
        url: `/customers/${id}`,
        headers: { 'If-Match': '0' },
        failOnStatusCode: false,
      });
    }
    """

    resultado = conferir_limpeza({"api.js": codigo}, inventario("DELETE"), None)

    assert CODIGO_LIMPEZA_CEGA in resultado.codigos


def test_desarmar_a_falha_com_asserção_e_aceito():
    """`failOnStatusCode: false` é legítimo — é como se trata um 409 esperado."""
    codigo = """
    export function limpar(id) {
      return cy.request({
        method: 'DELETE', url: `/customers/${id}`,
        headers: { 'If-Match': '0' }, failOnStatusCode: false,
      }).then((r) => expect(r.status).to.be.oneOf([204, 409]));
    }
    """

    resultado = conferir_limpeza({"api.js": codigo}, inventario("DELETE"), None)

    assert resultado.aprovado


def test_precondicao_exigida_pelo_contrato_e_ausente_no_codigo():
    """428/412 no contrato é o protocolo dizendo que a exclusão exige condicional."""
    codigo = """
    export function limpar(id) {
      return cy.request({ method: 'DELETE', url: `/customers/${id}` })
        .then((r) => expect(r.status).to.eq(204));
    }
    """

    resultado = conferir_limpeza({"api.js": codigo}, inventario("DELETE"), dossie_com_precondicao())

    assert resultado.codigos == [CODIGO_SEM_PRECONDICAO]
    assert "If-Match" in resultado.violacoes[0].mensagem


def test_sem_precondicao_no_contrato_nao_cobra_cabecalho():
    """A cobrança sai do contrato de erro, nunca de suposição sobre o backend."""
    sem_precondicao = DossieDoRecurso.model_validate(
        {
            "recurso": "customers",
            "erros": [
                {
                    "endpoint": "DELETE /customers/{id}",
                    "respostas": [{"status": 404, "quando": "id inexistente"}],
                }
            ],
        }
    )
    codigo = """
    export function limpar(id) {
      return cy.request({ method: 'DELETE', url: `/customers/${id}` })
        .then((r) => expect(r.status).to.eq(204));
    }
    """

    resultado = conferir_limpeza({"api.js": codigo}, inventario("DELETE"), sem_precondicao)

    assert resultado.aprovado


def test_support_ausente_conta_como_limpeza_ausente():
    """Dicionário vazio não pode virar aprovação por omissão."""
    resultado = conferir_limpeza({}, inventario("POST", "DELETE"), None)

    assert resultado.codigos == [CODIGO_SEM_LIMPEZA]


def test_limpeza_espalhada_entre_modulos_e_reconhecida():
    """O helper pode nascer em helpers.js e a asserção em asserts.js."""
    modulos = {
        "api.js": "export function apagar(id) { return cy.request({ method: 'DELETE',"
        " url: `/x/${id}`, headers: { 'If-Match': '0' }, failOnStatusCode: false }); }",
        "asserts.js": "export function conferirExclusao(r) { expect(r.status).to.eq(204); }",
    }

    resultado = conferir_limpeza(modulos, inventario("DELETE"), dossie_com_precondicao())

    assert resultado.aprovado
