"""Tokens de raciocínio lidos na fronteira com o provedor.

Num modelo de raciocínio, pensamento e resposta dividem o MESMO orçamento de
saída. Quatro execuções reais morreram com 65.536 tokens de saída inteiramente
em raciocínio — e, sem este contador, o relatório mostrava "saída grande", que
é o diagnóstico errado com o número certo. O contador existe para a decisão
(trocar de modelo, limitar o pensamento, não fazer nada) ser tomada com dado.

O cache da entrada tem o seu próprio invariante (`test_invariante_medida_de_cache`);
aqui é o lado da saída.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from orquestrador.llm.mensagens import uso_da_mensagem
from orquestrador.observabilidade.medidas import UsoDeTokens

pytestmark = pytest.mark.unit


def test_rota_compativel_com_openai_dentro_de_completion_tokens_details():
    """O formato da espiral real: 65.536 de saída, 65.536 pensando."""
    mensagem = AIMessage(
        content="",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 220_450,
                "completion_tokens": 65_536,
                "completion_tokens_details": {"reasoning_tokens": 65_536},
            }
        },
    )

    uso = uso_da_mensagem(mensagem)

    assert uso.saida == 65_536
    assert uso.raciocinio == 65_536


def test_usage_metadata_normalizado_do_langchain():
    """Quando o LangChain normaliza, o detalhe vira `output_token_details.reasoning`."""
    mensagem = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 1_000,
            "output_tokens": 900,
            "total_tokens": 1_900,
            "output_token_details": {"reasoning": 700},
        },
    )

    assert uso_da_mensagem(mensagem).raciocinio == 700


def test_modelo_sem_raciocinio_da_zero():
    """Ausência é zero, nunca palpite — zero constante também é informação."""
    mensagem = AIMessage(
        content="ok",
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    )

    assert uso_da_mensagem(mensagem).raciocinio == 0


def test_a_soma_acumula_o_raciocinio():
    """O agregado por estágio precisa somar o pensamento como soma o resto."""
    soma = UsoDeTokens(saida=10, raciocinio=7) + UsoDeTokens(saida=5, raciocinio=3)

    assert (soma.saida, soma.raciocinio) == (15, 10)
