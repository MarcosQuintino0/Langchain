"""Retenção exige prévia, confirmação e revalidação do alvo exato."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orquestrador.ferramentas.retencao_de_execucoes import (
    aplicar_limpeza,
    planejar_limpeza,
)
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit


def _concluida(base: Path, nome: str) -> Path:
    diretorio = base / nome
    with Registro(diretorio / "execucao.jsonl", run_id=nome, intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA, dry_run=True)
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)
    return diretorio


def test_previa_nao_apaga_e_aplicacao_explicita_deixa_diario(tmp_path: Path):
    antiga = _concluida(tmp_path, "20200101-000000-123-aaaaaaaa")
    plano = planejar_limpeza(
        tmp_path,
        antes_de_dias=30,
        agora=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert plano.candidatos == [antiga]
    assert antiga.is_dir(), "planejar é somente leitura"

    resultado = aplicar_limpeza(plano)

    assert resultado.removidos == [antiga]
    assert not antiga.exists()
    diario = tmp_path / "retencao.jsonl"
    linhas = [json.loads(item) for item in diario.read_text(encoding="utf-8").splitlines()]
    assert linhas[-1]["run_id"] == antiga.name
    assert linhas[-1]["resultado"] == "removido"


def test_recusa_execucao_ativa_sem_terminal_e_nome_desconhecido(tmp_path: Path):
    ativa = _concluida(tmp_path, "20200101-000000-123-aaaaaaaa")
    (ativa / "execucao.lock").write_text("123", encoding="utf-8")
    incompleta = tmp_path / "20200102-000000-123-bbbbbbbb"
    incompleta.mkdir()
    (incompleta / "execucao.jsonl").write_text(
        json.dumps({"tipo": "execucao_iniciada"}) + "\n", encoding="utf-8"
    )
    desconhecida = _concluida(tmp_path, "run-sem-data")

    plano = planejar_limpeza(
        tmp_path,
        antes_de_dias=30,
        agora=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert not plano.candidatos
    recusados = {item.caminho.name: item.motivo for item in plano.recusados}
    assert "ativa" in recusados[ativa.name]
    assert "terminal" in recusados[incompleta.name]
    assert "nome" in recusados[desconhecida.name]
