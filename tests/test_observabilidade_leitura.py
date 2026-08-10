"""Leitura histórica tolerante e validação estrita sem migração destrutiva."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.leitura import LogInvalido, ler_execucao
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit


def test_le_v0_v1_e_v2_sem_reescrever_o_historico(tmp_path: Path):
    execucao = tmp_path / "run-antigo"
    execucao.mkdir()
    caminho = execucao / "execucao.jsonl"
    caminho.write_text(
        json.dumps({"ts": "2025-01-01T00:00:00Z", "tipo": "gate", "aprovado": True})
        + "\n"
        + json.dumps(
            {
                "ts": "2025-01-01T00:00:01Z",
                "schema_version": 1,
                "tipo": "execucao_concluida",
                "sucesso": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = caminho.read_bytes()

    leitura = ler_execucao(execucao)

    assert [evento.schema_version for evento in leitura.eventos] == [0, 1]
    assert [evento.seq for evento in leitura.eventos] == [1, 2]
    assert all(evento.run_id == "run-antigo" for evento in leitura.eventos)
    assert leitura.eventos[0].dados == {"aprovado": True}
    assert caminho.read_bytes() == original


def test_v2_e_validado_com_envelope_e_terminal(tmp_path: Path):
    execucao = tmp_path / "run-v2"
    caminho = execucao / "execucao.jsonl"
    with Registro(caminho, run_id="run-v2", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA, dry_run=True)
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)

    leitura = ler_execucao(execucao, estrito=True)

    assert len(leitura.eventos) == 2
    assert not leitura.problemas
    assert leitura.terminal == "execucao_concluida"


def test_tolerante_relata_json_invalido_e_estrito_recusa(tmp_path: Path):
    execucao = tmp_path / "run-quebrado"
    execucao.mkdir()
    (execucao / "execucao.jsonl").write_text('{"tipo":\n', encoding="utf-8")

    leitura = ler_execucao(execucao)

    assert [problema.codigo for problema in leitura.problemas] == [
        "json_invalido",
        "terminal_ausente",
    ]
    with pytest.raises(LogInvalido, match="json_invalido"):
        ler_execucao(execucao, estrito=True)


def test_detecta_run_misturado_sequencia_e_tipo_desconhecido(tmp_path: Path):
    execucao = tmp_path / "run-v2"
    caminho = execucao / "execucao.jsonl"
    with Registro(caminho, run_id="run-v2", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA)
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)
    linhas = [json.loads(item) for item in caminho.read_text(encoding="utf-8").splitlines()]
    linhas[1]["run_id"] = "outro-run"
    linhas[1]["seq"] = 9
    linhas[1]["tipo"] = "evento_inventado"
    caminho.write_text("\n".join(json.dumps(item) for item in linhas) + "\n", encoding="utf-8")

    codigos = {problema.codigo for problema in ler_execucao(execucao).problemas}

    assert {"run_id_misturado", "sequencia_invalida", "tipo_desconhecido"} <= codigos
