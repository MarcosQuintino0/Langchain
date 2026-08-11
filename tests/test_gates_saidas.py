"""Parsing da saída do `eslint --format json`.

O arquivo era três vezes maior: cobria também os parsers do
`validar-suite-gerada.mjs` e do `qa-cobertura.mjs`. Os dois saíram com o
desacoplamento da skill, e os testes deles saíram junto — teste de função que
ninguém chama passa para sempre e não protege nada.

O ponto sensível que sobrou: o ESLint separa erro (severidade 2) de aviso
(severidade 1), e só o primeiro reprova. Contar os dois transformaria estilo em
reprovação e mandaria o executor reescrever o que estava certo.
"""

from __future__ import annotations

import json

import pytest

from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.gates.saidas import violacoes_do_eslint

pytestmark = pytest.mark.unit


def test_extrair_json_tolera_ruido_em_volta():
    assert extrair_json('aviso\n{"valid": true}\n') == {"valid": True}


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
