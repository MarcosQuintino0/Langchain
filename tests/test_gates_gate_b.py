"""Etapa de formatadores do Gate B (prettier/eslint).

O comando é configurável, então o teste usa o próprio Python como "formatador"
para exercitar os desfechos sem depender do toolchain do projeto.

A união das checagens deixou de ser exercitada aqui com o desacoplamento da skill
(2026-08-10): o validador `.mjs` e a medida de lacunas saíram do Gate B, e o que
sobrou ao lado dos formatadores é `conferir_limpeza`, coberto em
`tests/test_gates_limpeza.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orquestrador.dominio.veredito import VereditoDeGate
from orquestrador.gates import gate_b

pytestmark = pytest.mark.unit


def staging_de(config) -> Path:
    """O diretório que o gate realmente valida: o staging, não o destino."""
    caminho = config.caminhos.recurso(".qa-staging-teste-pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def test_formatador_desligado_nao_produz_veredito(config_falso):
    assert (
        gate_b._formatador(config_falso, "prettier", "QAORQ-020", [], staging_de(config_falso))
        is None
    )


def test_formatador_que_passa_aprova(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys"],
        staging_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is True


def test_formatador_que_reprova_vira_violacao(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys; print('mal formatado'); sys.exit(1)"],
        staging_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is False
    assert resultado.codigos == ["QAORQ-020"]
    assert "mal formatado" in resultado.violacoes[0].mensagem


def test_formatador_ausente_vira_aviso_por_padrao(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "eslint",
        "QAORQ-021",
        ["ferramenta-que-nao-existe-no-path"],
        staging_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is True
    assert [aviso.codigo for aviso in resultado.avisos] == ["QAORQ-022"]


def test_formatador_ausente_e_exigido_e_erro_da_ferramenta(config_falso):
    # Exigido e ausente é falha do ambiente de quem roda o orquestrador. Como
    # violação, o QAORQ-022 iria ao executor no delta — e ele não instala nada.
    config_falso.execucao.exigir_formatadores = True
    resultado = gate_b._formatador(
        config_falso,
        "eslint",
        "QAORQ-021",
        ["ferramenta-que-nao-existe-no-path"],
        staging_de(config_falso),
    )
    assert resultado is not None
    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
    assert resultado.violacoes == []
    assert "eslint" in resultado.motivo


def test_o_recurso_e_passado_relativo_ao_projeto(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(1)"],
        staging_de(config_falso),
    )
    assert resultado is not None
    # É o staging que vai ao formatador: formatar o destino seria formatar o que
    # ainda não foi aprovado.
    assert "cypress/e2e/apis/.qa-staging-teste-pedidos" in resultado.violacoes[0].mensagem
