"""A semente estática e o reparo de schema sem tools do mapeador.

Os dois vêm da mesma medição: o mapeador gastava a maior parte dos tokens
redescobrindo por LLM o que `extrator_de_endpoints` já sabia (e que o Gate A
usava só para reprová-lo), e cada falha de schema virava re-exploração inteira
porque o reparo voltava ao agente com tools carregando 2.000 caracteres do
próprio trabalho. Estes testes fixam os dois comportamentos novos:

* a entrada inicial carrega os endpoints extraídos estaticamente — e degrada
  para a entrada antiga, sem erro, quando o grafo não está preparado;
* o reparo de schema é chamada direta de modelo (sem tools) e recebe o
  artefato malformado inteiro, não um fragmento.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from orquestrador.agentes import mapeador
from orquestrador.analise_estatica.extrator_de_endpoints import (
    ClasseComRotas,
    EndpointsDoBackend,
)
from orquestrador.aplicacao.simulacao import Roteiros
from orquestrador.dominio.inventario import Endpoint
from orquestrador.dominio.recurso import Recurso
from orquestrador.excecoes import GrafoNaoPreparado
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.raiz import DIR_FIXTURES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Semente estática na entrada inicial
# ---------------------------------------------------------------------------


def backend_extraido() -> EndpointsDoBackend:
    return EndpointsDoBackend(
        classes=(
            ClasseComRotas(
                classe="PedidoController",
                arquivo="src/PedidoController.java",
                linguagem="java",
                framework="spring",
                endpoints=(
                    Endpoint(
                        metodo="GET",
                        rota="/api/v1/pedidos",
                        handler="PedidoController.list",
                        arquivo="src/PedidoController.java",
                        linha=14,
                    ),
                ),
            ),
        ),
        arquivos_no_grafo=1,
        arquivos_analisados=1,
    )


def test_entrada_inicial_carrega_os_endpoints_estaticos(config_falso, monkeypatch):
    """O que o Gate A usa para conferir, o mapeador recebe para partir na frente."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: backend_extraido())
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    entrada = mapeador.entrada_inicial(config_falso, recurso)

    assert "GET /api/v1/pedidos" in entrada
    assert "src/PedidoController.java:14" in entrada
    # A instrução de uso viaja junto: sem ela a lista vira gabarito para copiar,
    # e o modelo deixa de confirmar na fonte.
    assert "rotas_dinamicas_nao_resolvidas" in entrada


def test_grafo_nao_preparado_degrada_para_a_entrada_antiga(config_falso, monkeypatch):
    """Semente é aceleração, não pré-condição: sem grafo, o mapeador explora como antes."""

    def explode(**_: Any) -> EndpointsDoBackend:
        raise GrafoNaoPreparado("graph.json não encontrado")

    monkeypatch.setattr(mapeador, "extrair", explode)
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    entrada = mapeador.entrada_inicial(config_falso, recurso)

    assert "Recurso alvo" in entrada
    assert "extraídos estaticamente" not in entrada


def test_extracao_vazia_nao_produz_secao(config_falso, monkeypatch):
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    assert "extraídos estaticamente" not in mapeador.entrada_inicial(config_falso, recurso)


# ---------------------------------------------------------------------------
# Árvore de fontes na entrada inicial
# ---------------------------------------------------------------------------


