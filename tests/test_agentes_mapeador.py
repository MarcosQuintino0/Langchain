"""O mapeador em duas fases: exploração → notas; serialização fatiada.

O que estes testes fixam veio de medição (2026-08-10): a exploração era barata e
a emissão era o custo — 60% da saída do estágio era raciocínio de serialização,
e um único erro de validação reemitia o bundle inteiro (447s). Daqui para
frente:

* a entrada inicial carrega a semente estática e a árvore de fontes, e degrada
  sem erro quando o grafo não está preparado;
* a exploração termina em notas (texto), nunca em JSON — não existe reparo de
  schema sobre a resposta grande;
* o inventário é montado por CÓDIGO a partir das notas, com fallback de modelo
  só quando a seção combinada não existe;
* cada fatia serializa com o próprio contrato, e o reparo de gate reemite só a
  fatia dona da violação.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from orquestrador.agentes import mapeador
from orquestrador.analise_estatica.extrator_de_endpoints import (
    ClasseComRotas,
    EndpointsDoBackend,
)
from orquestrador.aplicacao.simulacao import Roteiros
from orquestrador.dominio.inventario import Endpoint
from orquestrador.dominio.notas import SECAO_ENDPOINTS, endpoints_das_notas
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import GrafoNaoPreparado
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.raiz import DIR_FIXTURES

pytestmark = pytest.mark.unit


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


# ---------------------------------------------------------------------------
# Semente estática e árvore de fontes na entrada inicial
# ---------------------------------------------------------------------------


def test_entrada_inicial_carrega_os_endpoints_estaticos(config_falso, monkeypatch):
    """O que o Gate A usa para conferir, o mapeador recebe para partir na frente."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: backend_extraido())
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))

    entrada = mapeador.entrada_inicial(config_falso, recurso)

    assert "GET /api/v1/pedidos" in entrada
    assert "src/PedidoController.java:14" in entrada
    # A instrução de uso viaja junto: sem ela a lista vira gabarito para copiar,
    # e o modelo deixa de confirmar na fonte.
    assert "registre nas notas" in entrada


def test_a_linha_da_semente_e_parseavel_pelo_contrato_das_notas(config_falso, monkeypatch):
    """Confirmar um endpoint é copiar a linha — então a linha TEM de parsear.

    É o acoplamento deliberado entre a semente e `dominio/notas.py`: se o render
    mudar de forma, o inventário-por-código morre em silêncio e tudo cai no
    fallback de modelo. Este teste transforma esse acoplamento em quebra visível.
    """
    monkeypatch.setattr(mapeador, "extrair", lambda **_: backend_extraido())
    semente = mapeador._semente_estatica(config_falso)
    linhas_de_endpoint = [
        linha.strip() for linha in semente.splitlines() if linha.lstrip().startswith("- GET")
    ]
    notas = SECAO_ENDPOINTS + "\n" + "\n".join(linhas_de_endpoint)

    (endpoint,) = endpoints_das_notas(notas)
    assert endpoint.canonico == "GET /api/v1/pedidos"
    assert endpoint.arquivo == "src/PedidoController.java"
    assert endpoint.linha == 14


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
    """Acima do teto, a árvore protege o prefixo: resumo, nunca lista de milhares."""
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
# As duas fases, com modelos falsos por fatia
# ---------------------------------------------------------------------------


class ModeloDeFase(BaseChatModel):
    """Devolve `respostas` em ordem e registra as mensagens de cada chamada."""

    respostas: list[str]
    chamadas: list[list[BaseMessage]] = Field(default_factory=list)
    contador: list[int] = Field(default_factory=lambda: [0])
    com_tools: bool = False

    @property
    def _llm_type(self) -> str:
        return "fase-falsa"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ModeloDeFase:
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
        self.chamadas.append(list(messages))
        texto = self.respostas[min(indice, len(self.respostas) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=texto))])


