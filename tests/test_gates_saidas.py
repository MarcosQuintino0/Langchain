"""Parsing da saída dos scripts .mjs.

Dois pontos sensíveis:

* o validador escreve no stdout quando aprova e no stderr quando reprova. Um parser
  que lê só stdout enxerga reprovação como saída vazia;
* o contrato do script é exit 0 com `valid: true` e exit 1 com `valid: false`.
  Qualquer outro par é quebra de contrato, e acreditar no JSON aprovaria suíte
  reprovada por um script que já não é o que esperávamos.
"""

from __future__ import annotations

import json

import pytest

from orquestrador.dominio.veredito import VereditoDeGate
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.gates.saidas import (
    resultado_do_validador,
    resumo_da_cobertura,
    violacoes_do_eslint,
)

pytestmark = pytest.mark.unit

APROVADO = json.dumps(
    {
        "valid": True,
        "root": "C:/projeto/cypress/e2e/apis/pedidos",
        "files": 5,
        "specs": 3,
        "errors": [],
        "warnings": [
            {"code": "QAAPI-035", "message": "documento de cobertura", "file": "matriz.md"}
        ],
    }
)

REPROVADO = json.dumps(
    {
        "valid": False,
        "root": "C:/projeto/cypress/e2e/apis/pedidos",
        "files": 4,
        "specs": 2,
        "manifestOnly": True,
        "errors": [
            {
                "code": "QAAPI-021",
                "message": "categoria(s) nao contabilizada(s) em POST /pedidos: CAT-05",
                "file": "_support/cobertura.json",
                "line": 24,
            },
            {"code": "QAAPI-002", "message": "spec-base ausente", "file": "seguranca.cy.js"},
        ],
        "warnings": [],
    }
)


def test_aprovacao_vem_pelo_stdout(saida_de_processo):
    resultado = resultado_do_validador(saida_de_processo(codigo=0, stdout=APROVADO), gate="gate_a")
    assert resultado.aprovado is True
    assert resultado.violacoes == []
    assert [aviso.codigo for aviso in resultado.avisos] == ["QAAPI-035"]


def test_reprovacao_vem_pelo_stderr(saida_de_processo):
    resultado = resultado_do_validador(
        saida_de_processo(codigo=1, stdout="", stderr=REPROVADO), gate="gate_a"
    )
    assert resultado.aprovado is False
    assert resultado.codigos == ["QAAPI-021", "QAAPI-002"]
    primeira = resultado.violacoes[0]
    assert primeira.arquivo == "_support/cobertura.json"
    assert primeira.linha == 24
    assert "CAT-05" in primeira.mensagem


def test_saida_bruta_guarda_os_dois_fluxos(saida_de_processo):
    resultado = resultado_do_validador(
        saida_de_processo(codigo=1, stdout="ruído", stderr=REPROVADO), gate="gate_a"
    )
    assert "ruído" in resultado.saida_bruta
    assert "QAAPI-021" in resultado.saida_bruta


def erro_de_ferramenta(resultado) -> bool:
    """Erro de ferramenta é o que nunca pode virar delta: sem veredito, sem violação."""
    return (
        resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
        and resultado.violacoes == []
        and resultado.motivo != ""
    )


def test_exit_2_e_erro_da_ferramenta_nao_delta(saida_de_processo):
    # Exit 2 é erro de uso NOSSO: o artefato não foi julgado. Mandá-lo ao modelo
    # como violação gastaria tentativa num script que sequer chegou a rodar.
    resultado = resultado_do_validador(
        saida_de_processo(codigo=2, stderr="ERRO DE USO: opcao desconhecida"),
        gate="gate_a",
    )
    assert erro_de_ferramenta(resultado)
    with pytest.raises(ErroDeFerramenta, match="exit 2"):
        resultado.exigir_veredito()


def test_saida_sem_json_e_erro_da_ferramenta(saida_de_processo):
    resultado = resultado_do_validador(
        saida_de_processo(codigo=1, stderr="SUITE INVALIDA: ..."), gate="gate_b"
    )
    assert erro_de_ferramenta(resultado)


@pytest.mark.parametrize(
    ("codigo", "corpo"),
    [
        (1, APROVADO),  # disse que passou, mas saiu como quem reprovou
        (0, REPROVADO),  # disse que reprovou, mas saiu como quem passou
        (3, APROVADO),  # código fora do contrato
    ],
)
def test_exit_incompativel_com_valid_e_quebra_de_contrato(
    saida_de_processo, codigo: int, corpo: str
):
    resultado = resultado_do_validador(
        saida_de_processo(codigo=codigo, stdout=corpo), gate="gate_a"
    )
    assert erro_de_ferramenta(resultado)


def test_valid_true_com_erros_listados_e_quebra_de_contrato(saida_de_processo):
    # Não dá para escolher em quem acreditar, e escolher o `valid` aprovaria uma
    # suíte que o próprio script acabou de descrever como quebrada.
    corpo = json.dumps({"valid": True, "errors": [{"code": "QAAPI-021", "message": "x"}]})
    resultado = resultado_do_validador(saida_de_processo(codigo=0, stdout=corpo), gate="gate_a")
    assert erro_de_ferramenta(resultado)


def test_reprovacao_sem_erro_descrito_continua_reprovando(saida_de_processo):
    # Reprovar sem conseguir dizer o motivo é reprovação, nunca aprovação por
    # ausência de violação.
    corpo = json.dumps({"valid": False, "errors": []})
    resultado = resultado_do_validador(saida_de_processo(codigo=1, stderr=corpo), gate="gate_a")
    assert resultado.veredito is VereditoDeGate.REPROVADO
    assert resultado.violacoes == []


def test_extrair_json_tolera_ruido_em_volta():
    assert extrair_json('aviso\n{"valid": true}\n') == {"valid": True}


def test_resumo_da_cobertura_vazio_quando_nao_ha_json(saida_de_processo):
    # qa-cobertura.mjs sai 0 mesmo quando falha: a ausência do JSON é o único sinal.
    assert resumo_da_cobertura(saida_de_processo(codigo=0, stderr="falha ao gerar")) == {}
    contadores = resumo_da_cobertura(
        saida_de_processo(codigo=0, stdout='{"endpoints": 2, "lacunas": 4}')
    )
    assert contadores["endpoints"] == 2


def test_eslint_json_vira_violacao_com_arquivo_e_linha():
    saida = json.dumps(
        [
            {
                "filePath": "C:\\projeto\\crud.cy.js",
                "messages": [
                    {"severity": 2, "ruleId": "no-unused-vars", "message": "x", "line": 12},
                    {"severity": 1, "ruleId": "aviso", "message": "y", "line": 3},
                ],
            }
        ]
    )
    violacoes = violacoes_do_eslint(saida)
    assert len(violacoes) == 1  # severidade 1 é aviso, não reprova
    assert violacoes[0].codigo == "QAORQ-021"
    assert violacoes[0].linha == 12
    assert violacoes[0].arquivo is not None
    assert violacoes[0].arquivo.endswith("crud.cy.js")


def test_eslint_sem_json_nao_explode():
    assert violacoes_do_eslint("saída em texto puro") == []
