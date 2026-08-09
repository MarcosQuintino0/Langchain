"""O reparo de schema do gerador estruturado revê o próprio trabalho INTEIRO.

Mesma classe de defeito consertada no mapeador, uma camada abaixo: a saída do
executor tem dezenas de KB, e o corte de 2.000 caracteres entregava ao reparo
~5% dela. Medido num artefato real de 35 KB com defeito profundo (chave errada
no último arquivo): o reparo truncado entrou em espiral de raciocínio e não
convergiu; com o artefato inteiro, o modelo devolveu os 7 arquivos
byte-idênticos fora do ponto reclamado, numa volta só.

O corte continua existindo (teto de 60 KB) — a proteção da janela fica; o que
sai é a cegueira sobre o próprio trabalho.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field

from orquestrador.config import ConfigEstagio
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


class Contrato(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recurso: str
    arquivos: list[str]


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


MARCADOR_PROFUNDO = "marcador-profundo-depois-do-corte.js"


def test_reparo_recebe_o_artefato_malformado_inteiro():
    # Malformado LONGO (>2.000 chars) com o defeito e o marcador no FIM: é o que
    # separa "o reparo viu o próprio trabalho" de "recebeu um fragmento".
    malformado = json.dumps(
        {
            "recurso": "products",
            "arquivos": [f"spec-{i:04d}.cy.js" for i in range(200)] + [MARCADOR_PROFUNDO],
            "extra": 1,
        },
        ensure_ascii=False,
    )
    assert len(malformado) > 2_000
    assert malformado.index(MARCADOR_PROFUNDO) > 2_000
    valido = json.dumps({"recurso": "products", "arquivos": ["crud.cy.js"]})

    modelo = ModeloSequencial(respostas=[malformado, valido])
    gerador = GeradorEstruturado(
        modelo=modelo,
        estagio="executor",
        parametros=ConfigEstagio(modelo="fake/executor"),
        telemetria=Telemetria(),
    )

    saida = gerador.gerar(
        Contrato, instrucao="gere o contrato", entrada="TAREFA", recurso="products"
    )

    assert saida.recurso == "products"
    assert len(modelo.capturas) == 2
    entrada_do_reparo = "\n".join(
        str(m.content) for m in modelo.capturas[1] if isinstance(m, HumanMessage)
    )
    assert "TAREFA" in entrada_do_reparo, "a tarefa original precisa voltar no reparo"
    assert MARCADOR_PROFUNDO in entrada_do_reparo, (
        "o reparo precisa ver o próprio trabalho além do corte antigo de 2.000"
    )
