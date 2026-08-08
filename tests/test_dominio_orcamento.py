"""O teto para antes da próxima chamada — e não promete mais que isso.

A decisão aqui é pura: entra `Consumo` e `Limites`, sai um motivo ou `None`. Quem
mede é a telemetria; quem interrompe é o agente. Testar os três juntos exigiria um
pipeline inteiro para provar uma comparação de inteiros.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orquestrador.dominio.orcamento import Consumo, Estimativa, Limites, Orcamento

pytestmark = pytest.mark.unit


def test_sem_teto_nada_para():
    """O padrão é não interromper. Orçamento que aparece sem ninguém pedir é desligado."""
    orcamento = Orcamento()

    assert orcamento.configurado is False
    assert (
        orcamento.motivo_para_parar(
            execucao=Consumo(chamadas=10_000, tokens=10_000_000),
            recurso=Consumo(chamadas=9_999),
        )
        is None
    )


def test_a_estimativa_sozinha_nao_torna_o_orcamento_configurado():
    """Ela é a régua de uma conta, não um teto. Não interrompe nada."""
    assert Orcamento(estimativa=Estimativa(tokens_por_endpoint_min=1)).configurado is False


def test_zero_e_teto_legitimo_e_ausente_nao_e_zero():
    """A distinção que faz `None` existir em vez de um `0` como padrão.

    Zero significa "não me deixe chamar o modelo" e é uma escolha válida. Se campo
    ausente virasse zero, toda execução sem `[orcamento]` morreria na primeira
    tentativa, com uma mensagem sobre um teto que ninguém configurou.
    """
    sem_teto = Limites()
    assert sem_teto.excedido(Consumo(chamadas=5), escopo="recurso") is None

    proibido = Limites(chamadas=0)
    assert proibido.excedido(Consumo(chamadas=0), escopo="recurso") is not None


def test_para_ao_alcancar_o_teto_e_nao_ao_ultrapassar():
    """`>=`, e não `>`. A pergunta é feita ANTES da próxima chamada.

    Com `>`, a checagem só dispararia depois de já ter gasto além do autorizado — o
    que transforma o teto num relatório do estrago.
    """
    limites = Limites(chamadas=3)

    assert limites.excedido(Consumo(chamadas=2), escopo="recurso") is None
    motivo = limites.excedido(Consumo(chamadas=3), escopo="recurso")
    assert motivo is not None and "3 de 3" in motivo


def test_o_escopo_do_recurso_vem_antes_do_da_execucao():
    """O teto mais específico é o mais acionável.

    "teto por recurso alcançado" diz onde olhar; o da execução inteira obriga a
    descobrir qual dos recursos consumiu.
    """
    orcamento = Orcamento(
        por_execucao=Limites(tokens=100),
        por_recurso=Limites(chamadas=1),
    )
    motivo = orcamento.motivo_para_parar(execucao=Consumo(tokens=500), recurso=Consumo(chamadas=1))

    assert motivo is not None and "por recurso" in motivo


@pytest.mark.parametrize(
    ("campo", "rotulo"),
    [
        ("chamadas", "chamadas ao modelo"),
        ("tokens", "tokens"),
        ("caracteres_de_tools", "caracteres devolvidos por tools"),
        ("segundos", "tempo"),
    ],
)
def test_todo_teto_nomeia_a_si_mesmo_na_mensagem(campo: str, rotulo: str):
    """Quem lê o erro precisa saber **qual** teto foi alcançado, não que houve um."""
    motivo = Limites(**{campo: 1}).excedido(Consumo(**{campo: 2}), escopo="execução")

    assert motivo is not None
    assert rotulo in motivo and "execução" in motivo


def test_a_faixa_da_estimativa_precisa_ser_coerente():
    with pytest.raises(ValidationError, match="não pode ser maior"):
        Estimativa(tokens_por_endpoint_min=200_000, tokens_por_endpoint_max=100_000)


def test_consumo_soma():
    total = Consumo(chamadas=1, tokens=10) + Consumo(chamadas=2, segundos=1.5)

    assert (total.chamadas, total.tokens, total.segundos) == (3, 10, 1.5)