def partes_da_fixture() -> dict[str, Any]:
    roteiros = Roteiros(DIR_FIXTURES / "roteiros")
    return {
        "notas": roteiros.carregar("pedidos", "mapeador", 1)["passos"][-1]["conteudo"],
        "manifesto": roteiros.carregar("pedidos", "mapeador-manifesto", 1)["passos"][-1][
            "artefato"
        ],
        "dossie": roteiros.carregar("pedidos", "mapeador-dossie", 2)["passos"][-1]["artefato"],
        "schemas": roteiros.carregar("pedidos", "mapeador-schemas", 1)["passos"][-1]["artefato"],
    }


def recurso_de(config) -> Recurso:
    return Recurso(
        nome="pedidos",
        caminho_testes=config.caminhos.recurso("pedidos"),
        raiz_schemas=config.caminhos.dir_schemas_abs,
    )


def fabrica_de_fatias(partes: dict[str, Any]) -> tuple[dict[str, ModeloDeFase], Any]:
    modelos = {
        "manifesto": ModeloDeFase(respostas=[json.dumps(partes["manifesto"])]),
        "dossie": ModeloDeFase(respostas=[json.dumps(partes["dossie"])]),
        "schemas": ModeloDeFase(respostas=[json.dumps(partes["schemas"])]),
        "inventario": ModeloDeFase(respostas=["{}"]),
    }
    return modelos, lambda fatia: modelos[fatia]


def test_explora_em_notas_e_serializa_por_fatias(config_falso, monkeypatch):
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    partes = partes_da_fixture()
    exploracao = ModeloDeFase(respostas=[partes["notas"]])
    modelos, fabrica = fabrica_de_fatias(partes)

    produzido = mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=exploracao,
        telemetria=Telemetria(),
        modelo_da_fatia=fabrica,
    )

    # A exploração passou pelo agente (com tools) e devolveu texto, não JSON.
    assert exploracao.chamadas, "a exploração precisa ter acontecido"
    assert produzido.notas == partes["notas"]
    # As fatias de julgamento foram chamadas uma vez cada, sem tools.
    for fatia in ("manifesto", "dossie"):
        assert len(modelos[fatia].chamadas) == 1, fatia
        assert modelos[fatia].com_tools is False
        entrada = "\n".join(str(m.content) for m in modelos[fatia].chamadas[0])
        assert "Notas de descoberta" in entrada
    # Inventário E schemas vieram por código, do parse das notas: as notas da
    # fixture colam o conteúdo completo, então nenhum modelo é pago para copiar.
    assert modelos["inventario"].chamadas == []
    assert modelos["schemas"].chamadas == []
    assert [s.caminho for s in produzido.saida.schemas] == ["pedidos/entidade.schema.json"]
    assert [e.canonico for e in produzido.saida.inventario.endpoints] == [
        "GET /pedidos",
        "POST /pedidos",
    ]
    assert produzido.saida.manifesto.recurso == "pedidos"
    assert produzido.saida.dossie is not None


