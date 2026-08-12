"""O gate da norma de código: o que ele reprova e, sobretudo, o que ele não reprova.

Metade destes casos nasceu de um disparo em seco contra a suíte real publicada em
`customers`, antes de o gate ter poder de reprovar. Foi ali que apareceram os três
falsos positivos que estão fixados abaixo — cada um teria mandado o executor
"consertar" código que estava certo, gastando uma tentativa paga por isso.
"""

from __future__ import annotations

import pytest

from orquestrador.gates.padrao_cypress import conferir_padrao

pytestmark = pytest.mark.unit


def codigos(fonte: str, caminho: str = "criar-cliente.cy.js") -> list[str]:
    return [violacao.codigo for violacao in conferir_padrao({caminho: fonte}).violacoes]


SUITE_CONFORME = """/**
 * Cadastro de clientes: o que a API aceita e o que ela recusa.
 *
 * Consumido pelo Cypress; a verificação mora em _support/asserts.js.
 */
import { criarCliente } from "./_support/api.js";
import { validarCadastroRecusado } from "./_support/asserts.js";

describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    // @endpoint POST /api/v1/customers  @cat CAT-02  @campo email
    it("recusa o cadastro sem e-mail", () => {
      criarCliente({ corpo: {} }).then((resposta) => {
        validarCadastroRecusado(resposta, "e-mail é obrigatório");
        expect(resposta.body.erros, "a resposta deve dizer o que faltou").to.have.length(1);
      });
    });
  });
});
"""


def test_suite_conforme_nao_gera_violacao():
    assert codigos(SUITE_CONFORME) == []


def test_dicionario_vazio_aprova():
    # "Não há código" não é "o código está errado" — quem cobra spec ausente é
    # outro gate, e transformar ausência em violação de norma mandaria o executor
    # reescrever um arquivo que ele nem tentou escrever.
    assert conferir_padrao({}).aprovado


# ---------------------------------------------------------------------------
# O que ele reprova
# ---------------------------------------------------------------------------


def test_it_fora_de_context_reprova():
    fonte = """
describe("Criar cliente", () => {
  it("cadastra o cliente", () => {});
});
"""
    assert "QAORQ-070" in codigos(fonte)


def test_context_que_nao_comeca_com_quando_reprova():
    fonte = """
describe("Criar cliente", () => {
  context("POST /api/v1/customers", () => {
    it("cadastra o cliente", () => {});
  });
});
"""
    assert "QAORQ-070" in codigos(fonte)


@pytest.mark.parametrize(
    "titulo",
    [
        "deve cadastrar o cliente",
        "testa o cadastro do cliente",
        "verifica o cadastro",
        "CAT-02 - cadastro sem e-mail",
        "retorna 422 quando falta o e-mail",
        "recusa o cadastro do cliente quando o e-mail está ausente do corpo enviado "
        "pela aplicação chamadora",
    ],
)
def test_titulo_fora_do_padrao_reprova(titulo: str):
    fonte = f"""
describe("Criar cliente", () => {{
  context("quando os dados estão errados", () => {{
    it("{titulo}", () => {{
      expect(1, "mensagem").to.eq(1);
    }});
  }});
}});
"""
    assert "QAORQ-071" in codigos(fonte)


def test_titulo_repetido_no_mesmo_context_reprova():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    it("recusa o cadastro", () => { expect(1, "m").to.eq(1); });
    it("recusa o cadastro", () => { expect(2, "m").to.eq(2); });
  });
});
"""
    assert codigos(fonte).count("QAORQ-071") == 1


def test_expect_sem_mensagem_reprova_tambem_no_support():
    fonte = (
        "// Verificações repetidas do recurso.\n"
        "export function validar(resposta) {\n"
        "  expect(resposta.status).to.eq(201);\n"
        "}\n"
    )
    assert codigos(fonte, "_support/asserts.js") == ["QAORQ-072"]


def test_cy_request_no_spec_reprova():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      cy.request({ method: "POST", url: "/customers" }).then((criacao) => {
        expect(criacao.body.id, "o cadastro devolve o identificador").to.be.a("string");
      });
    });
  });
});
"""
    assert "QAORQ-073" in codigos(fonte)


