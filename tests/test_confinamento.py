"""Confinamento de caminho das tools do mapeador e dos arquivos que ele emite.

O caminho vem do LLM — tanto o que ele pede para ler quanto o que ele manda gravar.
Nada é aberto nem escrito no caminho cru.

O último bloco confina outra coisa pela mesma razão: o **ambiente** que os
subprocessos herdam. Node, Cypress, prettier e eslint executam código do
repositório do cliente, e a chave do provedor não tem o que fazer lá.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from orquestrador.agentes.executor import escrever
from orquestrador.contratos import (
    ArquivoSchema,
    Inventario,
    Manifesto,
    Recurso,
    SaidaExecutor,
    SaidaMapeador,
)
from orquestrador.ferramentas.arquivos import (
    CaminhoForaDaRaiz,
    Confinamento,
    buscar,
    confinar,
    ler_arquivo,
    listar_diretorio,
    sob_a_raiz,
)
from orquestrador.ferramentas.processo import (
    VARIAVEIS_DO_CYPRESS,
    executar,
    montar_ambiente,
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


# ---------------------------------------------------------------------------
# Schemas emitidos pelo mapeador
#
# Mesmo confinamento do `ArquivoGerado`, outra raiz: a do diretório de schemas.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caminho",
    [
        "../fora/entidade.schema.json",
        "pedidos/../../entidade.schema.json",
        "/pedidos/entidade.schema.json",
        "C:/pedidos/entidade.schema.json",
        "",
        "pedidos/entidade.json",  # sufixo errado: a skill não acharia o arquivo
    ],
)
def test_arquivo_schema_recusa_caminho_que_escapa_da_raiz(caminho: str):
    with pytest.raises(ValidationError):
        ArquivoSchema(caminho=caminho, conteudo="{}")


def test_arquivo_schema_normaliza_separador_do_windows():
    arquivo = ArquivoSchema(caminho="pedidos\\entidade.schema.json", conteudo="{}")
    assert arquivo.caminho == "pedidos/entidade.schema.json"


def saida_com_schema(caminho: str) -> SaidaMapeador:
    return SaidaMapeador(
        inventario=Inventario(
            recurso="pedidos",
            endpoints=[{"metodo": "GET", "rota": "/pedidos", "handler": "a", "arquivo": "x"}],
        ),
        manifesto=Manifesto.model_validate(
            {"recurso": "pedidos", "endpoints": [{"endpoint": "GET /pedidos"}]}
        ),
        schemas=[{"caminho": caminho, "conteudo": "{}"}],
    )


def test_recurso_nao_escreve_schema_de_outro_recurso():
    # O caminho é válido em si; o que o proíbe é a raiz não ser a do recurso.
    with pytest.raises(ValidationError, match="fora do recurso"):
        saida_com_schema("clientes/entidade.schema.json")


def test_schema_na_raiz_de_schemas_tambem_e_recusado():
    # Layout achatado ("entidade.schema.json" solto): a skill ainda o aceita como
    # legado, mas emitir nele deixaria o schema de dois recursos colidindo no
    # mesmo nome.
    with pytest.raises(ValidationError, match="fora do recurso"):
        saida_com_schema("entidade.schema.json")


def test_schema_no_lugar_certo_passa():
    saida = saida_com_schema("pedidos/entidade.schema.json")
    assert saida.schemas[0].caminho == "pedidos/entidade.schema.json"


# ---------------------------------------------------------------------------
# A regra do confinamento: comparação por componente, nunca por texto
# ---------------------------------------------------------------------------


def test_irmao_com_prefixo_comum_nao_esta_sob_a_raiz(tmp_path: Path):
    # `str(alvo).startswith(str(raiz))` aprovava este caso: "pedidos-antigos" começa
    # com "pedidos".
    raiz = tmp_path / "pedidos"
    irmao = tmp_path / "pedidos-antigos"
    raiz.mkdir()
    irmao.mkdir()
    assert not sob_a_raiz(irmao, raiz)
    assert not sob_a_raiz(irmao / "spec.cy.js", raiz)


def test_o_proprio_diretorio_conta_como_sob_a_raiz(tmp_path: Path):
    assert sob_a_raiz(tmp_path, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="caixa só é irrelevante no Windows")
def test_diferenca_de_caixa_continua_sendo_a_mesma_raiz(tmp_path: Path):
    raiz = tmp_path / "pedidos"
    raiz.mkdir()
    assert sob_a_raiz(Path(str(raiz).upper()) / "spec.cy.js", raiz)


def criar_junction(link: Path, destino: Path) -> bool:
    """Junction do Windows (`mklink /J`), que não exige privilégio de administrador.

    Symlink exigiria Developer Mode ligado; junction é o que um projeto de testes
    real costuma ter, e é o caso que `resolve()` existe para desarmar.
    """
    if os.name != "nt":
        return False
    # `mklink` não é executável: é builtin do `cmd.exe`, então invocá-lo por "cmd"
    # sem caminho absoluto (S607) e abrir o subprocesso (S603) são o desenho deste
    # helper, não descuido. A lista de argumentos é fixa, `shell=True` não aparece,
    # e os dois caminhos vêm do `tmp_path` do próprio teste — não há entrada de
    # terceiro para injetar. Mesmo regime documentado em ferramentas/processo.py.
    concluido = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link), str(destino)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return concluido.returncode == 0 and link.exists()


def test_junction_para_fora_do_recurso_nao_esta_sob_a_raiz(tmp_path: Path):
    recurso = tmp_path / "pedidos"
    fora = tmp_path / "fora"
    recurso.mkdir()
    fora.mkdir()
    if not criar_junction(recurso / "atalho", fora):
        pytest.skip("não foi possível criar junction neste ambiente")
    assert not sob_a_raiz(recurso / "atalho" / "spec.cy.js", recurso)


# ---------------------------------------------------------------------------
# Escrita do executor
# ---------------------------------------------------------------------------


def recurso_em(caminho: Path) -> Recurso:
    return Recurso(nome="pedidos", caminho_testes=caminho)


def saida_executor(caminho: str) -> SaidaExecutor:
    return SaidaExecutor(
        recurso="pedidos", arquivos=[{"caminho": caminho, "conteudo": "// spec\n"}]
    )


def test_executor_grava_dentro_do_recurso(tmp_path: Path):
    recurso = recurso_em(tmp_path / "pedidos")
    escritos = escrever(recurso, saida_executor("crud.cy.js"))
    assert escritos == [(tmp_path / "pedidos" / "crud.cy.js").resolve()]
    assert escritos[0].read_text(encoding="utf-8") == "// spec\n"


def test_executor_recusa_gravar_atraves_de_junction(tmp_path: Path):
    """A fuga que a comparação textual deixava passar, ponta a ponta.

    O contrato de `ArquivoGerado` já recusa `..` e caminho absoluto, então sozinho o
    prefixo comum não chega até aqui. Junto com uma junction, chega: o alvo resolve
    para `.../pedidos-antigos/crud.cy.js`, que **começa com** `.../pedidos` e passava
    no `startswith`. O executor gravava no diretório do recurso anterior do cliente.
    """
    recurso_dir = tmp_path / "pedidos"
    irmao = tmp_path / "pedidos-antigos"
    recurso_dir.mkdir()
    irmao.mkdir()
    if not criar_junction(recurso_dir / "atalho", irmao):
        pytest.skip("não foi possível criar junction neste ambiente")

    with pytest.raises(CaminhoForaDaRaiz):
        escrever(recurso_em(recurso_dir), saida_executor("atalho/crud.cy.js"))
    assert not (irmao / "crud.cy.js").exists()


def test_confinar_recusa_o_irmao_de_prefixo_comum(tmp_path: Path):
    (tmp_path / "pedidos").mkdir()
    (tmp_path / "pedidos-antigos").mkdir()
    with pytest.raises(CaminhoForaDaRaiz):
        confinar(tmp_path / "pedidos", str(tmp_path / "pedidos-antigos" / "crud.cy.js"))


# ---------------------------------------------------------------------------
# Confinamento do ambiente dos subprocessos
# ---------------------------------------------------------------------------


@pytest.fixture
def ambiente_sujo(monkeypatch: pytest.MonkeyPatch) -> None:
    """O ambiente de quem roda o pipeline de verdade: chave e tracing ligados."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-segredo-do-teste")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-segredo-do-teste")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-segredo-do-teste")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-segredo-do-teste")
    monkeypatch.setenv("CYPRESS_BASE_URL", "http://localhost:8080")