def test_notas_sem_secao_combinada_caem_no_fallback_de_modelo(config_falso, monkeypatch):
    """Backend fora da matriz estática: o inventário volta a ser fatia de modelo."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    partes = partes_da_fixture()
    inventario_bom = {
        "recurso": "pedidos",
        "endpoints": [
            {
                "metodo": "GET",
                "rota": "/pedidos",
                "handler": "listar",
                "arquivo": "src/app.py",
                "linha": 10,
            }
        ],
    }
    notas_sem_secao = "# Notas\n\n## Regras de negócio\n- RN-01: GET /pedidos devolve tudo"
    modelos, fabrica = fabrica_de_fatias(partes)
    modelos["inventario"] = ModeloDeFase(respostas=[json.dumps(inventario_bom)])

    produzido = mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=ModeloDeFase(respostas=[notas_sem_secao]),
        telemetria=Telemetria(),
        modelo_da_fatia=fabrica,
    )

    assert len(modelos["inventario"].chamadas) == 1
    assert produzido.saida.inventario.endpoints[0].handler == "listar"


def test_reparo_de_gate_reemite_so_a_fatia_dona(config_falso, monkeypatch):
    """QAORQ-062 pertence ao dossiê: manifesto e schemas não são reemitidos."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    partes = partes_da_fixture()
    modelos, fabrica = fabrica_de_fatias(partes)
    exploracao = ModeloDeFase(respostas=[partes["notas"]])

    primeira = mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=exploracao,
        telemetria=Telemetria(),
        modelo_da_fatia=fabrica,
    )
    delta = Delta(
        estagio="gate_a",
        recurso="pedidos",
        violacoes=[Violacao(codigo="QAORQ-062", mensagem="checklist negativa incompleta")],
        tentativa=2,
    )

    chamadas_antes = {fatia: len(modelo.chamadas) for fatia, modelo in modelos.items()}
    reparo = mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=exploracao,
        telemetria=Telemetria(),
        tentativa=2,
        delta=delta,
        notas_anteriores=primeira.notas,
        saida_anterior=primeira.saida,
        modelo_da_fatia=fabrica,
    )

    assert len(modelos["dossie"].chamadas) == chamadas_antes["dossie"] + 1
    assert len(modelos["manifesto"].chamadas) == chamadas_antes["manifesto"]
    assert len(modelos["schemas"].chamadas) == chamadas_antes["schemas"]
    # A exploração NÃO rodou de novo: as notas anteriores são a memória do reparo.
    assert len(exploracao.chamadas) == 1
    assert reparo.notas == primeira.notas

    entrada_do_reparo = "\n".join(str(m.content) for m in modelos["dossie"].chamadas[-1])
    # A fórmula por fatia: notas + fatia atual + violações — e nada de histórico.
    assert "notas-de-descoberta.md" in entrada_do_reparo
    assert "QAORQ-062" in entrada_do_reparo
    assert "fatia atual" in entrada_do_reparo


def test_reparo_frio_sem_notas_re_explora(config_falso, monkeypatch):
    """Delta sem notas anteriores (retomada fria) degrada para exploração completa."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    partes = partes_da_fixture()
    _modelos, fabrica = fabrica_de_fatias(partes)
    exploracao = ModeloDeFase(respostas=[partes["notas"]])
    delta = Delta(
        estagio="gate_a",
        recurso="pedidos",
        violacoes=[Violacao(codigo="QAORQ-062", mensagem="checklist incompleta")],
        tentativa=2,
    )

    mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=exploracao,
        telemetria=Telemetria(),
        tentativa=2,
        delta=delta,
        artefato_atual="ARTEFATO-EM-DISCO",
        modelo_da_fatia=fabrica,
    )

    assert len(exploracao.chamadas) == 1, "a exploração precisa rodar no reparo frio"
    entrada = "\n".join(str(m.content) for m in exploracao.chamadas[0])
    assert "ARTEFATO-EM-DISCO" in entrada
    assert "QAORQ-062" in entrada


def test_fatia_invalida_repara_so_a_propria_fatia(config_falso, monkeypatch):
    """Erro de schema numa fatia fica na fatia: o mini-loop do gerador resolve."""
    monkeypatch.setattr(mapeador, "extrair", lambda **_: EndpointsDoBackend())
    partes = partes_da_fixture()
    modelos, fabrica = fabrica_de_fatias(partes)
    modelos["manifesto"] = ModeloDeFase(
        respostas=["{isso não é json válido", json.dumps(partes["manifesto"])]
    )

    produzido = mapeador.executar(
        config_falso,
        recurso_de(config_falso),
        modelo=ModeloDeFase(respostas=[partes["notas"]]),
        telemetria=Telemetria(),
        modelo_da_fatia=fabrica,
    )

    assert len(modelos["manifesto"].chamadas) == 2, "malformada + reparo"
    assert len(modelos["dossie"].chamadas) == 1, "as outras fatias não pagam o reparo alheio"
    assert produzido.saida.manifesto.recurso == "pedidos"
