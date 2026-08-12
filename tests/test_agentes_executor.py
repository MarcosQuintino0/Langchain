"""O executor fatiado: uma resposta pequena por arquivo, reparo dirigido.

O que estes testes fixam veio de medição: a suíte inteira numa resposta travava
o modelo de raciocínio 4 de 7 vezes; fatiada, zero. As invariantes aqui são as
que impedem o fatiamento de regredir em silêncio:

* geração com plano = uma chamada por fatia, e o filtro descarta arquivo que a
  chamada devolver fora da própria fatia;
* reparo = UMA chamada que nomeia os arquivos apontados pelas violações e aceita
  `SaidaExecutor` parcial (o staging acumula; o gate mede o disco);
* sem plano e sem delta, o caminho antigo de chamada única continua existindo.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from orquestrador.agentes import executor
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.plano import Cenario, PlanoDeTestes, PlanoDoEndpoint
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


class ModeloSequencial(BaseChatModel):
    """Devolve `respostas` em ordem e captura as mensagens de cada chamada."""

    respostas: list[str]
    capturas: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "sequencial-falso"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.capturas.append(list(messages))
        texto = self.respostas[min(len(self.capturas) - 1, len(self.respostas) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=texto))])


def resposta(*arquivos: tuple[str, str]) -> str:
    return json.dumps(
        {
            "recurso": "pedidos",
            "arquivos": [
                {"caminho": caminho, "conteudo": conteudo} for caminho, conteudo in arquivos
            ],
        },
        ensure_ascii=False,
    )


def manifesto_minimo_de_pedidos() -> Manifesto:
    def entrada(endpoint: str, cats: tuple[str, ...]) -> dict[str, Any]:
        return {
            "endpoint": endpoint,
            "cats": list(cats),
            "naoAplica": {
                f"CAT-{i:02d}": "não há o que testar aqui, comprovadamente"
                for i in range(1, 13)
                if f"CAT-{i:02d}" not in cats
            },
        }

    return Manifesto.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [
                entrada("GET /pedidos", ("CAT-01", "CAT-10")),
                entrada("POST /pedidos", ("CAT-01", "CAT-02")),
            ],
        }
    )


def plano_de_pedidos() -> PlanoDeTestes:
    """Dois endpoints, de propósito: é o que deixa ver o spec por operação.

    Com um endpoint só, "uma chamada por operação" e "uma chamada por categoria"
    dariam o mesmo número, e o teste passaria dos dois jeitos.
    """

    def caso(cat: str, endpoint: str) -> Cenario:
        marca = endpoint.split(" ", 1)[0].lower()
        return Cenario(cat=cat, nome=f"caso-{cat}-{marca}", entrada=endpoint, espera="200")

    return PlanoDeTestes(
        recurso="pedidos",
        endpoints=[
            PlanoDoEndpoint(
                endpoint="GET /pedidos",
                cenarios=[caso("CAT-01", "GET /pedidos"), caso("CAT-10", "GET /pedidos")],
            ),
            PlanoDoEndpoint(
                endpoint="POST /pedidos",
                cenarios=[caso("CAT-01", "POST /pedidos"), caso("CAT-02", "POST /pedidos")],
            ),
        ],
    )


def recurso_de(config) -> Recurso:
    caminho = config.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome="pedidos", caminho_testes=caminho)


def executar(config, modelo, **kwargs):
    return executor.executar(
        config,
        recurso_de(config),
        manifesto_minimo_de_pedidos(),
        modelo=modelo,
        telemetria=Telemetria(),
        **kwargs,
    )


def test_a_norma_de_codigo_chega_inteira_na_instrucao(config_falso):
    # A norma mora em arquivo próprio para que o auditor julgue contra exatamente a
    # regra que o executor recebeu. Se a injeção sumir, o executor volta a escrever
    # sem padrão e NADA quebra — nenhum gate percebe a ausência de uma instrução.
    # Por isso a checagem é aqui.
    instrucao = executor.instrucao_do_estagio(config_falso, recurso_de(config_falso))

    assert "{{padrao_de_codigo}}" not in instrucao
    assert "## O nome do teste" in instrucao
    assert "Todo `it` mora dentro de um `context`" in instrucao


def test_geracao_com_plano_e_uma_chamada_por_operacao(config_falso):
    modelo = ModeloSequencial(
        respostas=[
            resposta(("_support/api.js", "// api")),
            resposta(("listar-pedidos.cy.js", "it('a')")),
            resposta(("criar-pedidos.cy.js", "it('b')")),
        ]
    )

    saida = executar(config_falso, modelo, plano=plano_de_pedidos())

    assert len(modelo.capturas) == 3, "uma chamada para _support e uma por operação"
    assert sorted(a.caminho for a in saida.arquivos) == [
        "_support/api.js",
        "criar-pedidos.cy.js",
        "listar-pedidos.cy.js",
    ]
    # A fatia de cada operação leva TODAS as categorias daquele endpoint e nenhuma
    # de outro — é exatamente a troca em relação ao fatiamento por categoria, que
    # espalhava as categorias de um mesmo endpoint por três arquivos.
    entrada_criar = "\n".join(
        str(m.content) for m in modelo.capturas[2] if isinstance(m, HumanMessage)
    )
    assert "caso-CAT-01-post" in entrada_criar
    assert "caso-CAT-02-post" in entrada_criar, "as categorias do endpoint ficam juntas"
    assert "caso-CAT-10-get" not in entrada_criar, "cenário de outra operação vazou"


def test_o_nome_do_spec_vem_do_endpoint_e_nao_do_modelo(config_falso):
    """O modelo pode devolver o caminho que quiser: quem decide o arquivo é o código.

    Sem isso o filtro da fatia não teria contra o que comparar, e duas chamadas
    poderiam reivindicar o mesmo caminho sem ninguém notar no merge.
    """
    modelo = ModeloSequencial(
        respostas=[
            resposta(("_support/api.js", "// api")),
            resposta(("pedidos-listagem.cy.js", "it('nome que o modelo inventou')")),
            resposta(("criar-pedidos.cy.js", "it('b')")),
        ]
    )

    saida = executar(config_falso, modelo, plano=plano_de_pedidos())

    caminhos = {a.caminho for a in saida.arquivos}
    assert "pedidos-listagem.cy.js" not in caminhos, "nome fora da fatia é descartado"
    assert caminhos == {"_support/api.js", "criar-pedidos.cy.js"}


def test_filtro_descarta_arquivo_fora_da_fatia(config_falso):
    """Chamada que 'aproveita' para reescrever outro arquivo não contamina o merge."""
    modelo = ModeloSequencial(
        respostas=[
            resposta(
                ("_support/api.js", "// api"),
                ("listar-pedidos.cy.js", "// INTRUSO na fatia support"),
            ),
            resposta(
                ("listar-pedidos.cy.js", "it('a')"),
                ("_support/api.js", "// INTRUSO na fatia do spec"),
            ),
            resposta(("criar-pedidos.cy.js", "it('b')")),
        ]
    )

    saida = executar(config_falso, modelo, plano=plano_de_pedidos())

    conteudos = {a.caminho: a.conteudo for a in saida.arquivos}
    assert conteudos["_support/api.js"] == "// api"
    assert conteudos["listar-pedidos.cy.js"] == "it('a')"


def test_sem_plano_e_sem_delta_permanece_a_chamada_unica(config_falso):
    modelo = ModeloSequencial(
        respostas=[resposta(("_support/api.js", "// api"), ("crud.cy.js", "it('a')"))]
    )

    saida = executar(config_falso, modelo)

    assert len(modelo.capturas) == 1
    assert len(saida.arquivos) == 2


# ---------------------------------------------------------------------------
# Reparo: o modelo diz o que trocar, o código faz a cirurgia
# ---------------------------------------------------------------------------

SPEC = """// Criação de pedidos.
describe("Criar pedido", () => {
  context("quando os dados estão corretos", () => {
    // @endpoint POST /pedidos  @cat CAT-01
    it("deve criar o pedido", () => {
      expect(resposta.status).to.eq(201);
    });
  });

  context("quando os dados estão errados", () => {
    // @endpoint POST /pedidos  @cat CAT-02  @campo situacao
    it("recusa sem situação", () => {});
  });
});
"""


def reparo(
    trocas: tuple[tuple[str, str, str], ...] = (),
    arquivos: tuple[tuple[str, str], ...] = (),
) -> str:
    return json.dumps(
        {
            "trocas": [
                {"caminho": caminho, "antigo": antigo, "novo": novo}
                for caminho, antigo, novo in trocas
            ],
            "arquivos": [
                {"caminho": caminho, "conteudo": conteudo} for caminho, conteudo in arquivos
            ],
        },
        ensure_ascii=False,
    )


def delta_de(nome: str) -> Delta:
    return Delta(
        estagio="gate_b",
        recurso="pedidos",
        violacoes=[Violacao(codigo="QAORQ-071", arquivo=nome, mensagem="título com 'deve'")],
        tentativa=2,
    )


def consertar(config, modelo, tmp_path, conteudo: str = SPEC):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    (staging / "criar-pedidos.cy.js").write_text(conteudo, encoding="utf-8")
    saida = executar(
        config,
        modelo,
        tentativa=2,
        delta=delta_de("criar-pedidos.cy.js"),
        artefato_atual=conteudo,
        plano=plano_de_pedidos(),
        dir_recurso=staging,
    )
    return saida, staging


def test_a_troca_muda_so_o_trecho_citado(config_falso, tmp_path):
    """O resto do arquivo não é reescrito — é o mesmo texto, byte a byte.

    É o que torna impossível a perda medida em 2026-08-11, quando o reparo por
    reescrita devolveu 20 dos 57 testes de um spec.
    """
    modelo = ModeloSequencial(
        respostas=[
            reparo((("criar-pedidos.cy.js", 'it("deve criar o pedido"', 'it("cria o pedido"'),))
        ]
    )

    saida, _ = consertar(config_falso, modelo, tmp_path)

    conteudo = saida.arquivos[0].conteudo
    assert 'it("cria o pedido"' in conteudo
    assert "deve criar" not in conteudo
    # Tudo que não foi citado continua idêntico.
    assert conteudo == SPEC.replace('it("deve criar o pedido"', 'it("cria o pedido"')


def test_troca_que_nao_casa_e_recusada(config_falso, tmp_path):
    # O modelo parafraseou em vez de copiar. Aplicar por aproximação corromperia o
    # arquivo; recusar devolve o problema ao gate, que torna a cobrar.
    modelo = ModeloSequencial(
        respostas=[reparo((("criar-pedidos.cy.js", "trecho que não existe", "qualquer coisa"),))]
    )

    saida, _ = consertar(config_falso, modelo, tmp_path)

    assert saida.arquivos[0].conteudo == SPEC, "arquivo intocado"


def test_troca_ambigua_e_recusada(config_falso, tmp_path):
    # `});` aparece várias vezes: trocar "a primeira" acertaria o lugar errado.
    modelo = ModeloSequencial(
        respostas=[reparo((("criar-pedidos.cy.js", "});", "}); // marcado"),))]
    )

    saida, _ = consertar(config_falso, modelo, tmp_path)

    assert saida.arquivos[0].conteudo == SPEC, "arquivo intocado"


def test_varias_trocas_sao_aplicadas_em_ordem(config_falso, tmp_path):
    modelo = ModeloSequencial(
        respostas=[
            reparo(
                (
                    ("criar-pedidos.cy.js", 'it("deve criar o pedido"', 'it("cria o pedido"'),
                    (
                        "criar-pedidos.cy.js",
                        "expect(resposta.status).to.eq(201)",
                        'expect(resposta.status, "o pedido deve ser criado").to.eq(201)',
                    ),
                )
            )
        ]
    )

    conteudo = consertar(config_falso, modelo, tmp_path)[0].arquivos[0].conteudo

    assert 'it("cria o pedido"' in conteudo
    assert '"o pedido deve ser criado"' in conteudo


def test_arquivo_completo_continua_valendo_para_acrescimo(config_falso, tmp_path):
    """Violação de AUSÊNCIA não tem trecho antigo para citar.

    "O gabarito prometeu CAT-05 e não existe teste nenhum" só se resolve
    acrescentando — e aí o arquivo inteiro volta, passando pela trava de regressão.
    """
    maior = SPEC.replace(
        "});\n",
        '  // @endpoint POST /pedidos  @cat CAT-05\n  it("recusa corpo vazio", () => {});\n});\n',
        1,
    )
    modelo = ModeloSequencial(respostas=[reparo(arquivos=(("criar-pedidos.cy.js", maior),))])

    saida, _ = consertar(config_falso, modelo, tmp_path)

    assert "CAT-05" in saida.arquivos[0].conteudo


def test_arquivo_completo_que_perde_cobertura_continua_barrado(config_falso, tmp_path):
    menor = """// Criação de pedidos.