def test_condicional_e_espera_fixa_dentro_do_teste_reprovam():
    fonte = """
describe("Listar clientes", () => {
  context("quando existem clientes", () => {
    it("devolve a coleção ordenada", () => {
      cy.wait(500);
      listar().then((consulta) => {
        if (consulta.body.items.length > 1) {
          expect(consulta.body.items[0].nome, "a ordenação deve valer").to.eq("Ana");
        }
      });
    });
  });
});
"""
    assert codigos(fonte).count("QAORQ-074") >= 2


def test_credencial_literal_reprova():
    fonte = """
describe("Criar cliente", () => {
  context("quando quem chama tem permissão", () => {
    it("cadastra o cliente", () => {
      const cabecalhos = { Authorization: "Bearer abcdef123456" };
      expect(cabecalhos, "o cabeçalho é montado").to.be.ok;
    });
  });
});
"""
    assert "QAORQ-075" in codigos(fonte)


def test_oraculo_que_so_prova_existencia_reprova():
    fonte = """
describe("Listar clientes", () => {
  context("quando existem clientes", () => {
    it("devolve a coleção", () => {
      listar().then((consulta) => {
        expect(consulta.body.items, "a listagem deve devolver itens").to.be.an("array");
      });
    });
  });
});
"""
    assert "QAORQ-076" in codigos(fonte)


def test_identificador_de_uma_letra_reprova():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      criar().then((r) => {
        expect(r.body.id, "o cadastro devolve o identificador").to.eq("1");
      });
    });
  });
});
"""
    assert "QAORQ-077" in codigos(fonte)


# ---------------------------------------------------------------------------
# Os falsos positivos que o disparo em seco encontrou
# ---------------------------------------------------------------------------


def test_helper_compartilhado_chamado_status_nao_e_leitura_de_status():
    """`BaseAssert.status(resposta, 200, "...")` é o certo, não o errado.

    Era a origem de 62 das 66 violações de camada no primeiro disparo: o regex lia
    a CHAMADA de um verificador compartilhado como se fosse leitura de propriedade.
    """
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      criar().then((criacao) => {
        BaseAssert.status(criacao, 201, "cadastro deve ser aceito");
        expect(criacao.body.nome, "o nome salvo deve ser o enviado").to.eq("Ana");
      });
    });
  });
});
"""
    assert "QAORQ-073" not in codigos(fonte)


def test_status_lido_dentro_de_uma_assercao_nao_reprova():
    """`expect([403, 404], "...").to.include(resposta.status)` continua sendo asserção.

    O `.status` cai depois do fecha-parêntese do `expect`, então conferir só o
    intervalo da chamada acusaria uma asserção legítima. O critério é o statement.
    """
    fonte = """
describe("Excluir cliente", () => {
  context("quando quem chama não tem permissão", () => {
    it("recusa a exclusão", () => {
      excluir().then((resposta) => {
        expect([403, 404], "sem permissão não exclui").to.include(resposta.status);
      });
    });
  });
});
"""
    assert "QAORQ-073" not in codigos(fonte)


def test_token_invalido_de_teste_negativo_nao_e_segredo():
    """`token: "token-invalido-aleatorio"` é massa de teste, escrita como deve ser.

    A primeira versão da regra casava o NOME da variável seguido de literal, e
    acusava exatamente o dado que um teste de autenticação precisa ter.
    """
    fonte = """
describe("Criar cliente", () => {
  context("quando a credencial é inválida", () => {
    it("recusa o cadastro", () => {
      criar({ token: "token-invalido-aleatorio" }).then((resposta) => {
        expect(resposta.body.erro, "a credencial inválida deve ser recusada").to.eq("nao_autenticado");
      });
    });
  });
});
"""
    assert "QAORQ-075" not in codigos(fonte)


def test_cabecalho_montado_de_variavel_nao_e_credencial_literal():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      const cabecalhos = { Authorization: `Bearer ${token}` };
      expect(cabecalhos.Authorization, "o cabeçalho leva a identidade").to.contain("Bearer");
    });
  });
});
"""
    assert "QAORQ-075" not in codigos(fonte)


def test_condicional_fora_do_teste_e_legitima():
    """`helpers.js` precisa de `if` para decidir se registra um id para limpeza.

    A norma proíbe o TESTE escolher o que verifica, não o helper decidir o que
    guardar — e o gate precisa saber a diferença ou reprovaria a limpeza correta.
    """
    fonte = """
