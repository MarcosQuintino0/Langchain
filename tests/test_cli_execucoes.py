"""Relatórios locais de execução: lista, detalhe, validação e comparação."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.cli.execucoes import comando_execucoes
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit


def _execucao(base: Path, nome: str, *, tokens: int, dry_run: bool = False) -> None:
    caminho = base / nome / "execucao.jsonl"
    with Registro(caminho, run_id=nome, intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA, dry_run=dry_run, recursos=["pedidos"])
        registro.evento(
            TipoDeEvento.REQUISICAO_LLM_CONCLUIDA,
            estagio="executor",
            uso={"entrada": tokens, "saida": 2},
            duracao_s=1.5,
        )
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)


def _rodar(argv: list[str]) -> tuple[int, str]:
    saida = io.StringIO()
    codigo = comando_execucoes(argv, Console(file=saida, width=180, color_system=None))
    return codigo, saida.getvalue()


def test_listar_e_mostrar_em_json(tmp_path: Path):
    _execucao(tmp_path, "run-a", tokens=10)
    _execucao(tmp_path, "run-b", tokens=20, dry_run=True)

    codigo, texto = _rodar(["listar", "--base", str(tmp_path), "--json"])
    lista = json.loads(texto)
    assert codigo == 0
    assert {item["run_id"] for item in lista} == {"run-a", "run-b"}

    codigo, texto = _rodar(["mostrar", "run-a", "--base", str(tmp_path), "--json"])
    detalhe = json.loads(texto)
    assert codigo == 0
    assert detalhe["tokens"] == 12
    assert detalhe["chamadas_llm"] == 1
    assert detalhe["terminal"] == "execucao_concluida"


def test_validar_e_comparar(tmp_path: Path):
    _execucao(tmp_path, "run-a", tokens=10)
    _execucao(tmp_path, "run-b", tokens=25)

    codigo, texto = _rodar(["validar", "--todas", "--base", str(tmp_path)])
    assert codigo == 0
    assert "2 execução(ões) válida(s)" in texto

    codigo, texto = _rodar(["comparar", "run-a", "run-b", "--base", str(tmp_path), "--json"])
    comparacao = json.loads(texto)
    assert codigo == 0
    assert comparacao["delta"]["tokens"] == 15


def test_historico_separa_real_de_dry_run(tmp_path: Path):
    _execucao(tmp_path, "run-real", tokens=10)
    _execucao(tmp_path, "run-dry", tokens=20, dry_run=True)

    codigo, texto = _rodar(["listar", "--historico", "--base", str(tmp_path), "--json"])
    historico = json.loads(texto)

    assert codigo == 0
    assert historico["real"]["execucoes"] == 1
    assert historico["dry_run"]["execucoes"] == 1
