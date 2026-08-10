"""O planejador: um endpoint por chamada, completude reparada na própria moeda.

O estágio existe para o julgamento sair da escrita — mas só cumpre isso se toda
categoria do gabarito virar cenário. Estes testes fixam o mini-loop de
completude (QAORQ-050), a indexação pelo endpoint DO GABARITO e o fallback de
configuração que impede a seção nova de quebrar config existente.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from orquestrador.agentes import planejador
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


class ModeloSequencial(BaseChatModel):
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


def test_config_sem_secao_do_planejador_usa_a_do_mapeador(config_falso):
    """A seção nova não pode quebrar config existente: cai para o mapeador, avisado."""
    assert "planejador" not in config_falso.estagios
    modelo = ModeloSequencial(respostas=[plano_json("GET /pedidos", "CAT-01")])

    plano = executar(config_falso, modelo, ["CAT-01"])

    assert plano.total_de_cenarios() == 1
