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


def test_requisicao_que_falhou_e_o_custo_do_provedor_aparecem_no_resumo(tmp_path: Path):
    """Uma execução que bateu em 429 antes de acertar não pode parecer uma chamada só.

    E `custo_reportado` é o único número autoritativo de gasto que existe — era
    capturado por requisição e nunca somado por ninguém.
    """
    caminho = tmp_path / "run-falhas" / "execucao.jsonl"
    with Registro(caminho, run_id="run-falhas", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA, dry_run=False)
        for _ in range(3):
            registro.evento(
                TipoDeEvento.REQUISICAO_LLM_FALHOU, estagio="mapeador", status=429, estado="falha"
            )
        registro.evento(
            TipoDeEvento.REQUISICAO_LLM_CONCLUIDA,
            estagio="mapeador",
            uso={"entrada": 100, "saida": 10},
            custo_reportado=0.0025,
        )
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)

    codigo, texto = _rodar(["mostrar", "run-falhas", "--base", str(tmp_path), "--json"])
    resumo = json.loads(texto)

    assert codigo == 0
    assert resumo["chamadas_llm"] == 1
    assert resumo["requisicoes_falhas"] == 3, "as três recusas do provedor sumiam do relatório"
    assert resumo["custo_reportado"] == 0.0025


def test_limpar_sem_aplicar_nao_apaga_nada(tmp_path: Path):
    """A garantia mais importante do único comando destrutivo da CLI.

    Ela não tinha teste nenhum: nem a prévia, nem o `--aplicar`.
    """
    antiga = tmp_path / "20200101-000000-123-abcdef12"
    _execucao(tmp_path, antiga.name, tokens=5)

    codigo, texto = _rodar(["limpar", "--base", str(tmp_path), "--antes-de", "1", "--json"])
    dados = json.loads(texto)

    assert codigo == 0
    assert dados["modo"] == "previa"
    assert str(antiga) in dados["candidatos"]
    assert dados["removidos"] == []
    assert antiga.is_dir(), "a prévia apagou um diretório — é o contrário do contrato"


def test_limpar_com_aplicar_remove_somente_o_candidato(tmp_path: Path):
    antiga = tmp_path / "20200101-000000-123-abcdef12"
    _execucao(tmp_path, antiga.name, tokens=5)
    recente = tmp_path / "run-recente"
    _execucao(tmp_path, recente.name, tokens=5)

    codigo, texto = _rodar(
        ["limpar", "--base", str(tmp_path), "--antes-de", "1", "--aplicar", "--json"]
    )
    dados = json.loads(texto)

    assert codigo == 0
    assert dados["modo"] == "aplicado"
    assert not antiga.exists()
    assert recente.is_dir(), "execução fora da janela de idade não pode ser tocada"
    assert str(antiga) in dados["removidos"]
