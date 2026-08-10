"""O planejador: um endpoint por chamada, completude reparada na própria moeda.

O estágio existe para o julgamento sair da escrita — mas só cumpre isso se toda
categoria do gabarito virar cenário. Estes testes fixam o mini-loop de
completude (QAORQ-050), a indexação pelo endpoint DO GABARITO e o fallback de
configuração que impede a seção nova de quebrar config existente.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from orquestrador.agentes import planejador
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.excecoes import RespostaTruncada
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


class ModeloSequencial(BaseChatModel):
    respostas: list[str]
    capturas: list[list[BaseMessage]] = Field(default_factory=list)
    # Índices (base 0) das respostas que saem marcadas como cortadas pelo provedor
    # (finish_reason=length) — é como a espiral de raciocínio chega ao estágio.
    cortadas: set[int] = Field(default_factory=set)

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
        indice = min(len(self.capturas) - 1, len(self.respostas) - 1)
        metadados = {"finish_reason": "length"} if indice in self.cortadas else {}
        mensagem = AIMessage(content=self.respostas[indice], response_metadata=metadados)
        return ChatResult(generations=[ChatGeneration(message=mensagem)])


def manifesto_de_um_endpoint(cats: list[str]) -> Manifesto:
    return Manifesto.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [
                {
                    "endpoint": "GET /pedidos",
                    "cats": cats,
                    "naoAplica": {
                        f"CAT-{i:02d}": "não há o que testar aqui, comprovadamente"
                        for i in range(1, 13)
                        if f"CAT-{i:02d}" not in cats
                    },
                }
            ],
        }
    )


def plano_json(endpoint: str, *cats: str) -> str:
    return json.dumps(
        {
            "endpoint": endpoint,
            "cenarios": [
                {"cat": cat, "nome": f"caso-{cat}", "entrada": "x", "espera": "y"} for cat in cats
            ],
        },
        ensure_ascii=False,
    )


def executar(config, modelo, cats: list[str]):
    recurso = Recurso(nome="pedidos", caminho_testes=config.caminhos.recurso("pedidos"))
    return planejador.executar(
        config,
        recurso,
        manifesto_de_um_endpoint(cats),
        [],
        modelo=modelo,
        telemetria=Telemetria(),
    )


def test_plano_completo_sai_em_uma_chamada_por_endpoint(config_falso):
    modelo = ModeloSequencial(respostas=[plano_json("GET /pedidos", "CAT-01", "CAT-10")])

    plano = executar(config_falso, modelo, ["CAT-01", "CAT-10"])

    assert len(modelo.capturas) == 1
    assert plano.total_de_cenarios() == 2


def test_plano_incompleto_e_reparado_com_qaorq_050(config_falso):
    modelo = ModeloSequencial(
        respostas=[
            plano_json("GET /pedidos", "CAT-01"),
            plano_json("GET /pedidos", "CAT-01", "CAT-10"),
        ]
    )

    plano = executar(config_falso, modelo, ["CAT-01", "CAT-10"])

    assert len(modelo.capturas) == 2, "faltou a volta de reparo"
    entrada_do_reparo = "\n".join(
        str(m.content) for m in modelo.capturas[1] if isinstance(m, HumanMessage)
    )
    assert "QAORQ-050" in entrada_do_reparo
    assert "CAT-10" in entrada_do_reparo
    assert plano.do_endpoint("GET /pedidos") is not None


def test_endpoint_reescrito_pelo_modelo_volta_para_a_grafia_do_gabarito(config_falso):
    modelo = ModeloSequencial(respostas=[plano_json("get /Pedidos/", "CAT-01")])

    plano = executar(config_falso, modelo, ["CAT-01"])

    # O plano é indexado pelo endpoint do GABARITO; grafia própria do modelo
    # tornaria a fatia do executor incapaz de encontrá-lo.
    assert plano.do_endpoint("GET /pedidos") is not None


def test_cats_de_grupos_diferentes_saem_em_chamadas_separadas(config_falso):
    """CAT-01 (crud) e CAT-02 (validações) não cabem no mesmo pedido.

    O fatiamento por grupo é o que mantém a resposta pedida pequena — e o filtro
    descarta cenário de categoria de outro grupo, senão o caso apareceria duas
    vezes no plano quando a chamada dona o planejasse também.
    """
    modelo = ModeloSequencial(
        respostas=[
            plano_json("GET /pedidos", "CAT-01"),
            # A chamada do grupo de validações devolve um intruso de CAT-01.
            plano_json("GET /pedidos", "CAT-02", "CAT-01"),
        ]
    )

    plano = executar(config_falso, modelo, ["CAT-01", "CAT-02"])

    assert len(modelo.capturas) == 2, "cada grupo tem chamada própria"
    parte = plano.do_endpoint("GET /pedidos")
    assert parte is not None
    assert [c.cat for c in parte.cenarios] == ["CAT-01", "CAT-02"], (
        "cenário intruso de outro grupo não foi descartado"
    )
    entrada_da_segunda = "\n".join(
        str(m.content) for m in modelo.capturas[1] if isinstance(m, HumanMessage)
    )
    assert "SOMENTE" in entrada_da_segunda and "CAT-02" in entrada_da_segunda


def test_resposta_cortada_pelo_provedor_e_repetida_uma_unica_vez(config_falso):
    """Exceção mínima ao veto, aprovada em 2026-08-10: um corte = um dado novo.

    A espiral estocástica corta ~1 chamada em 10 com a MESMA entrada que completa
    nas demais; a repetição única converte isso de morte da execução em aviso.
    """
    modelo = ModeloSequencial(
        respostas=[plano_json("GET /pedidos", "CAT-01"), plano_json("GET /pedidos", "CAT-01")],
        cortadas={0},
    )

    plano = executar(config_falso, modelo, ["CAT-01"])

    assert len(modelo.capturas) == 2, "a chamada cortada não foi repetida"
    assert plano.total_de_cenarios() == 1


def test_segundo_corte_seguido_sobe_como_falha_operacional(config_falso):
    """Uma repetição, e exatamente uma: dois cortes seguidos não viram loop."""
    modelo = ModeloSequencial(
        respostas=[plano_json("GET /pedidos", "CAT-01"), plano_json("GET /pedidos", "CAT-01")],
        cortadas={0, 1},
    )

    with pytest.raises(RespostaTruncada):
        executar(config_falso, modelo, ["CAT-01"])

    assert len(modelo.capturas) == 2


def test_config_sem_secao_do_planejador_usa_a_do_mapeador(config_falso):
    """A seção nova não pode quebrar config existente: cai para o mapeador, avisado."""
    assert "planejador" not in config_falso.estagios
    modelo = ModeloSequencial(respostas=[plano_json("GET /pedidos", "CAT-01")])

    plano = executar(config_falso, modelo, ["CAT-01"])

    assert plano.total_de_cenarios() == 1


def test_escrita_sem_releitura_volta_para_reparo_com_qaorq_051(config_falso):
    """Qualidade que vive só em prosa flutua; o QAORQ-051 a torna cobrável."""
    cego = json.dumps(
        {
            "endpoint": "GET /pedidos",
            "cenarios": [
                {"cat": "CAT-01", "nome": "cego", "entrada": "POST /pedidos", "espera": "201"}
            ],
        }
    )
    provado = json.dumps(
        {
            "endpoint": "GET /pedidos",
            "cenarios": [
                {
                    "cat": "CAT-01",
                    "nome": "provado",
                    "entrada": "POST /pedidos",
                    "espera": "201; GET confirma que persistiu",
                }
            ],
        }
    )
    modelo = ModeloSequencial(respostas=[cego, provado])

    plano = executar(config_falso, modelo, ["CAT-01"])

    assert len(modelo.capturas) == 2, "faltou a volta de reparo do QAORQ-051"
    entrada_do_reparo = "\n".join(
        str(m.content) for m in modelo.capturas[1] if isinstance(m, HumanMessage)
    )
    assert "QAORQ-051" in entrada_do_reparo
    parte = plano.do_endpoint("GET /pedidos")
    assert parte is not None
    assert parte.cenarios[0].nome == "provado"


def test_pendencia_heuristica_remanescente_nao_mata_o_estagio(config_falso):
    """Só o QAORQ-050 é fatal: falso positivo de palavra-chave custa aviso, não execução."""
    cego = json.dumps(
        {
            "endpoint": "GET /pedidos",
            "cenarios": [
                {"cat": "CAT-01", "nome": "cego", "entrada": "POST /pedidos", "espera": "201"}
            ],
        }
    )
    modelo = ModeloSequencial(respostas=[cego, cego])

    plano = executar(config_falso, modelo, ["CAT-01"])

    assert len(modelo.capturas) == 2
    assert plano.total_de_cenarios() == 1, "o plano com pendência heurística é aceito"


def test_entrada_nao_carrega_justificativas_e_carrega_o_oraculo_do_grupo():
    """`naoAplica` não participa de decisão nenhuma daqui; o oráculo do grupo sim."""
    item = manifesto_de_um_endpoint(["CAT-01"]).endpoints[0]

    entrada = planejador.entrada_do_endpoint(
        item, [], None, ["CAT-01"], "| Cat | ... |\n| --- | --- |\n| `CAT-01` | linha |"
    )

    assert "não há o que testar aqui" not in entrada, "justificativa de naoAplica vazou"
    assert "Oráculo das categorias desta chamada" in entrada
    assert "| `CAT-01` |" in entrada


def test_oraculo_do_grupo_seleciona_so_as_linhas_pedidas():
    tabela = (
        "prosa fora da tabela\n"
        "| Cat | Cenários | Como |\n"
        "| --- | --- | --- |\n"
        "| `CAT-01` | a | b |\n"
        "| `CAT-02` | c | d |\n"
        "| `CAT-10` | e | f |\n"
    )

    recorte = planejador._oraculo_do_grupo(tabela, ["CAT-01", "CAT-10"])

    assert "| `CAT-01` |" in recorte and "| `CAT-10` |" in recorte
    assert "| `CAT-02` |" not in recorte
    assert recorte.startswith("| Cat |"), "o cabeçalho da tabela precisa acompanhar"
    assert "prosa" not in recorte


class ModeloQueLeAEntrada(BaseChatModel):
    """Responde o que a própria entrada pede — determinístico sob concorrência.

    O `ModeloSequencial` indexa respostas pela ordem de chegada, que em paralelo
    é aleatória; este lê "SOMENTE para: CAT-xx" da entrada e devolve exatamente
    aquelas categorias, então a resposta certa chega à chamada certa em qualquer
    ordem de escalonamento.
    """

    capturas: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "por-conteudo"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.capturas.append(list(messages))
        texto = "\n".join(str(m.content) for m in messages if isinstance(m, HumanMessage))
        pedido = re.search(r"SOMENTE para: ([A-Z0-9, -]+)\.", texto)
        assert pedido is not None
        cats = [cat.strip() for cat in pedido.group(1).split(",")]
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=plano_json("GET /pedidos", *cats)))
            ]
        )


def test_paralelismo_monta_o_plano_na_ordem_canonica(config_falso):
    """Paralelizar muda o relógio, nunca o plano: a montagem segue a ordem das
    tarefas (endpoint do gabarito × ordem de GRUPOS_DE_CATS), não a de chegada."""
    config_falso.estagios["mapeador"].paralelismo = 3
    modelo = ModeloQueLeAEntrada()

    plano = executar(config_falso, modelo, ["CAT-01", "CAT-02", "CAT-08"])

    assert len(modelo.capturas) == 3, "uma chamada por grupo"
    parte = plano.do_endpoint("GET /pedidos")
    assert parte is not None
    assert [c.cat for c in parte.cenarios] == ["CAT-01", "CAT-02", "CAT-08"]