export function criarParaTeste(corpo) {
  return criar(corpo).then((criacao) => {
    if (criacao.body && criacao.body.id) {
      registrados.push(criacao.body.id);
    }
    return criacao;
  });
}
"""
    assert "QAORQ-074" not in codigos(fonte, "_support/helpers.js")


def test_uma_letra_numa_expressao_de_uma_linha_nao_reprova():
    """`itens.map((i) => i.externalCode)` é idiomático e não custa nada a quem lê.

    Foram 31 das 38 violações da primeira execução real com a norma nova. A regra
    existe contra `.then((r) => { ...vinte linhas... })`, onde é preciso subir o
    arquivo para lembrar o que é `r` — e essa é a justificativa escrita na norma.
    """
    fonte = """
describe("Listar clientes", () => {
  context("quando existem clientes", () => {
    it("devolve os clientes em ordem alfabética", () => {
      listar().then((consulta) => {
        const nomes = consulta.body.items.map((item) => item.nome);
        expect(nomes, "a ordem deve ser alfabética").to.deep.equal(["Ana", "Bruno"]);
        expect(nomes.some((n) => n === "Zeca"), "não deve vazar outro tenant").to.be.false;
      });
    });
  });
});
"""
    assert "QAORQ-077" not in codigos(fonte)


def test_indice_de_laco_nao_reprova():
    fonte = """
describe("Listar clientes", () => {
  context("quando há mais de uma página", () => {
    it("devolve a segunda página", () => {
      for (let i = 1; i <= 25; i++) {
        criarCliente({ nome: `Cliente ${i}` });
      }
      expect(1, "mensagem explicativa").to.eq(1);
    });
  });
});
"""
    assert "QAORQ-077" not in codigos(fonte)


def test_verificador_compartilhado_conta_como_oraculo():
    """O probe do CAT-04 afirma `lessThan(500)` E chama o verificador de vazamento.

    O contrato daquela categoria pede exatamente isso. O gate conta `expect`, e a
    camada de verificação existe justamente para tirar o `expect` do spec — sem ler
    os imports, a regra condenava o teste por seguir o que lhe foi mandado.
    """
    fonte = """
import { validarNaoVazaInterno } from "./_support/asserts.js";

describe("Criar cliente", () => {
  context("quando o valor é absurdamente grande", () => {
    // @endpoint POST /clientes  @cat CAT-04  @campo email
    it("responde de forma controlada a e-mail com 10000 caracteres", () => {
      criarCliente({ corpo: enorme() }).then((resposta) => {
        expect(resposta.status, "a resposta deve ser controlada, sem 5xx").to.be.lessThan(500);
        validarNaoVazaInterno(resposta);
      });
    });
  });
});
"""
    assert "QAORQ-076" not in codigos(fonte)


def test_sem_o_verificador_o_oraculo_fraco_continua_reprovando():
    # O contraponto do caso acima: sem a segunda prova, `lessThan(500)` sozinho
    # passa com o backend devolvendo qualquer coisa que não seja erro de servidor.
    fonte = """
describe("Criar cliente", () => {
  context("quando o valor é absurdamente grande", () => {
    it("responde de forma controlada a e-mail com 10000 caracteres", () => {
      criarCliente({ corpo: enorme() }).then((resposta) => {
        expect(resposta.status, "a resposta deve ser controlada, sem 5xx").to.be.lessThan(500);
      });
    });
  });
});
"""
    assert "QAORQ-076" in codigos(fonte)


def test_varredura_data_driven_nao_conta_como_titulo_repetido():
    fonte = """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    [
      { campo: "email", esperado: "nulo" },
      { campo: "nome", esperado: "vazio" },
    ].forEach(({ campo, esperado }) => {
      // @endpoint POST /clientes  @cat CAT-03  @campo ${campo}
      it(`recusa o cadastro com ${campo} ${esperado}`, () => {
        expect(1, "mensagem explicativa").to.eq(1);
      });
    });
  });
});
"""
    assert "QAORQ-071" not in codigos(fonte)


# ---------------------------------------------------------------------------
# As réguas que a leitura da primeira suíte publicada pediu
# ---------------------------------------------------------------------------

CABECALHO = "// Testes de criação de cliente.\n"


def test_arquivo_sem_cabecalho_reprova():
    assert "QAORQ-078" in codigos("export function nada() {}\n", "_support/api.js")


def test_cabecalho_em_qualquer_forma_de_comentario_aprova():
    for topo in ("// linha\n", "/** bloco */\n", "/* bloco */\n"):
        assert "QAORQ-078" not in codigos(topo + "export function nada() {}\n", "_support/api.js")


def test_chamada_sem_import_nem_declaracao_reprova():
    """Medido na suíte publicada: cinco chamadas assim em dois specs.

    É `ReferenceError` antes da primeira asserção — o defeito mais barato de achar
    por script e o mais caro de descobrir rodando.
    """
    fonte = (
        CABECALHO
        + """