describe("Criar pedido", () => {
  context("quando os dados estão corretos", () => {
    // @endpoint POST /pedidos  @cat CAT-01
    it("cria o pedido", () => {});
  });
});
"""
    modelo = ModeloSequencial(respostas=[reparo(arquivos=(("criar-pedidos.cy.js", menor),))])

    saida, _ = consertar(config_falso, modelo, tmp_path)

    assert saida.arquivos[0].conteudo == SPEC, "o que perdeu CAT-02 não chega ao disco"


def test_o_pedido_manda_nao_reescrever(config_falso, tmp_path):
    modelo = ModeloSequencial(
        respostas=[
            reparo((("criar-pedidos.cy.js", 'it("deve criar o pedido"', 'it("cria o pedido"'),))
        ]
    )

    consertar(config_falso, modelo, tmp_path)

    entrada = "\n".join(str(m.content) for m in modelo.capturas[0] if isinstance(m, HumanMessage))
    assert "Não reescreva o arquivo" in entrada
    assert "copiado do arquivo" in entrada, "o antigo tem de ser cópia, não descrição"
    assert "SaidaDeReparo" in entrada, "o schema da chamada é o de troca, não o de geração"
    assert "criar-pedidos.cy.js" in entrada


def test_o_reparo_ve_o_arquivo_inteiro_que_vai_editar(config_falso, tmp_path):
    """Para copiar o trecho, o modelo precisa estar vendo o trecho.

    O recorte compartilhado do delta não garante isso: ele mostra 12 linhas em
    volta da linha citada, e é montado UMA vez para a suíte toda, com teto de 60
    mil caracteres. Uma violação de arquivo inteiro cita a linha 1 — o modelo veria
    só o cabeçalho e teria de adivinhar o resto, e adivinhação não casa.
    """
    longo = SPEC + "\n".join(f"// linha de enchimento {i}" for i in range(400)) + "\n"
    modelo = ModeloSequencial(
        respostas=[
            reparo((("criar-pedidos.cy.js", "// linha de enchimento 399", "// consertado"),))
        ]
    )

    saida, _ = consertar(config_falso, modelo, tmp_path, conteudo=longo)

    entrada = "\n".join(str(m.content) for m in modelo.capturas[0] if isinstance(m, HumanMessage))
    assert "// linha de enchimento 399" in entrada, "o fim do arquivo precisa estar à vista"
    assert "// consertado" in saida.arquivos[0].conteudo