SEGREDOS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
)


def test_ambiente_minimo_nao_leva_chave_nem_tracing(ambiente_sujo: None):
    ambiente = montar_ambiente()
    assert not [nome for nome in SEGREDOS if nome in ambiente]


def test_ambiente_minimo_preserva_o_que_node_precisa(ambiente_sujo: None):
    ambiente = montar_ambiente()
    assert ambiente.get("PATH")
    if os.name == "nt":
        # Sem SystemRoot o Node nem sobe: ws2_32 e bcrypt saem daí.
        assert ambiente.get("SystemRoot") or ambiente.get("SYSTEMROOT")
        assert ambiente.get("TEMP") or ambiente.get("TMP")


def test_cypress_entra_por_allowlist_e_nao_por_padrao(ambiente_sujo: None):
    assert "CYPRESS_BASE_URL" not in montar_ambiente()
    assert "CYPRESS_BASE_URL" in montar_ambiente(VARIAVEIS_DO_CYPRESS)


def test_allowlist_larga_demais_ainda_nao_reintroduz_a_chave(ambiente_sujo: None):
    # A denylist é rede de segurança: uma configuração futura larga demais não pode
    # devolver a credencial ao subprocesso em silêncio.
    ambiente = montar_ambiente(["*"])
    assert not [nome for nome in SEGREDOS if nome in ambiente]
    assert ambiente.get("PATH")


def test_subprocesso_real_nao_enxerga_a_chave(ambiente_sujo: None):
    # O teste que o plano pede: rodar um processo que imprime o próprio ambiente.
    # É o mesmo interpretador do pytest, não uma dependência externa.
    saida = executar(
        [sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"],
        timeout_s=60,
    )
    assert saida.codigo == 0, saida.texto
    ambiente = json.loads(saida.stdout)
    assert not [nome for nome in SEGREDOS if nome in ambiente]
    assert "sk-or-v1-segredo-do-teste" not in json.dumps(ambiente)
