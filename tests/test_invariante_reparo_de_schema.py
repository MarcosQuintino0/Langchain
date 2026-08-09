"""O reparo de schema não pode apagar a tarefa, e truncamento não é violação.

Por que este arquivo existe
---------------------------
Numa execução real com backend e modelo de verdade, o executor entrou no reparo
com 29.997 caracteres de entrada e saiu dele com **266**. O manifesto, o artefato
e as 65 violações do Gate B tinham sumido: o loop substituía a entrada inteira
pelo texto malformado que o próprio modelo havia produzido, e pedia "produza JSON
válido". As três tentativas foram gastas numa tarefa que já não existia.

A linha tinha o comentário *"Princípio 2, também aqui: só o artefato atual e as
violações"* — uma boa regra aplicada no lugar errado. O princípio diz
`artefato_atual` **lido do disco**; numa violação de schema nada foi para o disco,
porque a saída não validou. Não existe artefato atual ali.

Ao lado disso, a causa da primeira saída malformada: o provedor **cortou** a
resposta por limite de tokens (63.172 de 65.536 gastos em raciocínio, num modelo
que divide o orçamento entre pensar e responder). Isso chegava como `QAORQ-011`,
"não é JSON" — e mandava o modelo consertar o que ele não causou.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import FalhaDeEstagio, RespostaTruncada
from orquestrador.llm.estruturado import exigir_resposta_inteira
from orquestrador.llm.montagem import montar_entrada_reparo_de_schema

pytestmark = pytest.mark.unit

TAREFA = "## Manifesto\n\nGET /api/v1/customers com CAT-01 até CAT-12.\n"
DELTA = Delta(
    estagio="schema",
    recurso="customers",
    violacoes=[Violacao(codigo="QAORQ-011", mensagem="a saída não é um objeto JSON")],
    tentativa=1,
)


# ---------------------------------------------------------------------------
# A tarefa sobrevive ao reparo
# ---------------------------------------------------------------------------


def test_a_tarefa_original_volta_no_reparo():
    """Sem ela, o modelo não sabe mais o que era para construir.

    É o defeito medido: a entrada caiu de 29.997 para 266 caracteres, e as três
    tentativas seguintes pediram algo impossível de atender.
    """
    entrada = montar_entrada_reparo_de_schema(TAREFA, '{"recurso": "cust', DELTA)

    assert "GET /api/v1/customers" in entrada, "a tarefa sumiu do reparo"
    assert '{"recurso": "cust' in entrada, "o modelo precisa ver o que respondeu"
    assert "QAORQ-011" in entrada, "e o que estava errado nela"


def test_o_reparo_nao_encolhe_abaixo_da_tarefa():
    """A entrada do reparo é maior que a tarefa, nunca menor.

    A checagem é grosseira de propósito: ela pega a classe inteira do defeito —
    "o reparo perdeu contexto" — sem depender do formato exato do texto.
    """
    entrada = montar_entrada_reparo_de_schema(TAREFA, "lixo", DELTA)

    assert len(entrada) > len(TAREFA)


def test_o_malformado_e_truncado_para_nao_comer_a_janela():
    """Ele é evidência da forma do erro, não carga.

    Uma resposta cortada pode ter dezenas de milhares de caracteres; reenviá-los
    inteiros gastaria a janela justamente na volta que precisa de espaço para
    responder.
    """
    gigante = "x" * 50_000
    entrada = montar_entrada_reparo_de_schema(TAREFA, gigante, DELTA, limite=500)

    assert len(entrada) < 5_000
    assert "50.000 caracteres no total" in entrada or "50,000 caracteres" in entrada


def test_o_reparo_nao_cresce_com_o_numero_de_tentativas():
    """Princípio 2 continua valendo: nada de histórico acumulado.

    A tarefa é a mesma de sempre e o malformado é só o da última volta. Duas voltas
    seguidas produzem entradas do mesmo tamanho — o que muda é o conteúdo do
    fragmento, não a quantidade dele.
    """
    primeira = montar_entrada_reparo_de_schema(TAREFA, "erro um", DELTA)
    segunda = montar_entrada_reparo_de_schema(TAREFA, "erro dois", DELTA)

    assert abs(len(primeira) - len(segunda)) <= 2


# ---------------------------------------------------------------------------
# Truncamento é operacional, não violação
# ---------------------------------------------------------------------------


def resposta_cortada(**meta: object) -> AIMessage:
    return AIMessage(content='{"recurso": "cust', response_metadata=dict(meta))


@pytest.mark.parametrize("motivo", ["length", "max_tokens", "MAX_TOKENS"])
def test_resposta_cortada_pelo_teto_nao_e_violacao(motivo: str):
    """`RespostaTruncada`, não `QAORQ-011`.

    Os três motivos são o mesmo sinal em provedores diferentes: `length` na API
    compatível com OpenAI, `max_tokens` na Anthropic. Classificar isso como "o
    modelo não sabe fazer JSON" manda o reparo pedir o impossível.
    """
    with pytest.raises(RespostaTruncada) as erro:
        exigir_resposta_inteira(
            resposta_cortada(finish_reason=motivo), estagio="executor", recurso="customers"
        )

    assert "não é erro do modelo" in str(erro.value)


def test_a_mensagem_diz_quanto_foi_gasto_pensando():
    """O número é o diagnóstico. Sem ele, "cortou por limite" não diz o que fazer."""
    with pytest.raises(RespostaTruncada) as erro:
        exigir_resposta_inteira(
            resposta_cortada(
                finish_reason="length",
                token_usage={
                    "completion_tokens": 65_536,
                    "completion_tokens_details": {"reasoning_tokens": 63_172},
                },
            ),
            estagio="executor",
            recurso="customers",
        )

    mensagem = str(erro.value)
    assert "65.536" in mensagem
    assert "63.172" in mensagem and "raciocínio" in mensagem


def test_resposta_inteira_passa_direto():
    exigir_resposta_inteira(
        resposta_cortada(finish_reason="stop"), estagio="executor", recurso="customers"
    )
    exigir_resposta_inteira(None, estagio="executor", recurso="customers")


def test_truncamento_isola_o_recurso_em_vez_de_derrubar_a_execucao():
    """`FalhaDeEstagio`, e não `ErroDeFerramenta`.

    O que estourou foi o tamanho **desta** tarefa; o recurso seguinte pode caber.
    Interromper a execução inteira trataria um limite local como indisponibilidade.
    """
    assert issubclass(RespostaTruncada, FalhaDeEstagio)