def test_entrada_inicial_carrega_a_arvore_de_fontes(config_falso, monkeypatch):
    """A fase cara da exploração era descobrir que arquivo existe — a árvore paga isso."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    monkeypatch.setattr(
        mapeador,
        "arquivos_do_grafo",
        lambda _: ["src/A.java", "src/api/B.java", "src/db/V1__init.sql"],
    )
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    entrada = mapeador.entrada_inicial(config_falso, recurso)

    assert "Árvore de fontes" in entrada
    assert "- src/api/B.java" in entrada
    assert "- src/db/V1__init.sql" in entrada


def test_backend_grande_vira_resumo_por_diretorio(config_falso, monkeypatch):
    """Acima do teto, a árvore protege o prefixo: resumo, nunca lista de milhares.

    Informação a menos, nunca errada — o modelo continua com `listar_diretorio`
    para detalhar o que interessar.
    """
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    arquivos = [f"src/modulo{i % 7}/Arquivo{i}.java" for i in range(500)]
    monkeypatch.setattr(mapeador, "arquivos_do_grafo", lambda _: sorted(arquivos))
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    entrada = mapeador.entrada_inicial(config_falso, recurso)

    assert "resumida por diretório" in entrada
    assert "- src/modulo0/ (" in entrada
    assert "Arquivo123.java" not in entrada, "arquivo individual vazou no resumo"


def test_grafo_ausente_nao_produz_arvore(config_falso, monkeypatch):
    def explode(_: Any) -> list[str]:
        raise GrafoNaoPreparado("graph.json não encontrado")

    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    monkeypatch.setattr(mapeador, "arquivos_do_grafo", explode)
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    assert "Árvore de fontes" not in mapeador.entrada_inicial(config_falso, recurso)


# ---------------------------------------------------------------------------
# Reparo de schema: chamada direta, artefato inteiro
# ---------------------------------------------------------------------------


class ModeloDeReparo(BaseChatModel):
    """Devolve `respostas` em ordem e registra QUEM atendeu cada chamada.

    `bind_tools` devolve uma cópia marcada `com_tools=True` que compartilha as
    listas por referência — é assim que o teste distingue a volta que passou
    pelo agente ReAct da chamada direta do reparo.
    """

    respostas: list[str]
    chamadas: list[tuple[bool, list[BaseMessage]]] = Field(default_factory=list)
    contador: list[int] = Field(default_factory=lambda: [0])
    com_tools: bool = False

    @property
    def _llm_type(self) -> str:
        return "reparo-falso"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ModeloDeReparo:
        return self.model_copy(update={"com_tools": True})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        indice = self.contador[0]
        self.contador[0] += 1
        self.chamadas.append((self.com_tools, list(messages)))
        texto = self.respostas[min(indice, len(self.respostas) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=texto))])


MARCADOR_PROFUNDO = "MarcadorProfundo.no_fim_do_artefato"


def artefatos_do_roteiro() -> tuple[str, str]:
    """(malformado LONGO com marcador no fim, válido) a partir da fixture real.

    O malformado precisa passar de 2.000 caracteres com o marcador DEPOIS do
    corte antigo: é isso que separa "recebeu o artefato inteiro" de "recebeu o
    fragmento que causava a re-exploração".
    """
    roteiro = Roteiros(DIR_FIXTURES / "roteiros").carregar("pedidos", "mapeador", 2)
    valido = roteiro["passos"][-1]["artefato"]

    quebrado = json.loads(json.dumps(valido))
    base = quebrado["inventario"]["endpoints"][0]
    quebrado["inventario"]["endpoints"] = [dict(base) for _ in range(40)]
    quebrado["inventario"]["endpoints"][-1]["handler"] = MARCADOR_PROFUNDO
    del quebrado["manifesto"]  # QAORQ-010: campo obrigatório ausente

    malformado = json.dumps(quebrado, ensure_ascii=False)
    assert len(malformado) > 4_000, "o malformado precisa ultrapassar o corte antigo"
    assert malformado.index(MARCADOR_PROFUNDO) > 2_000
    return malformado, json.dumps(valido, ensure_ascii=False)


def test_reparo_de_schema_e_direto_e_recebe_o_artefato_inteiro(config_falso, monkeypatch):
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    malformado, valido = artefatos_do_roteiro()
    modelo = ModeloDeReparo(respostas=[malformado, valido])
    recurso = Recurso(
        nome="pedidos",
        caminho_testes=config_falso.caminhos.recurso("pedidos"),
        raiz_schemas=config_falso.caminhos.dir_schemas_abs,
    )

    saida = mapeador.executar(config_falso, recurso, modelo=modelo, telemetria=Telemetria())

    assert saida.manifesto.recurso == "pedidos"
    assert len(modelo.chamadas) == 2

    com_tools_1, _ = modelo.chamadas[0]
    com_tools_2, mensagens_2 = modelo.chamadas[1]
    # A exploração passa pelo agente (com tools); o reparo de schema, nunca:
    # violação de schema é defeito de forma, e tool ali só reabre a exploração.
    assert com_tools_1 is True
    assert com_tools_2 is False

    entrada_do_reparo = "\n".join(
        str(m.content) for m in mensagens_2 if isinstance(m, HumanMessage)
    )
    # O artefato malformado entra INTEIRO: o marcador mora depois do corte de
    # 2.000 caracteres que fazia o reparo trabalhar sobre 5% do próprio trabalho.
    assert MARCADOR_PROFUNDO in entrada_do_reparo
    assert "QAORQ-010" in entrada_do_reparo


def test_reparo_direto_aparece_na_telemetria(config_falso, monkeypatch):
    """O relatório precisa distinguir exploração de reparo para o custo ser legível."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    malformado, valido = artefatos_do_roteiro()
    telemetria = Telemetria()
    recurso = Recurso(
        nome="pedidos",
        caminho_testes=config_falso.caminhos.recurso("pedidos"),
        raiz_schemas=config_falso.caminhos.dir_schemas_abs,
    )

    mapeador.executar(
        config_falso,
        recurso,
        modelo=ModeloDeReparo(respostas=[malformado, valido]),
        telemetria=telemetria,
    )

    detalhes = [chamada.detalhe for chamada in telemetria.chamadas]
    assert any(detalhe.startswith("schema:1; mensagens:") for detalhe in detalhes)
    assert "schema:2; reparo-direto" in detalhes
