"""O wheel contém o que a execução procura, e `init`/`doctor` dizem a verdade.

Por que este arquivo existe
---------------------------
O `pip install` do wheel instalava com sucesso e falhava no primeiro comando: os
prompts ficavam fora do pacote e eram procurados na árvore de fontes, que não
existe num ambiente instalado. É um defeito que **nenhum teste do repositório
pegava**, porque todos rodam de dentro do checkout — onde os prompts estão lá.

Daí a forma das checagens aqui. Duas famílias:

* as que leem o `pyproject.toml` e a árvore real e comparam as duas. São baratas e
  pegam a regressão mais provável: subpacote novo que ninguém acrescentou à lista
  explícita de `packages` e some do wheel em silêncio.
* a que constrói o wheel de verdade e o instala num ambiente vazio
  (`test_wheel_limpo`). Só ela prova o que interessa, e por isso ela existe apesar
  de ser lenta — marcada `integration`, pulada quando falta `uv`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from orquestrador.cli import (
    MARCA_DE_PREENCHIMENTO,
    MODELO_DE_CONFIG_DE_PROJETO,
    SUCESSO,
    main,
)
from orquestrador.config import Config
from orquestrador.llm.montagem import carregar_prompt
from orquestrador.raiz import ARVORE_DE_FONTES, DIR_PROMPTS_PADRAO

RAIZ_DO_REPOSITORIO = Path(__file__).resolve().parent.parent
PACOTE = RAIZ_DO_REPOSITORIO / "src" / "orquestrador"
PYPROJECT = RAIZ_DO_REPOSITORIO / "pyproject.toml"

# Os prompts sem os quais não há estágio de LLM. O auditor está fora porque é stub.
PROMPTS_EXIGIDOS = ("mapeador", "executor")


def setuptools_do_pyproject() -> dict[str, Any]:
    dados: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return dados["tool"]["setuptools"]


def subpacotes_reais() -> set[str]:
    """Diretórios de `src/orquestrador/` que são pacote Python de verdade."""
    return {
        f"orquestrador.{caminho.parent.name}"
        for caminho in PACOTE.glob("*/__init__.py")
        if caminho.parent.name != "__pycache__"
    }


# ---------------------------------------------------------------------------
# A lista explícita de `packages` acompanha a árvore real
# ---------------------------------------------------------------------------


def test_todo_subpacote_real_esta_declarado_no_pyproject():
    """`packages.find` não vê `prompts/`; o preço é uma lista que pode envelhecer.

    Esta é a checagem que paga esse preço. Sem ela, um subpacote novo continua
    passando em todo o resto da suíte — que roda do checkout — e só some no wheel,
    onde ninguém olha até a instalação de alguém quebrar.
    """
    declarados = set(setuptools_do_pyproject()["packages"])
    reais = subpacotes_reais() | {"orquestrador"}

    faltando = sorted(reais - declarados)
    assert not faltando, (
        f"subpacote(s) ausente(s) de [tool.setuptools].packages: {faltando}.\n"
        "Eles existem em src/orquestrador/ e não iriam para o wheel. Acrescente-os "
        "à lista em pyproject.toml — ela é explícita porque `package-dir` mapeia "
        "`prompts/` de fora de src/, e a descoberta automática não enxerga isso."
    )

    fantasmas = sorted(declarados - reais - {"orquestrador.prompts"})
    assert not fantasmas, (
        f"[tool.setuptools].packages lista pacote(s) que não existem: {fantasmas}.\n"
        "Remova a linha, ou restaure o subpacote se ele foi movido por engano."
    )


def test_prompts_sao_empacotados_a_partir_da_raiz():
    setuptools = setuptools_do_pyproject()

    assert setuptools["package-dir"].get("orquestrador.prompts") == "prompts", (
        "o mapeamento [tool.setuptools].package-dir de `orquestrador.prompts` para "
        "`prompts/` é o que leva o conteúdo editorial da raiz para dentro do wheel "
        "sem criar uma segunda cópia versionada. Sem ele o wheel volta a instalar "
        "sem prompt nenhum e a falhar no primeiro comando."
    )
    assert "*.md" in setuptools["package-data"]["orquestrador.prompts"], (
        "sem package-data o diretório é mapeado e nenhum arquivo é copiado."
    )


def test_fixtures_e_config_ficam_fora_do_pacote():
    """O que NÃO vai no wheel é decisão, e decisão não declarada volta atrás sozinha.

    `fixtures/` é dado de desenvolvimento do `--dry-run`; `config.toml` é do
    usuário e nasce de `orquestrador init`. Nenhum dos dois pode virar package-data
    por conveniência de um teste futuro.
    """
    setuptools = setuptools_do_pyproject()
    mapeados = set(setuptools["package-dir"].values())

    assert "fixtures" not in mapeados
    assert not any(nome.endswith("fixtures") for nome in setuptools["packages"])


def test_existe_uma_unica_copia_dos_prompts():
    """A tensão do AGENTS.md resolvida: editável fora de `src/`, e só uma versão.

    Se um dia alguém "resolver" o empacotamento copiando `prompts/` para dentro de
    `src/orquestrador/`, passam a existir duas cópias versionadas — e a que o
    editor de conteúdo abre deixa de ser a que o pipeline lê. É exatamente a
    divergência que o mapeamento de `package-dir` existe para evitar.
    """
    assert (RAIZ_DO_REPOSITORIO / "prompts").is_dir()
    assert not (PACOTE / "prompts").exists(), (
        "existe uma cópia dos prompts dentro do pacote. No checkout eles moram só "
        "em prompts/, na raiz; a cópia do wheel é gerada no build, nunca versionada."
    )


# ---------------------------------------------------------------------------
# A resolução em tempo de execução
# ---------------------------------------------------------------------------


def test_o_padrao_dos_prompts_aponta_para_a_arvore_de_fontes_no_checkout():
    assert ARVORE_DE_FONTES == RAIZ_DO_REPOSITORIO
    assert DIR_PROMPTS_PADRAO == RAIZ_DO_REPOSITORIO / "prompts"


@pytest.mark.parametrize("nome", PROMPTS_EXIGIDOS)
def test_cada_prompt_exigido_carrega_do_padrao(nome: str):
    assert carregar_prompt(nome).strip()


# ---------------------------------------------------------------------------
# `orquestrador init`
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_escreve_uma_configuracao_que_carrega(tmp_path: Path):
    """O template e os modelos Pydantic são duas descrições da mesma forma.

    Elas divergem em silêncio: um campo renomeado em `config.py` só aparece quando
    alguém roda `init` e o primeiro comando reclama de `extra="forbid"`. Carregar o
    arquivo gerado é o que amarra as duas.
    """
    assert main(["init", "--em", str(tmp_path)]) == SUCESSO

    config = Config.carregar(tmp_path / "config.toml")
    assert config.origem == (tmp_path / "config.toml").resolve()
    assert config.gates["b"].exigir_cobertura is True


@pytest.mark.unit
def test_init_deixa_os_campos_sem_padrao_possivel_detectaveis(tmp_path: Path):
    """Não preenchido precisa ser distinguível de preenchido, ou o doctor mente.

    Caminho e modelo usam marcas diferentes porque falham diferente. Um `""` em
    caminho vira `Path(".")` e o diretório do próprio `config.toml` passaria por
    "backend existe"; já um `PREENCHA` em `modelo` seria mandado ao provedor como
    se fosse um identificador real.
    """
    main(["init", "--em", str(tmp_path)])
    config = Config.carregar(tmp_path / "config.toml")

    for caminho in (
        config.caminhos.skill,
        config.caminhos.backend,
        config.caminhos.projeto_testes,
    ):
        assert MARCA_DE_PREENCHIMENTO in caminho.as_posix()
        assert not caminho.is_dir()

    assert [nome for nome, estagio in config.estagios.items() if not estagio.modelo] == [
        "mapeador",
        "executor",
    ]


@pytest.mark.unit
def test_o_template_nao_traz_nome_de_modelo():
    """Princípio 6: nenhum nome de modelo em código — e o template é código nosso."""
    for linha in MODELO_DE_CONFIG_DE_PROJETO.splitlines():
        if linha.startswith("modelo"):
            assert linha == 'modelo = ""', f"nome de modelo embutido no template: {linha!r}"


@pytest.mark.unit
def test_init_recusa_sobrescrever_e_so_cede_com_forcar(tmp_path: Path):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text("# configuração de alguém\n", encoding="utf-8")

    assert main(["init", "--em", str(tmp_path)]) != SUCESSO
    assert arquivo.read_text(encoding="utf-8") == "# configuração de alguém\n"

    assert main(["init", "--em", str(tmp_path), "--forcar"]) == SUCESSO
    assert MARCA_DE_PREENCHIMENTO in arquivo.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# O wheel, de verdade
# ---------------------------------------------------------------------------

UV = shutil.which("uv")


@pytest.mark.integration
@pytest.mark.skipif(UV is None, reason="o teste de wheel limpo precisa do uv")
def test_wheel_limpo(tmp_path: Path):
    """Constrói o wheel, instala num ambiente vazio e roda os três comandos.

    É o único teste que prova o item: todos os outros rodam do checkout, onde
    `prompts/`, `fixtures/` e `config.toml` estão a um `parents[2]` de distância —
    exatamente a condição que escondia o defeito.

    O `--dry-run` é exercitado esperando **falha**, e isso é a decisão, não uma
    concessão: `fixtures/` é material de desenvolvimento e empacotá-lo faria todo
    usuário baixar o backend e o projeto Cypress de mentira deste repositório. Quem
    instala confere a instalação com `doctor`, que não depende de fixture nenhuma.
    O que o teste cobra é que a recusa seja um diagnóstico, e não um erro de
    `copytree` sobre um caminho que ninguém pediu.
    """
    executavel = UV
    assert executavel is not None

    def uv(*argumentos: str, cwd: Path | None = None) -> None:
        subprocess.run(  # noqa: S603
            [executavel, *argumentos],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    dist = tmp_path / "dist"
    # O wheel sai em `tmp_path`, mas o setuptools ainda usa `build/` na raiz do
    # repositório como área intermediária. Se ele não estava lá antes, sai daqui
    # limpo: um `build/` esquecido é uma cópia velha do pacote no meio da árvore de
    # quem estiver trabalhando em paralelo. Se já estava, não é nosso para apagar.
    intermediario = RAIZ_DO_REPOSITORIO / "build"
    nosso = not intermediario.exists()
    try:
        uv("build", "--wheel", "--out-dir", str(dist), cwd=RAIZ_DO_REPOSITORIO)
    finally:
        if nosso:
            shutil.rmtree(intermediario, ignore_errors=True)
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1, f"esperava um wheel, achei {wheels}"

    ambiente = tmp_path / "ambiente"
    versao = f"{sys.version_info.major}.{sys.version_info.minor}"
    uv("venv", "--python", versao, str(ambiente))
    python = (
        ambiente
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    uv("pip", "install", "--python", str(python), str(wheels[0]))

    projeto = tmp_path / "projeto"
    projeto.mkdir()

    def orquestrador(*argumentos: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [str(python), "-m", "orquestrador", *argumentos],
            cwd=projeto,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    ajuda = orquestrador("--help")
    assert ajuda.returncode == SUCESSO
    assert "orquestrador init" in ajuda.stdout

    # Sem config.toml o doctor precisa reprovar, e mandar rodar `init`.
    antes = orquestrador("doctor")
    assert antes.returncode != SUCESSO
    assert "orquestrador init" in antes.stdout

    assert orquestrador("init").returncode == SUCESSO
    assert (projeto / "config.toml").is_file()

    depois = orquestrador("doctor")
    # Segue reprovando — os campos PREENCHA continuam lá —, mas agora o que ele diz
    # é o que falta preencher, e os prompts empacotados já aparecem como OK.
    assert depois.returncode != SUCESSO
    assert "orquestrador" in depois.stdout and "prompts" in depois.stdout

    seco = orquestrador("--dry-run", "--recurso", "pedidos")
    assert seco.returncode != SUCESSO
    assert "fixtures" in seco.stdout

    # E o que motivou tudo: o prompt do estágio carrega do pacote instalado.
    carga = subprocess.run(  # noqa: S603
        [
            str(python),
            "-c",
            "from orquestrador.llm.montagem import carregar_prompt;"
            "print(len(carregar_prompt('mapeador')))",
        ],
        cwd=projeto,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert carga.returncode == 0, carga.stderr
    assert int(carga.stdout.strip()) > 0
