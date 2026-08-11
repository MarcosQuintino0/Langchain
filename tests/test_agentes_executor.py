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


def test_reparo_e_uma_chamada_que_nomeia_os_arquivos_das_violacoes(config_falso):
    modelo = ModeloSequencial(respostas=[resposta(("seguranca.cy.js", "it('consertado')"))])
    delta = Delta(
        estagio="gate_b",
        recurso="pedidos",
        violacoes=[
            Violacao(codigo="QAAPI-002", arquivo="seguranca.cy.js", mensagem="spec ausente"),
            Violacao(
                codigo="QAAPI-025",
                arquivo="pedidos/_support/cobertura.json",
                mensagem="campo sem teste",
            ),
        ],
        tentativa=2,
    )

    saida = executar(
        config_falso,
        modelo,
        tentativa=2,
        delta=delta,
        artefato_atual="--- crud.cy.js ---\n// atual",
        plano=plano_de_pedidos(),
    )

    assert len(modelo.capturas) == 1, "reparo não refaz a geração fatiada"
    entrada = "\n".join(str(m.content) for m in modelo.capturas[0] if isinstance(m, HumanMessage))
    assert "`seguranca.cy.js`" in entrada, "o reparo nomeia o arquivo apontado"
    # cobertura.json é do mapeador: não pode virar alvo de reescrita do executor.
    assert "cobertura.json`" not in entrada
    assert [a.caminho for a in saida.arquivos] == ["seguranca.cy.js"], "saída parcial é aceita"


def test_reparo_com_varios_arquivos_e_uma_chamada_por_arquivo(config_falso):
    """Medido em 2026-08-11: 6 violações em 3 arquivos numa chamada só pediram a
    reescrita de 3.300 linhas de uma vez. A resposta saiu com 60 mil tokens e JSON
    inválido, e a retentativa foi cortada no teto de 120 mil — a mesma espiral que
    o fatiamento da geração tinha fechado, reaberta pelo reparo.
    """
    modelo = ModeloSequencial(
        respostas=[
            resposta(("criar-pedidos.cy.js", "it('a')")),
            resposta(("listar-pedidos.cy.js", "it('b')")),
        ]
    )
    delta = Delta(
        estagio="gate_b",
        recurso="pedidos",
        violacoes=[
            Violacao(codigo="QAORQ-074", arquivo="criar-pedidos.cy.js", mensagem="condicional"),
            Violacao(codigo="QAORQ-072", arquivo="listar-pedidos.cy.js", mensagem="sem mensagem"),
        ],
        tentativa=2,
    )

    saida = executar(
        config_falso,
        modelo,
        tentativa=2,
        delta=delta,
        artefato_atual="--- criar-pedidos.cy.js ---\n// atual",
        plano=plano_de_pedidos(),
    )

    assert len(modelo.capturas) == 2, "uma chamada por arquivo implicado"
    primeira = "\n".join(str(m.content) for m in modelo.capturas[0] if isinstance(m, HumanMessage))
    assert "`criar-pedidos.cy.js`" in primeira
    # Cada chamada leva SÓ as violações do arquivo dela: mandar as dos outros faria
    # o modelo tentar consertar aqui o que é para consertar lá.
    assert "sem mensagem" not in primeira
    assert sorted(a.caminho for a in saida.arquivos) == [
        "criar-pedidos.cy.js",
        "listar-pedidos.cy.js",
    ]


def test_sem_plano_e_sem_delta_permanece_a_chamada_unica(config_falso):
    modelo = ModeloSequencial(
        respostas=[resposta(("_support/api.js", "// api"), ("crud.cy.js", "it('a')"))]
    )

    saida = executar(config_falso, modelo)

    assert len(modelo.capturas) == 1
    assert len(saida.arquivos) == 2
