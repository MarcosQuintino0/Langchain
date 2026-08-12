"""A reconciliação: o gabarito prometeu, os specs entregaram?

O caso que dá nome a este módulo veio da suíte real publicada em 2026-08-11:
33 de 39 categorias prometidas tinham teste, e as outras seis ninguém tinha visto
faltar — o arquivo com mais lacunas era justamente o MENOR da suíte.
"""

from __future__ import annotations

from typing import Any

import pytest

from orquestrador.dominio.manifesto import Manifesto
from orquestrador.gates.cobertura import conferir_cobertura

pytestmark = pytest.mark.unit

CABECALHO = "// Testes de clientes.\n"


def manifesto_de(**endpoint: Any) -> Manifesto:
    base = {
        "endpoint": "POST /clientes",
        "cats": ["CAT-01", "CAT-02"],
        "naoAplica": {},
        **endpoint,
    }
    cobertas = set(base["cats"]) | set(base["naoAplica"])
    base["naoAplica"] = {
        **base["naoAplica"],
        **{
            f"CAT-{i:02d}": "não há o que testar aqui, comprovadamente"
            for i in range(1, 13)
            if f"CAT-{i:02d}" not in cobertas
        },
    }
    return Manifesto.model_validate({"recurso": "clientes", "endpoints": [base]})


def spec(*tags: str) -> str:
    corpo = CABECALHO + 'describe("Criar cliente", () => {\n'
    corpo += '  context("quando os dados estão corretos", () => {\n'
    for indice, tag in enumerate(tags):
        corpo += f"    // {tag}\n"
        corpo += f'    it("cadastra o cliente {indice}", () => {{}});\n'
    return corpo + "  });\n});\n"


def test_tudo_prometido_entregue_aprova():
    resultado = conferir_cobertura(
        manifesto_de(),
        {
            "criar-clientes.cy.js": spec(
                "@endpoint POST /clientes  @cat CAT-01", "@endpoint POST /clientes  @cat CAT-02"
            )
        },
        {},
    )
    assert resultado.aprovado


def test_categoria_prometida_sem_teste_reprova():
    resultado = conferir_cobertura(
        manifesto_de(),
        {"criar-clientes.cy.js": spec("@endpoint POST /clientes  @cat CAT-01")},
        {},
    )
    assert not resultado.aprovado
    assert [v.codigo for v in resultado.violacoes] == ["QAORQ-030"]
    assert "CAT-02" in resultado.violacoes[0].mensagem


def test_categoria_dispensada_no_gabarito_nao_e_cobrada():
    # `naoAplica` é a dispensa com justificativa escrita pelo mapeador. Cobrá-la
    # seria exigir teste para o que o gabarito já explicou por que não tem.
    resultado = conferir_cobertura(
        manifesto_de(cats=["CAT-01"], naoAplica={"CAT-02": "endpoint não aceita corpo"}),
        {"criar-clientes.cy.js": spec("@endpoint POST /clientes  @cat CAT-01")},
        {},
    )
    assert resultado.aprovado


def test_campo_do_schema_sem_teste_reprova():
    resultado = conferir_cobertura(
        manifesto_de(cats=["CAT-01"], schemaEntrada="create"),
        {"criar-clientes.cy.js": spec("@endpoint POST /clientes  @cat CAT-01  @campo nome")},
        {"create": {"properties": {"nome": {}, "email": {}}}},
    )
    assert [v.codigo for v in resultado.violacoes] == ["QAORQ-082"]
    assert "`email`" in resultado.violacoes[0].mensagem


def test_campo_dispensado_no_gabarito_nao_e_cobrado():
    resultado = conferir_cobertura(
        manifesto_de(
            cats=["CAT-01"],
            schemaEntrada="create",
            campos={"email": "campo opcional sem regra própria"},
        ),
        {"criar-clientes.cy.js": spec("@endpoint POST /clientes  @cat CAT-01  @campo nome")},
        {"create": {"properties": {"nome": {}, "email": {}}}},
    )
    assert resultado.aprovado


def test_varredura_data_driven_conta_como_cobertura():
    """A norma MANDA escrever varredura em tabela, e tabela produz `@campo ${campo}`.

    Sem o resolvedor de `tags_cypress`, esta conta puniria exatamente a forma que a
    outra régua exige — e o executor ficaria entre duas regras incompatíveis.
    """
    fonte = (
        CABECALHO
        + """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    [
      { campo: "nome", esperado: "vazio" },
      { campo: "email", esperado: "nulo" },
    ].forEach(({ campo, esperado }) => {
      // @endpoint POST /clientes  @cat CAT-02  @campo ${campo}
      it(`recusa o cadastro com ${campo} ${esperado}`, () => {});
    });
  });
});
"""
    )
    resultado = conferir_cobertura(
        manifesto_de(cats=["CAT-02"], schemaEntrada="create"),
        {"criar-clientes.cy.js": fonte},
        {"create": {"properties": {"nome": {}, "email": {}}}},
    )
    assert resultado.aprovado


def test_tag_que_nao_resolve_derruba_o_veredito_para_aviso():
    """Conta incompleta não reprova.

    Um par ausente pode estar coberto por uma tag que ninguém conseguiu ler, e
    reprovar sobre isso mandaria o executor reescrever um teste que já existe —
    falso positivo aqui é volta de reparo paga.
    """
    fonte = (
        CABECALHO
        + """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    // @endpoint POST /clientes  @cat ${categoriaVindaDeOutroLugar}
    it("recusa o cadastro", () => {});
  });
});
"""
    )
    resultado = conferir_cobertura(manifesto_de(), {"criar-clientes.cy.js": fonte}, {})

    assert resultado.aprovado, "conta incompleta não reprova"
    assert resultado.avisos, "mas não fica em silêncio"
    assert "não resolvem estaticamente" in resultado.avisos[0].mensagem


def test_sem_gabarito_nao_ha_denominador():
    # Sem gabarito não há o que cobrar; inventar uma régua a partir do que o
    # executor resolveu escrever seria medir a suíte contra ela mesma.
    assert conferir_cobertura(None, {"criar.cy.js": spec()}, {}).aprovado


def test_sem_spec_nao_ha_conta():
    assert conferir_cobertura(manifesto_de(), {}, {}).aprovado
