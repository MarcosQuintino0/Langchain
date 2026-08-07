"""Parsing da saída dos scripts .mjs.

O ponto sensível: o validador escreve no stdout quando aprova e no stderr quando
reprova. Um parser que lê só stdout enxerga reprovação como saída vazia — o teste
abaixo é o que impede essa regressão.
"""

from __future__ import annotations

import json

import pytest
from conftest import saida_de_processo

from orquestrador.gates.parser import (
    ErroDeInvocacao,
    extrair_json,
    resultado_do_validador,
    resumo_da_cobertura,
    violacoes_do_eslint,
)

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


def test_aprovacao_vem_pelo_stdout():
    resultado = resultado_do_validador(
        saida_de_processo(codigo=0, stdout=APROVADO), gate="gate_a"
    )
    assert resultado.aprovado is True
    assert resultado.violacoes == []
    assert [aviso.codigo for aviso in resultado.avisos] == ["QAAPI-035"]


def test_reprovacao_vem_pelo_stderr():
    resultado = resultado_do_validador(
        saida_de_processo(codigo=1, stdout="", stderr=REPROVADO), gate="gate_a"
    )
    assert resultado.aprovado is False
    assert resultado.codigos == ["QAAPI-021", "QAAPI-002"]
    primeira = resultado.violacoes[0]
    assert primeira.arquivo == "_support/cobertura.json"
    assert primeira.linha == 24
    assert "CAT-05" in primeira.mensagem


def test_saida_bruta_guarda_os_dois_fluxos():
    resultado = resultado_do_validador(
        saida_de_processo(codigo=1, stdout="ruído", stderr=REPROVADO), gate="gate_a"
    )
    assert "ruído" in resultado.saida_bruta
    assert "QAAPI-021" in resultado.saida_bruta


def test_exit_2_e_erro_de_invocacao_nao_delta():
    # Exit 2 é erro de uso NOSSO: precisa estourar, não virar violação para o LLM.
    with pytest.raises(ErroDeInvocacao):
        resultado_do_validador(
            saida_de_processo(codigo=2, stderr="ERRO DE USO: opcao desconhecida"),
            gate="gate_a",
        )


def test_saida_sem_json_e_erro_de_invocacao():
    with pytest.raises(ErroDeInvocacao):
        resultado_do_validador(
            saida_de_processo(codigo=1, stderr="SUITE INVALIDA: ..."), gate="gate_b"
        )


def test_extrair_json_tolera_ruido_em_volta():
    assert extrair_json('aviso\n{"valid": true}\n') == {"valid": True}


def test_resumo_da_cobertura_vazio_quando_nao_ha_json():
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
    assert violacoes[0].arquivo.endswith("crud.cy.js")


def test_eslint_sem_json_nao_explode():
    assert violacoes_do_eslint("saída em texto puro") == []
