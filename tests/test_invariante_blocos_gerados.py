"""Tabela publicada é gerada, e o teste é o que garante que ela foi regenerada.

Por que este arquivo existe
---------------------------
Toda tabela de referência deste projeto já divergiu do código pelo menos uma vez.
O catálogo de eventos divergiu duas vezes no mesmo dia; a tabela de códigos
`QAORQ-` estava com quatro descrições diferentes das do `gates/codigos.py` quando
foi migrada para `docs/`. Ninguém errou: manter duas cópias sincronizadas à mão é
uma tarefa que não tem como dar certo indefinidamente.

O padrão é o mesmo em todos: um bloco entre marcadores HTML, e um gerador que mora
no **módulo dono** do dado. O teste compara os dois. Quando reprova, a resposta é
regenerar o bloco — nunca editar a tabela.

O modelo original é `test_observabilidade_eventos.py::test_o_catalogo_da_referencia_e_o_gerado`,
que continua lá porque o dono daquele catálogo é o enum de eventos.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from orquestrador.cli.codigos_de_saida import catalogo_markdown as catalogo_de_saida
from orquestrador.cli.principal import construir_analisador
from orquestrador.gates.codigos import catalogo_markdown as catalogo_qaorq

pytestmark = pytest.mark.unit

DOCS = Path(__file__).resolve().parent.parent / "docs"


def bloco(pagina: Path, marca: str) -> str:
    """O texto entre `<!-- INICIO DO {marca} ... -->` e `<!-- FIM DO {marca} -->`."""
    texto = pagina.read_text(encoding="utf-8")
    inicio_da_marca = f"<!-- INICIO {marca}"
    fim = f"<!-- FIM {marca} -->"

    assert inicio_da_marca in texto and fim in texto, (
        f"não achei os marcadores de {marca} em {pagina.name}.\n"
        f"O bloco fica entre `{inicio_da_marca} ... -->` e `{fim}`. Sem eles esta "
        "checagem fica cega, e a tabela volta a ser mantida à mão."
    )
    depois = texto.index("-->", texto.index(inicio_da_marca)) + len("-->")
    return texto[depois : texto.index(fim)].strip()


def conferir(pagina: Path, marca: str, gerado: str, dono: str) -> None:
    assert bloco(pagina, marca) == gerado.strip(), (
        f"o bloco {marca} de {pagina.name} não é o que {dono} gera.\n\n"
        "Não edite a tabela: ela é saída, não fonte. Edite o catálogo em "
        f"{dono} e cole o resultado de `catalogo_markdown()` entre os marcadores.\n\n"
        "--- o que o gerador produz agora ---\n"
        f"{gerado}"
    )


def test_a_tabela_de_codigos_de_saida_e_a_gerada():
    """Os códigos de 0 a 4 não estavam em `.md` nenhum antes desta página existir.

    Eram cinco constantes com um comentário ao lado, e o contrato com quem
    automatiza a execução — que é quem lê código de saída — não tinha onde ser
    consultado.
    """
    conferir(
        DOCS / "referencia" / "cli.md",
        "DOS CODIGOS DE SAIDA",
        catalogo_de_saida(),
        "cli/codigos_de_saida.py",
    )


def test_a_tabela_de_codigos_qaorq_e_a_gerada():
    conferir(
        DOCS / "referencia" / "codigos-de-violacao.md",
        "DOS CODIGOS QAORQ",
        catalogo_qaorq(),
        "gates/codigos.py",
    )


def test_a_ajuda_da_cli_publicada_e_a_que_o_argparse_produz():
    """Flag nova sem documentação reprova.

    A ajuda vem do próprio `argparse`, então a página não pode ficar para trás de um
    `add_argument`. É a checagem mais barata das três e a que pega o esquecimento
    mais comum.

    `COLUMNS` fixo porque o argparse quebra as linhas conforme o terminal de quem
    roda — sem isso, a mesma suíte passaria numa janela e reprovaria noutra.
    """
    largura_anterior = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "88"
    try:
        ajuda = construir_analisador().format_help().rstrip()
    finally:
        if largura_anterior is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = largura_anterior

    cercado = "```\n" + ajuda + "\n```"
    conferir(
        DOCS / "referencia" / "cli.md",
        "DA AJUDA",
        cercado,
        "cli/principal.py::construir_analisador",
    )


# ---------------------------------------------------------------------------
# O ferramental documentado é o ferramental instalado
# ---------------------------------------------------------------------------

# Ferramentas que a página cobre e que **não** são dependência do projeto. Elas
# entram por exceção declarada, e não por esquecimento: `pre-commit` é ferramenta de
# máquina (`pipx install`), e `uv` é usado por um único teste, que pula sem ele.
FORA_DOS_EXTRAS = frozenset({"pre-commit", "uv"})


def _ferramentas_dos_extras() -> set[str]:
    """Os nomes de pacote dos extras `dev` e `docs`, sem versão."""
    pyproject = (DOCS.parent / "pyproject.toml").read_text(encoding="utf-8")
    nomes: set[str] = set()
    for extra in ("dev = [", "docs = ["):
        bloco = pyproject[pyproject.index(extra) :]
        bloco = bloco[: bloco.index("\n]")]
        nomes |= set(re.findall(r'"([A-Za-z][\w.-]*)[=<>~]', bloco))
    return nomes


# As transitivas não precisam de parágrafo: quem lê a página quer saber por que o
# Ruff existe, não por que o `babel` veio junto com o MkDocs.
DIRETAS = frozenset(
    {"pytest", "ruff", "pyright", "pytest-cov", "coverage", "mkdocs", "mkdocs-material"}
)


def test_o_ferramental_documentado_cobre_o_que_esta_instalado():
    """Ferramenta nova sem parágrafo reprova, e parágrafo sem ferramenta também.

    A página responde, para cada uma, "o que aconteceria sem ela?". Uma ferramenta
    que entra no extra e não ganha resposta é uma que ninguém sabe por que está lá —
    e é a primeira a ser removida por engano, ou a primeira a ficar quando já não
    serve.
    """
    pagina = (DOCS / "desenvolvimento" / "ferramental.md").read_text(encoding="utf-8")
    # `-` e ` ` são a mesma coisa aqui: o pacote é `mkdocs-material` e o título é
    # "MkDocs Material". Exigir a grafia do PyPI num cabeçalho de prosa faria a
    # documentação ler como um `requirements.txt`.
    titulos = {
        titulo.strip().lower().replace(" ", "-")
        for titulo in re.findall(r"^## ([^\n—]+)", pagina, re.M)
    }

    esperadas = (_ferramentas_dos_extras() & DIRETAS) | FORA_DOS_EXTRAS
    sem_paragrafo = sorted(
        nome for nome in esperadas if not any(nome.lower() in titulo for titulo in titulos)
    )
    assert not sem_paragrafo, (
        f"ferramenta(s) sem parágrafo em ferramental.md: {sem_paragrafo}.\n"
        "Escreva o que ela faz aqui e, principalmente, o que aconteceria sem ela. "
        "Se ela não é ferramenta de desenvolvimento e sim transitiva, ela não "
        "pertence a DIRETAS neste teste."
    )
