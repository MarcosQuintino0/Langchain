"""Exportação OTLP/HTTP é opt-in, minimizada e não interfere no JSONL local."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.config import ConfigOtlp
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.exportacao_otlp import ExportadorOtlp
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit


def test_desabilitado_nao_inicia_envio():
    chamadas = []
    exportador = ExportadorOtlp(
        ConfigOtlp(habilitado=False),
        transporte=lambda *args: chamadas.append(args),
    )

    exportador.exportar({"tipo": "gate"})
    exportador.fechar()

    assert chamadas == []


def test_exporta_traces_e_metricas_sem_identificadores_ou_conteudo(tmp_path: Path):
    chamadas: list[tuple[str, dict[str, object], dict[str, str], float]] = []

    def transporte(url, dados, headers, timeout_s):
        chamadas.append((url, dados, headers, timeout_s))

    exportador = ExportadorOtlp(
        ConfigOtlp(
            habilitado=True,
            endpoint="http://collector:4318",
            timeout_s=0.1,
            service_name="orquestrador-testes",
            incluir_identificadores=False,
        ),
        transporte=transporte,
    )
    destino = tmp_path / "run-secreto" / "execucao.jsonl"
    with Registro(
        destino,
        run_id="run-secreto",
        intervalo_pulso_s=None,
        exportador=exportador,
    ) as registro:
        registro.evento(
            TipoDeEvento.REQUISICAO_LLM_CONCLUIDA,
            recurso="pedidos",
            endpoint="POST /pedidos",
            modelo="modelo-interno",
            request_id="req-secreto",
            uso={"entrada": 10, "saida": 4},
            duracao_s=0.5,
            stdout="conteúdo que só pode ficar resumido localmente",
        )

    urls = {url for url, _dados, _headers, _timeout in chamadas}
    assert urls == {"http://collector:4318/v1/traces", "http://collector:4318/v1/metrics"}
    remoto = json.dumps([dados for _url, dados, _headers, _timeout in chamadas])
    for proibido in ("run-secreto", "pedidos", "POST /pedidos", "modelo-interno", "req-secreto"):
        assert proibido not in remoto
    assert "conteúdo" not in remoto
    assert "orquestrador-testes" in remoto
    assert destino.is_file(), "o JSONL local continua sendo a fonte primária"


def test_falha_do_collector_nao_impede_evento_local(tmp_path: Path):
    def falhar(*_args):
        raise OSError("collector indisponível")

    exportador = ExportadorOtlp(
        ConfigOtlp(habilitado=True, endpoint="http://collector:4318", timeout_s=0.1),
        transporte=falhar,
    )
    destino = tmp_path / "run" / "execucao.jsonl"

    with Registro(destino, intervalo_pulso_s=None, exportador=exportador) as registro:
        registro.evento(TipoDeEvento.PROCESSO, codigo=1, duracao_s=0.1)

    assert destino.read_text(encoding="utf-8").strip()
    assert exportador.falhas >= 1
