"""Confinamento de caminho das tools do mapeador.

O caminho vem do LLM. Nada é aberto no caminho cru.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orquestrador.ferramentas.arquivos import (
    Confinamento,
    CaminhoForaDaRaiz,
    buscar,
    ler_arquivo,
    listar_diretorio,
)


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    raiz = tmp_path / "backend"
    (raiz / "src" / "controllers").mkdir(parents=True)
    (raiz / "src" / "controllers" / "PedidoController.java").write_text(
        "class PedidoController {\n  @GetMapping\n  listar() {}\n}\n", encoding="utf-8"
    )
    (raiz / "node_modules" / "lixo").mkdir(parents=True)
    (raiz / "node_modules" / "lixo" / "index.js").write_text(
        "@GetMapping falso\n", encoding="utf-8"
    )
    (raiz / "graph.json").write_text('{"nodes": []}\n', encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("fora da raiz\n", encoding="utf-8")
    return raiz


def test_resolve_caminho_relativo_sob_a_raiz(backend: Path):
    confinamento = Confinamento(backend)
    alvo = confinamento.resolver("src/controllers/PedidoController.java")
    assert alvo.is_file()
    assert confinamento.relativo(alvo) == "src/controllers/PedidoController.java"


def test_recusa_fuga_por_pontos(backend: Path):
    with pytest.raises(CaminhoForaDaRaiz):
        Confinamento(backend).resolver("../segredo.txt")


def test_recusa_fuga_disfarcada_no_meio_do_caminho(backend: Path):
    with pytest.raises(CaminhoForaDaRaiz):
        Confinamento(backend).resolver("src/../../segredo.txt")


def test_recusa_caminho_absoluto_fora_da_raiz(backend: Path):
    with pytest.raises(CaminhoForaDaRaiz):
        Confinamento(backend).resolver(str(backend.parent / "segredo.txt"))


def test_aceita_caminho_absoluto_dentro_da_raiz(backend: Path):
    alvo = Confinamento(backend).resolver(str(backend / "src"))
    assert alvo == (backend / "src").resolve()


def test_prefixo_parecido_nao_conta_como_dentro(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend-outro").mkdir()
    with pytest.raises(CaminhoForaDaRaiz):
        Confinamento(tmp_path / "backend").resolver(str(tmp_path / "backend-outro"))


@pytest.mark.skipif(os.name != "nt", reason="caixa só é irrelevante no Windows")
def test_windows_ignora_a_caixa_do_caminho(backend: Path):
    # Comparar sensível a maiúsculas deixaria C:\BACKEND\... parecer outra raiz.
    alvo = Confinamento(backend).resolver(str(backend).upper() + "\\src")
    assert alvo.name.lower() == "src"


def test_ler_arquivo_numera_linhas_e_respeita_offset(backend: Path):
    saida = ler_arquivo(
        Confinamento(backend), "src/controllers/PedidoController.java", offset=1, limit=1
    )
    assert "@GetMapping" in saida
    assert "class PedidoController" not in saida
    assert "linhas 2-2 de 4" in saida


def test_ler_arquivo_recusa_o_grafo(backend: Path):
    # graph.json real tem dezenas de MB: ou graphify query, ou leitura do código.
    saida = ler_arquivo(Confinamento(backend), "graph.json")
    assert saida.startswith("ERRO")
    assert "graphify" in saida


def test_ler_arquivo_recusa_arquivo_grande(backend: Path):
    grande = backend / "grande.java"
    grande.write_text("x" * 5000, encoding="utf-8")
    saida = ler_arquivo(Confinamento(backend), "grande.java", max_bytes=1000)
    assert saida.startswith("ERRO")
    assert "grande demais" in saida


def test_ler_arquivo_fora_da_raiz_estoura(backend: Path):
    with pytest.raises(CaminhoForaDaRaiz):
        ler_arquivo(Confinamento(backend), "../segredo.txt")


def test_listar_diretorio_marca_pastas_e_pula_ignorados(backend: Path):
    saida = listar_diretorio(Confinamento(backend), ".")
    assert "src/" in saida
    assert "node_modules" not in saida


def test_buscar_ignora_node_modules_e_o_grafo(backend: Path):
    saida = buscar(Confinamento(backend), r"@GetMapping")
    assert "PedidoController.java:2" in saida
    assert "node_modules" not in saida


def test_buscar_aceita_glob(backend: Path):
    assert "nenhuma ocorrência" in buscar(Confinamento(backend), r"@GetMapping", glob="*.ts")


def test_buscar_com_regex_invalida_nao_explode(backend: Path):
    assert buscar(Confinamento(backend), "(nao fecha").startswith("ERRO")
