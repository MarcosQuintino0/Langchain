"""Subprocessos e artefatos são medidos sem copiar conteúdo para eventos."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orquestrador.ferramentas.processo import executar, observar_processos
from orquestrador.observabilidade.artefatos import medir_arquivos

pytestmark = pytest.mark.unit


def test_observador_de_processo_recebe_bytes_hash_codigo_e_nao_recebe_saida(tmp_path: Path):
    observados = []
    with observar_processos(observados.append):
        saida = executar(
            [sys.executable, "-c", "print('texto confidencial')"],
            cwd=tmp_path,
            resolver=False,
        )

    assert saida.codigo == 0
    medido = observados[0]
    assert medido.codigo == 0
    assert medido.stdout_bytes == len(saida.stdout.encode("utf-8"))
    assert medido.stderr_bytes == 0
    assert len(medido.comando_sha256) == 64
    assert "confidencial" not in repr(medido)


def test_artefato_vira_caminho_tamanho_e_hash(tmp_path: Path):
    arquivo = tmp_path / "resultado.json"
    arquivo.write_text('{"ok": true}', encoding="utf-8")

    medido = medir_arquivos([arquivo])[0]

    assert medido["caminho"] == str(arquivo)
    assert medido["bytes"] == arquivo.stat().st_size
    assert len(medido["sha256"]) == 64
    assert "conteudo" not in medido
