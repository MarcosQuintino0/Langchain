"""Etapa de formatadores do Gate B (prettier/eslint) e a união das checagens.

O comando é configurável, então o teste usa o próprio Python como "formatador"
para exercitar os desfechos sem depender do toolchain do projeto.
"""

from __future__ import annotations

import json
import sys

import pytest

from conftest import saida_de_processo
from orquestrador.contratos import Recurso, VereditoDeGate
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.gates import cobertura as gate_cobertura
from orquestrador.gates import gate_b


def recurso_de(config) -> Recurso:
    caminho = config.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome="pedidos", caminho_testes=caminho)


def test_formatador_desligado_nao_produz_veredito(config_falso):
    assert (
        gate_b._formatador(config_falso, "prettier", "QAORQ-020", [], recurso_de(config_falso))
        is None
    )


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


def test_formatador_ausente_e_exigido_e_erro_da_ferramenta(config_falso):
    # Exigido e ausente é falha do ambiente de quem roda o orquestrador. Como
    # violação, o QAORQ-022 iria ao executor no delta — e ele não instala nada.
    config_falso.execucao.exigir_formatadores = True
    resultado = gate_b._formatador(
        config_falso,
        "eslint",
        "QAORQ-021",
        ["ferramenta-que-nao-existe-no-path"],
        recurso_de(config_falso),
    )
    assert resultado is not None
    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
    assert resultado.violacoes == []
    assert "eslint" in resultado.motivo


def com_validador(monkeypatch, *, valido: bool, erros: list[dict] | None = None) -> None:
    corpo = json.dumps({"valid": valido, "errors": erros or []})
    monkeypatch.setattr(
        gate_b.Validador,
        "executar",
        lambda *_a, **_k: saida_de_processo(codigo=0 if valido else 1, stdout=corpo),
    )


def com_cobertura(monkeypatch, corpo: str) -> None:
    monkeypatch.setattr(
        gate_cobertura.Cobertura, "executar", lambda *_a, **_k: saida_de_processo(stdout=corpo)
    )


def test_checagem_que_nao_rodou_interrompe_em_vez_de_virar_delta(config_falso, monkeypatch):
    # O validador aprovou, mas a lacuna ficou sem medida. Aprovar aqui declararia
    # cobertura que ninguém contou; reprovar mandaria o executor reescrever specs
    # por causa de um script que não rodou.
    com_validador(monkeypatch, valido=True)
    com_cobertura(monkeypatch, "falha ao gerar o relatório")

    with pytest.raises(ErroDeFerramenta, match="qa-cobertura"):
        gate_b.executar(config_falso, recurso_de(config_falso))


def test_reprovacao_normal_atravessa_a_uniao(config_falso, monkeypatch):
    com_validador(
        monkeypatch,
        valido=False,
        erros=[{"code": "QAAPI-025", "message": "campo sem teste"}],
    )
    com_cobertura(monkeypatch, json.dumps({"lacunas": 0}))

    resultado = gate_b.executar(config_falso, recurso_de(config_falso))

    assert resultado.veredito is VereditoDeGate.REPROVADO
    assert resultado.codigos == ["QAAPI-025"]


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
