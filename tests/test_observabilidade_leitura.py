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


def test_byte_invalido_nao_derruba_o_leitor(tmp_path: Path):
    """Log cortado no meio de um caractere multibyte é O caso do modo tolerante.

    `UnicodeDecodeError` herda de `ValueError` e escapava do `except OSError`:
    um byte ruim derrubava a leitura inteira, e um arquivo assim impedia validar
    todos os outros.
    """
    execucao = tmp_path / "run-bytes"
    execucao.mkdir()
    valida = json.dumps(
        {
            "ts": "2026-01-01T00:00:00Z",
            "schema_version": 1,
            "tipo": "execucao_concluida",
            "sucesso": True,
        }
    )
    (execucao / "execucao.jsonl").write_bytes(valida.encode("utf-8") + b"\n\xff\xfe\n")

    leitura = ler_execucao(execucao)

    codigos = {problema.codigo for problema in leitura.problemas}
    assert "bytes_invalidos" in codigos
    assert leitura.terminal == "execucao_concluida", "o que era legível foi conservado"


def test_versao_futura_nao_e_lida_pelo_ramo_legado(tmp_path: Path):
    """Versão desconhecida vira problema, nunca "validado sem problema".

    O legado é o ramo mais permissivo que existe; deixar um formato futuro cair
    nele produzia log aprovado com o payload aninhado no lugar errado — e a
    retenção decide APAGAR a partir dessa leitura.
    """
    execucao = tmp_path / "run-v3"
    execucao.mkdir()
    (execucao / "execucao.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00Z",
                "schema_version": 3,
                "tipo": "execucao_concluida",
                "run_id": "run-v3",
                "dados": {"sucesso": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    leitura = ler_execucao(execucao)

    assert "versao_desconhecida" in {problema.codigo for problema in leitura.problemas}
    assert not leitura.eventos, "linha de versão desconhecida não vira evento inventado"
    with pytest.raises(LogInvalido, match="versao_desconhecida"):
        ler_execucao(execucao, estrito=True)


def test_lock_sem_terminal_avisa_em_vez_de_aprovar_em_silencio(tmp_path: Path):
    """Processo morto deixa o lock; a execução truncada não pode virar "válida"."""
    execucao = tmp_path / "run-orfa"
    caminho = execucao / "execucao.jsonl"
    with Registro(caminho, run_id="run-orfa", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.BLOCO0, ok=True)
    (execucao / "execucao.lock").write_text("999999\n", encoding="utf-8")

    leitura = ler_execucao(execucao, estrito=True)

    assert [(p.codigo, p.severidade) for p in leitura.problemas] == [
        ("sem_terminal_com_lock", "aviso")
    ]
    assert leitura.terminal is None


def test_pulso_depois_do_terminal_nao_reprova_execucao_sadia(tmp_path: Path):
    """O heartbeat roda até `fechar()`; um batimento tardio é normal, não defeito.

    Como erro, ele reprovava a execução e a retenção passava a recusar aquele
    diretório para sempre.
    """
    execucao = tmp_path / "run-pulso"
    caminho = execucao / "execucao.jsonl"
    with Registro(caminho, run_id="run-pulso", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_INICIADA)
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)
        registro.evento(TipoDeEvento.PULSO, pid=1)

    leitura = ler_execucao(execucao, estrito=True)

    assert not leitura.problemas


def test_evento_util_depois_do_terminal_continua_reprovando(tmp_path: Path):
    """A tolerância é do `pulso`, e só dele."""
    execucao = tmp_path / "run-depois"
    caminho = execucao / "execucao.jsonl"
    with Registro(caminho, run_id="run-depois", intervalo_pulso_s=None) as registro:
        registro.evento(TipoDeEvento.EXECUCAO_CONCLUIDA, sucesso=True)
        registro.evento(TipoDeEvento.GATE, aprovado=False)

    codigos = [problema.codigo for problema in ler_execucao(execucao).problemas]

    assert codigos == ["evento_apos_terminal"]
