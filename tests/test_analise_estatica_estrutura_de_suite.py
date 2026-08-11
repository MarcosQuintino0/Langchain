"""A árvore describe/context/it: o que o parser lê e o que ele recusa a inventar.

Os casos que importam não são os felizes: são chave dentro de string, bloco
comentado e título em template. Cada um deles, lido errado, produz uma violação
inventada — e violação inventada custa uma volta de reparo paga.
"""

from __future__ import annotations

import pytest

from orquestrador.analise_estatica.estrutura_de_suite import (
    chamadas,
    extrair_estrutura,
    literais,
    neutralizar,
    percorrer,
)

pytestmark = pytest.mark.unit


def test_le_os_tres_niveis_com_titulo_e_linha():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {});
  });
});
"""
    raizes = extrair_estrutura(fonte)

    assert len(raizes) == 1
    describe = raizes[0]
    assert (describe.tipo, describe.titulo, describe.linha) == ("describe", "Criar cliente", 2)
    contexto = describe.filhos[0]
    assert contexto.tipo == "context"
    assert contexto.filhos[0].titulo == "cadastra o cliente"


def test_chave_dentro_de_string_nao_desalinha_o_aninhamento():
    # Contar `{` no fonte cru fecharia o `context` cedo e o `it` viraria irmão dele.
    fonte = """
describe("Criar cliente", () => {
  context("quando o corpo tem { chave aberta", () => {
    it("recusa o cadastro", () => {});
  });
});
"""
    describe = extrair_estrutura(fonte)[0]
    assert describe.filhos[0].filhos[0].titulo == "recusa o cadastro"


def test_bloco_comentado_nao_conta_como_teste():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    // it("teste antigo que ninguém apagou", () => {});
    /* it("outro comentado", () => {}); */
    it("cadastra o cliente", () => {});
  });
});
"""
    testes = [bloco for bloco, _ in percorrer(extrair_estrutura(fonte)) if bloco.tipo == "it"]
    assert [teste.titulo for teste in testes] == ["cadastra o cliente"]


def test_titulo_em_template_e_marcado_como_dinamico():
    # A forma data-driven é legítima. Fingir que se leu o título resolvido acusaria
    # os dois `it` da varredura como se fossem o mesmo nome repetido.
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    [1, 2].forEach((campo) => {
      it(`recusa o cadastro com ${campo} nulo`, () => {});
    });
  });
});
"""
    testes = [bloco for bloco, _ in percorrer(extrair_estrutura(fonte)) if bloco.tipo == "it"]
    assert len(testes) == 1
    assert testes[0].dinamico


def test_objeto_de_opcoes_nao_e_confundido_com_o_corpo():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", { retries: 2 }, () => {
      const resposta = 1;
    });
  });
});
"""
    teste = next(bloco for bloco, _ in percorrer(extrair_estrutura(fonte)) if bloco.tipo == "it")
    assert "const resposta" in fonte[teste.inicio : teste.fim]


def test_ancestrais_vem_do_mais_externo_ao_pai():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {});
  });
});
"""
    achatado = percorrer(extrair_estrutura(fonte))
    teste, ancestrais = next(item for item in achatado if item[0].tipo == "it")
    assert [bloco.tipo for bloco in ancestrais] == ["describe", "context"]
    assert teste.linha == 4


def test_argumentos_separam_no_nivel_de_topo():
    # A vírgula de `.property("email", null)` está dentro de OUTRO par de
    # parênteses: contá-la daria a asserção por explicada.
    neutro = neutralizar('expect(resposta.body).to.have.property("email", null);')
    assert len(chamadas(neutro, "expect")[0].argumentos) == 1

    neutro = neutralizar('expect(resposta.status, "cadastro deve ser recusado").to.eq(422);')
    assert len(chamadas(neutro, "expect")[0].argumentos) == 2


def test_literais_ignoram_aspas_dentro_de_comentario():
    # Sem isso, um `// não use "aspas"` abriria um literal que nunca fecha e o
    # resto do arquivo seria lido como texto.
    fonte = 'const url = "https://exemplo"; // cuidado com "aspas" soltas\nconst n = 1;'
    achados = literais(fonte, neutralizar(fonte))
    assert [conteudo for _, conteudo in achados] == ["https://exemplo"]
