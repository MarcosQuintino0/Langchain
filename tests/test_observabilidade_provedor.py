"""Uma linha por requisição real, inclusive falha, com contexto da fatia."""

from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from orquestrador.observabilidade.provedor import ObservadorDeProvedor
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


class RegistroFalso:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict[str, object]]] = []

    def evento(self, tipo, **campos) -> None:
        self.eventos.append((str(tipo), campos))

    def aviso(self, _texto: str) -> None:
        pass


def test_callback_mede_cada_requisicao_e_atribui_endpoint_e_fatia():
    registro = RegistroFalso()
    telemetria = Telemetria(registro)
    guardas: list[str] = []
    observador = ObservadorDeProvedor(
        telemetria=telemetria,
        registro=registro,
        estagio="executor",
        recurso="pedidos",
        tentativa=2,
        modelo="modelo-configurado",
        endpoint="POST /pedidos",
        fatia="crud.cy.js",
        antes_de_chamar=lambda: guardas.append("ok"),
    )
    run_id = uuid4()

    observador.on_chat_model_start(
        {"kwargs": {"model_name": "modelo-configurado"}},
        [[HumanMessage(content="entrada")]],
        run_id=run_id,
    )
    resposta = AIMessage(
        content="{}",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_token_details": {"cache_read": 6},
            "output_token_details": {"reasoning": 2},
        },
        response_metadata={"finish_reason": "stop", "request_id": "req-123"},
    )
    observador.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=resposta)]]),
        run_id=run_id,
    )

    assert guardas == ["ok"]
    assert [tipo for tipo, _ in registro.eventos] == [
        "requisicao_llm_iniciada",
        "requisicao_llm_concluida",
    ]
    chamada = telemetria.chamadas[0]
    assert chamada.uso.entrada == 10
    assert chamada.uso.cache_lido == 6
    assert chamada.uso.raciocinio == 2
    assert chamada.endpoint == "POST /pedidos"
    assert chamada.fatia == "crud.cy.js"
    assert chamada.request_id == "req-123"


def test_callback_registra_falha_sem_transcrever_a_excecao():
    registro = RegistroFalso()
    observador = ObservadorDeProvedor(
        telemetria=Telemetria(registro),
        registro=registro,
        estagio="mapeador",
        recurso="pedidos",
        tentativa=1,
        modelo="modelo-configurado",
    )
    run_id = uuid4()
    observador.on_chat_model_start({}, [[HumanMessage(content="entrada")]], run_id=run_id)
    observador.on_llm_error(RuntimeError("conteúdo que não deve ir ao log"), run_id=run_id)

    tipo, dados = registro.eventos[-1]
    assert tipo == "requisicao_llm_falhou"
    assert dados["erro_tipo"] == "RuntimeError"
    assert "conteúdo" not in str(dados)
    assert observador.chamadas_finalizadas == 1
