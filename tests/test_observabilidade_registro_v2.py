"""Contrato de segurança, correlação e vida da execução no JSONL v2."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.observabilidade.eventos import ESQUEMA_DOS_EVENTOS, Evento, TipoDeEvento
from orquestrador.observabilidade.rastreamento import Rastreador
from orquestrador.observabilidade.registro import Registro, diretorio_de_execucao

pytestmark = pytest.mark.unit


def _linhas(caminho: Path) -> list[dict[str, object]]:
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


def test_jsonl_redige_segredo_e_substitui_saida_bruta_por_medida(tmp_path: Path):
    destino = tmp_path / "execucao.jsonl"
    segredo = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    stdout = "resposta extensa do Cypress"

    with Registro(destino, Console(file=None)) as registro:
        registro.evento(
            TipoDeEvento.CYPRESS,
            autorizacao=f"Bearer {segredo}",
            stdout=stdout,
        )

    texto = destino.read_text(encoding="utf-8")
    assert segredo not in texto
    assert stdout not in texto
    linha = _linhas(destino)[0]
    dados = linha["dados"]
    assert isinstance(dados, dict)
    assert dados["stdout"] == {
        "bytes": len(stdout.encode("utf-8")),
        "sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "omitido": True,
    }
    assert linha["sanitizacao"]["conteudos_omitidos"] == 1
    assert linha["sanitizacao"]["redacoes"] == 1


def test_envelope_v2_tem_identidade_ordem_e_payload_tipado(tmp_path: Path):
    destino = tmp_path / "execucao.jsonl"
    with Registro(destino, run_id="run-teste") as registro:
        registro.emitir(Evento(tipo=TipoDeEvento.GATE, dados={"aprovado": True}))
        registro.evento(TipoDeEvento.DELTA, codigos=["QAORQ-030"])

    primeira, segunda = _linhas(destino)
    assert primeira["schema_version"] == ESQUEMA_DOS_EVENTOS == 2
    assert primeira["run_id"] == segunda["run_id"] == "run-teste"
    assert primeira["seq"] == 1
    assert segunda["seq"] == 2
    assert primeira["event_id"] != segunda["event_id"]
    assert primeira["trace_id"] == segunda["trace_id"]
    assert primeira["dados"] == {"aprovado": True}


def test_diretorios_criados_no_mesmo_instante_nao_colidem(tmp_path: Path):
    primeiro = diretorio_de_execucao(tmp_path)
    segundo = diretorio_de_execucao(tmp_path)

    assert primeiro != segundo
    assert primeiro.is_dir() and segundo.is_dir()


def test_operacoes_formam_arvore_de_spans_e_registram_falha(tmp_path: Path):
    destino = tmp_path / "execucao.jsonl"
    with Registro(destino, run_id="run-teste") as registro:
        rastreador = Rastreador(registro)
        with rastreador.operacao("recurso", recurso="pedidos"):
            with pytest.raises(RuntimeError, match="quebrou"):
                with rastreador.operacao("gate", gate="b"):
                    raise RuntimeError("quebrou")

    linhas = _linhas(destino)
    inicios = [linha for linha in linhas if linha["tipo"] == "operacao_iniciada"]
    falha = next(linha for linha in linhas if linha["tipo"] == "operacao_falhou")
    fim = next(linha for linha in linhas if linha["tipo"] == "operacao_concluida")

    assert len(inicios) == 2
    assert inicios[1]["parent_span_id"] == inicios[0]["span_id"]
    assert falha["span_id"] == inicios[1]["span_id"]
    assert fim["span_id"] == inicios[0]["span_id"]
    assert falha["dados"]["erro_tipo"] == "RuntimeError"


def test_heartbeat_e_daemon_e_para_ao_fechar(tmp_path: Path):
    destino = tmp_path / "execucao.jsonl"
    registro = Registro(destino, intervalo_pulso_s=0.01)
    time.sleep(0.035)
    registro.fechar()
    quantidade_ao_fechar = len(_linhas(destino))
    time.sleep(0.025)

    assert any(linha["tipo"] == "pulso" for linha in _linhas(destino))
    assert len(_linhas(destino)) == quantidade_ao_fechar
    assert not (tmp_path / "execucao.lock").exists()
