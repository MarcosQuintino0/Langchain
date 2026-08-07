"""Montagem do delta — princípio 2.

    prompt_reparo = instrucao_fixa_do_estagio + artefato_atual + delta.violacoes

O que estes testes protegem é a ausência: nenhum vestígio de tentativa anterior
pode entrar no prompt de reparo. É esse corte que troca custo quadrático por
custo linear.
"""

from __future__ import annotations

from orquestrador.contratos import Delta, ResultadoGate, Violacao
from orquestrador.montagem import montar_entrada_reparo


def violacao(codigo: str, mensagem: str = "detalhe", **extra) -> Violacao:
    return Violacao(codigo=codigo, mensagem=mensagem, **extra)


def test_delta_carrega_exatamente_as_violacoes_do_gate():
    resultado = ResultadoGate(
        aprovado=False,
        violacoes=[violacao("QAAPI-021"), violacao("QAAPI-022")],
        avisos=[violacao("QAAPI-036")],
    )
    delta = Delta(
        estagio="gate_a", recurso="pedidos", violacoes=resultado.violacoes, tentativa=1
    )
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
            ResultadoGate(aprovado=True, avisos=[violacao("QAORQ-001")]),
            ResultadoGate(aprovado=False, violacoes=[violacao("QAAPI-002")]),
        ],
        gate="gate_a",
    )
    assert combinado.aprovado is False
    assert combinado.codigos == ["QAAPI-002"]
    assert [a.codigo for a in combinado.avisos] == ["QAORQ-001"]


def test_combinar_aprova_quando_todas_aprovam():
    combinado = ResultadoGate.combinar(
        [ResultadoGate(aprovado=True), ResultadoGate(aprovado=True)], gate="gate_b"
    )
    assert combinado.aprovado is True
    assert combinado.gate == "gate_b"
