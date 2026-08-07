"""Etapa de formatadores do Gate B (prettier/eslint).

O comando é configurável, então o teste usa o próprio Python como "formatador"
para exercitar os três desfechos sem depender do toolchain do projeto.
"""

from __future__ import annotations

import sys

from orquestrador.contratos import Recurso
from orquestrador.gates import gate_b


def recurso_de(config) -> Recurso:
    caminho = config.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome="pedidos", caminho_testes=caminho)


def test_formatador_desligado_nao_produz_veredito(config_falso):
    assert gate_b._formatador(config_falso, "prettier", "QAORQ-020", [], recurso_de(config_falso)) is None


def test_formatador_que_passa_aprova(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys"],
        recurso_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is True


def test_formatador_que_reprova_vira_violacao(config_falso):
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys; print('mal formatado'); sys.exit(1)"],
        recurso_de(config_falso),
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
        recurso_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is True
    assert [aviso.codigo for aviso in resultado.avisos] == ["QAORQ-022"]


def test_formatador_ausente_reprova_quando_exigido(config_falso):
    config_falso.execucao.exigir_formatadores = True
    resultado = gate_b._formatador(
        config_falso,
        "eslint",
        "QAORQ-021",
        ["ferramenta-que-nao-existe-no-path"],
        recurso_de(config_falso),
    )
    assert resultado is not None and resultado.aprovado is False
    assert resultado.codigos == ["QAORQ-022"]


def test_o_recurso_e_passado_relativo_ao_projeto(config_falso):
    recurso = recurso_de(config_falso)
    resultado = gate_b._formatador(
        config_falso,
        "prettier",
        "QAORQ-020",
        [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(1)"],
        recurso,
    )
    assert resultado is not None
    assert "cypress/e2e/apis/pedidos" in resultado.violacoes[0].mensagem
