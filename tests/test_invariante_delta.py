"""Montagem do delta — princípio 2.

    prompt_reparo = instrucao_fixa_do_estagio + artefato_atual + delta.violacoes

O que estes testes protegem é a ausência: nenhum vestígio de tentativa anterior
pode entrar no prompt de reparo. É esse corte que troca custo quadrático por
custo linear.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from orquestrador.contratos import Delta, ResultadoGate, VereditoDeGate, Violacao
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.llm.montagem import montar_entrada_reparo

pytestmark = pytest.mark.unit


def violacao(codigo: str, mensagem: str = "detalhe", **extra) -> Violacao:
    return Violacao(codigo=codigo, mensagem=mensagem, **extra)


def test_delta_carrega_exatamente_as_violacoes_do_gate():
    resultado = ResultadoGate.reprovado_por(
        [violacao("QAAPI-021"), violacao("QAAPI-022")],
        avisos=[violacao("QAAPI-036")],
    )
    delta = Delta(estagio="gate_a", recurso="pedidos", violacoes=resultado.violacoes, tentativa=1)
    assert [v.codigo for v in delta.violacoes] == ["QAAPI-021", "QAAPI-022"]
    # Aviso não reprova, então não entra no delta.
    assert "QAAPI-036" not in delta.render()


def test_prompt_de_reparo_tem_artefato_e_violacoes_e_nada_mais():
    delta = Delta(
        estagio="gate_b",
        recurso="pedidos",
        violacoes=[violacao("QAAPI-025", "campo situacao sem CAT-03", arquivo="a.json", linha=9)],
        tentativa=2,
    )
    entrada = montar_entrada_reparo("MANIFESTO ATUAL", delta)

    assert "MANIFESTO ATUAL" in entrada
    assert "QAAPI-025" in entrada
    assert "a.json:9" in entrada
    # Nada de histórico: a entrada é curta e não menciona tentativas anteriores.
    assert "tentativa 1" not in entrada
    assert entrada.count("MANIFESTO ATUAL") == 1


def test_prompt_de_reparo_nao_cresce_com_o_numero_de_tentativas():
    # O tamanho da entrada de reparo depende só do artefato e das violações
    # atuais — nunca do número da tentativa. É a checagem que refuta O(n²).
    def entrada_da_tentativa(numero: int) -> str:
        return montar_entrada_reparo(
            "artefato",
            Delta(
                estagio="gate_a",
                recurso="pedidos",
                violacoes=[violacao("QAAPI-021")],
                tentativa=numero,
            ),
        )

    tamanhos = {len(entrada_da_tentativa(numero)) for numero in (1, 2, 3, 9)}
    # A única variação possível é o dígito do número da tentativa no cabeçalho.
    assert max(tamanhos) - min(tamanhos) <= 1


def test_render_da_violacao_localiza_arquivo_e_linha():
    assert violacao("QAAPI-020", "sem tag", arquivo="crud.cy.js", linha=7).render() == (
        "[QAAPI-020] crud.cy.js:7 - sem tag"
    )
    assert violacao("QAAPI-016", "sem spec").render() == "[QAAPI-016] - sem spec"


def test_combinar_reprova_se_qualquer_parte_reprovar():
    combinado = ResultadoGate.combinar(
        [
            ResultadoGate.aprovado_por(avisos=[violacao("QAORQ-001")]),
            ResultadoGate.reprovado_por([violacao("QAAPI-002")]),
        ],
        gate="gate_a",
    )
    assert combinado.aprovado is False
    assert combinado.codigos == ["QAAPI-002"]
    assert [a.codigo for a in combinado.avisos] == ["QAORQ-001"]


def test_combinar_aprova_quando_todas_aprovam():
    combinado = ResultadoGate.combinar(
        [ResultadoGate.aprovado_por(), ResultadoGate.aprovado_por()], gate="gate_b"
    )
    assert combinado.aprovado is True
    assert combinado.gate == "gate_b"


def test_combinar_reprova_com_filho_reprovado_e_sem_violacao():
    # Aprovar por ausência de violação era o defeito: bastava uma checagem reprovar
    # sem conseguir descrever o motivo para o gate inteiro passar.
    combinado = ResultadoGate.combinar(
        [ResultadoGate.aprovado_por(), ResultadoGate.reprovado_por([])], gate="gate_a"
    )
    assert combinado.veredito is VereditoDeGate.REPROVADO
    assert combinado.violacoes == []


def test_aprovado_com_violacao_e_contradicao_recusada_no_modelo():
    # `aprovado_por` nem aceita `violacoes`, mas `combinar` monta o resultado pelo
    # construtor cru: é ele que precisa continuar recusando a combinação.
    with pytest.raises(ValidationError, match="contradição"):
        ResultadoGate(veredito=VereditoDeGate.APROVADO, violacoes=[violacao("QAAPI-002")])


def test_erro_da_ferramenta_domina_a_uniao_e_nao_vira_delta():
    # Uma checagem que não rodou não é compensada por outra que rodou: sem ela, o
    # gate não sabe se o artefato presta.
    combinado = ResultadoGate.combinar(
        [
            ResultadoGate.aprovado_por(),
            ResultadoGate.erro_da_ferramenta("qa-cobertura.mjs mudo", gate="gate_b"),
        ],
        gate="gate_b",
    )
    assert combinado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
    assert combinado.aprovado is False
    assert combinado.violacoes == []
    with pytest.raises(ErroDeFerramenta, match=re.escape("qa-cobertura.mjs mudo")):
        combinado.exigir_veredito()


def test_erro_da_ferramenta_exige_motivo_acionavel():
    # Sem motivo, a interrupção chega a quem opera como "falhou" e nada mais.
    with pytest.raises(ValidationError, match="motivo"):
        ResultadoGate(veredito=VereditoDeGate.ERRO_DA_FERRAMENTA, gate="gate_b")


def test_aprovado_como_argumento_e_recusado_em_vez_de_ignorado():
    # `aprovado=` já foi apelido aceito pelo construtor. Ele saiu porque nenhum
    # verificador estático enxergava a tradução; o risco de sair é `aprovado=False`
    # virar silenciosamente o padrão `APROVADO`. `extra="forbid"` é o que impede.
    with pytest.raises(ValidationError, match="aprovado"):
        ResultadoGate(aprovado=False)  # pyright: ignore[reportCallIssue]


def test_gate_que_reprova_devolve_veredito_sem_interromper():
    reprovado = ResultadoGate.reprovado_por([violacao("QAAPI-025")])
    assert reprovado.exigir_veredito() is reprovado
