"""Categoria planejada que não virou teste reprova o Gate B.

É o defeito de origem do projeto — "planejei e não entreguei" — e ele passava por
todos os gates: o `validar-suite-gerada.mjs` prova forma, e quem conta lacuna é o
`qa-cobertura.mjs`, que era só relatório no Bloco 3, depois do loop.

Numa execução real isso deixou `CAT-07` sem um único teste em cinco endpoints, com
o Gate B aprovando.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.contratos import Manifesto, Recurso
from orquestrador.gates import cobertura as gate_cobertura
from orquestrador.javascript import extrair_tags

from conftest import saida_de_processo

MANIFESTO = Manifesto.model_validate(
    {
        "recurso": "pedidos",
        "endpoints": [
            {"endpoint": "POST /pedidos", "cats": ["CAT-01", "CAT-07"]},
            {"endpoint": "GET /pedidos", "cats": ["CAT-01"]},
        ],
    }
)

SPEC_COMPLETO = """
describe("pedidos", () => {
  // @endpoint POST /pedidos @cat CAT-01
  it("cria", () => {});
  // @endpoint POST /pedidos @cat CAT-07
  it("aplica a regra", () => {});
  // @endpoint GET /pedidos @cat CAT-01
  it("lista", () => {});
});
"""

# Falta o `@cat CAT-07` de POST /pedidos: a categoria mais cara é a que some.
SPEC_COM_LACUNA = """
describe("pedidos", () => {
  // @endpoint POST /pedidos @cat CAT-01
  it("cria", () => {});
  // @endpoint GET /pedidos @cat CAT-01
  it("lista", () => {});
});
"""


@pytest.fixture
def recurso(config_falso, tmp_path: Path) -> Recurso:
    caminho = config_falso.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome="pedidos", caminho_testes=caminho)


def com_contadores(monkeypatch, lacunas: int, *, json_valido: bool = True) -> None:
    """Substitui o qa-cobertura.mjs: o veredito continua vindo do contador dele."""
    corpo = json.dumps({"endpoints": 2, "esperadas": 3, "lacunas": lacunas}) if json_valido else ""
    monkeypatch.setattr(
        gate_cobertura.Cobertura,
        "executar",
        lambda *_a, **_k: saida_de_processo(stdout=corpo),
    )


def escrever_spec(recurso: Recurso, conteudo: str) -> None:
    (recurso.caminho_testes / "crud.cy.js").write_text(conteudo, encoding="utf-8")


def test_sem_lacuna_aprova(config_falso, recurso, monkeypatch):
    com_contadores(monkeypatch, 0)
    escrever_spec(recurso, SPEC_COMPLETO)

    resultado = gate_cobertura.executar(
        config_falso, recurso, manifesto=MANIFESTO, gate="gate_b"
    )

    assert resultado is not None
    assert resultado.aprovado is True


def test_lacuna_reprova_e_nomeia_a_categoria(config_falso, recurso, monkeypatch):
    com_contadores(monkeypatch, 1)
    escrever_spec(recurso, SPEC_COM_LACUNA)

    resultado = gate_cobertura.executar(
        config_falso, recurso, manifesto=MANIFESTO, gate="gate_b"
    )

    assert resultado.aprovado is False
    assert resultado.codigos == ["QAORQ-030"]
    mensagem = resultado.violacoes[0].mensagem
    assert "POST /pedidos" in mensagem and "CAT-07" in mensagem
    # O delta precisa dizer o que escrever, não só que faltou algo.
    assert "@cat CAT-07" in mensagem


def test_contagem_divergente_descarta_o_detalhe(config_falso, recurso, monkeypatch):
    # O script diz 2, a leitura local acha 1. Nomear um par errado faria o executor
    # gastar tentativa consertando o que não estava quebrado — então o detalhe cai e
    # sobra o número, que é do script.
    com_contadores(monkeypatch, 2)
    escrever_spec(recurso, SPEC_COM_LACUNA)

    resultado = gate_cobertura.executar(
        config_falso, recurso, manifesto=MANIFESTO, gate="gate_b"
    )

    assert resultado.aprovado is False
    assert len(resultado.violacoes) == 1
    assert "2 categoria(s)" in resultado.violacoes[0].mensagem
    assert "CAT-07" not in resultado.violacoes[0].mensagem


def test_tag_dinamica_impede_o_detalhe_mas_nao_o_veredito(config_falso, recurso, monkeypatch):
    # A forma data-driven da skill resolve a tag em tempo de execução; este parser
    # não. Sem isto, um `it` coberto por template pareceria lacuna.
    com_contadores(monkeypatch, 1)
    escrever_spec(
        recurso,
        SPEC_COM_LACUNA + '\n// @endpoint POST /pedidos @cat ${cenario.cat}\n',
    )

    resultado = gate_cobertura.executar(
        config_falso, recurso, manifesto=MANIFESTO, gate="gate_b"
    )

    assert resultado.aprovado is False
    assert "1 categoria(s)" in resultado.violacoes[0].mensagem


def test_sem_contadores_avisa_em_vez_de_reprovar(config_falso, recurso, monkeypatch):
    # O script sai 0 mesmo sem gerar relatório. Sem contador não há veredito, e
    # inventar um seria pior do que anunciar que o instrumento falhou.
    com_contadores(monkeypatch, 0, json_valido=False)
    escrever_spec(recurso, SPEC_COMPLETO)

    resultado = gate_cobertura.executar(
        config_falso, recurso, manifesto=MANIFESTO, gate="gate_b"
    )

    assert resultado.aprovado is True
    assert resultado.avisos[0].codigo == "QAORQ-030"


def test_desligado_na_configuracao_nao_roda(config_falso, recurso, monkeypatch):
    config_falso.gates["b"].exigir_cobertura = False
    assert gate_cobertura.executar(config_falso, recurso, gate="gate_b") is None


# ---------------------------------------------------------------------------
# Parser de tags
# ---------------------------------------------------------------------------


def test_extrair_tags_le_endpoint_e_categoria():
    tags = extrair_tags(SPEC_COMPLETO)
    assert tags.pares == {
        ("POST /pedidos", "CAT-01"),
        ("POST /pedidos", "CAT-07"),
        ("GET /pedidos", "CAT-01"),
    }
    assert tags.dinamicas == 0


def test_extrair_tags_conta_a_dinamica_sem_adivinhar():
    tags = extrair_tags("// @endpoint POST /pedidos @cat ${caso.cat}\n")
    assert tags.pares == set()
    assert tags.dinamicas == 1