import { criarCliente } from "./_support/api.js";

describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      criarCliente({}).then((criacao) => {
        validarClienteCadastrado(criacao, {});
        expect(criacao.status, "deve cadastrar").to.equal(201);
      });
    });
  });
});
"""
    )
    violacoes = conferir_padrao({"criar-cliente.cy.js": fonte}).violacoes
    achado = next(v for v in violacoes if v.codigo == "QAORQ-081")
    assert "validarClienteCadastrado" in achado.mensagem
    assert "criarCliente" not in achado.mensagem, "o que está importado não é acusado"


def test_globais_do_cypress_e_do_javascript_nao_sao_acusados():
    fonte = (
        CABECALHO
        + """
describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      const corpo = JSON.parse(String(Number(1)));
      cy.wrap(corpo).then((valor) => {
        expect(valor, "mensagem explicativa").to.be.ok;
      });
    });
  });
});
"""
    )
    assert "QAORQ-081" not in codigos(fonte)


def test_varredura_extensa_sem_tabela_reprova():
    fonte = (
        CABECALHO
        + 'describe("Criar cliente", () => {\n  context("quando os dados estão errados", () => {\n'
    )
    for indice in range(9):
        fonte += f"    // @endpoint POST /clientes  @cat CAT-03  @campo campo{indice}\n"
        fonte += f'    it("recusa o cadastro com campo{indice} inválido", () => {{\n'
        fonte += '      expect(1, "mensagem explicativa").to.eq(1);\n    });\n'
    fonte += "  });\n});\n"
    assert "QAORQ-079" in codigos(fonte)


def test_varredura_extensa_com_tabela_nao_reprova():
    fonte = (
        CABECALHO
        + """
describe("Criar cliente", () => {
  context("quando os dados estão errados", () => {
    [
      { campo: "email", esperado: "nulo" },
      { campo: "nome", esperado: "vazio" },
    ].forEach(({ campo, esperado }) => {
      // @endpoint POST /clientes  @cat CAT-02  @campo ${campo}
      // @cat CAT-03 @cat CAT-04 @cat CAT-02 @cat CAT-03 @cat CAT-04
      // @cat CAT-02 @cat CAT-03 @cat CAT-04 @cat CAT-02
      it(`recusa o cadastro com ${campo} ${esperado}`, () => {
        expect(1, "mensagem explicativa").to.eq(1);
      });
    });
  });
});
"""
    )
    assert "QAORQ-079" not in codigos(fonte)


def test_spec_que_cria_massa_sem_limpeza_reprova():
    fonte = (
        CABECALHO
        + """
import { criarClienteParaTeste } from "./_support/helpers.js";

describe("Criar cliente", () => {
  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      criarClienteParaTeste({}).then((criacao) => {
        expect(criacao.status, "deve cadastrar").to.equal(201);
      });
    });
  });
});
"""
    )
    assert "QAORQ-080" in codigos(fonte)


def test_spec_que_cria_massa_com_limpeza_nao_reprova():
    fonte = (
        CABECALHO
        + """
import { criarClienteParaTeste, limparClientesCriados } from "./_support/helpers.js";

describe("Criar cliente", () => {
  afterEach(() => {
    limparClientesCriados();
  });

  context("quando os dados estão corretos", () => {
    it("cadastra o cliente", () => {
      criarClienteParaTeste({}).then((criacao) => {
        expect(criacao.status, "deve cadastrar").to.equal(201);
      });
    });
  });
});
"""
    )
    assert "QAORQ-080" not in codigos(fonte)
