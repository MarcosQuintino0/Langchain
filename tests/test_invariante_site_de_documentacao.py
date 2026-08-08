"""A navegação do site e os arquivos em `docs/` são a mesma lista.

Por que este arquivo existe
---------------------------
Página que ninguém linkou é página que ninguém acha — e uma que só existe na
navegação é um link morto que o leitor descobre clicando. As duas falhas são
silenciosas: o `mkdocs build` sem `--strict` avisa e segue.

Este teste faz o papel que o `mkdocs.yml` sozinho não faz: cobra a lista **nos dois
sentidos**, e roda em `pytest`, sem depender do extra `[docs]` estar instalado. O
`--strict` da CI continua sendo a segunda linha, para link quebrado *dentro* das
páginas.

Também é aqui que mora a checagem de numeração dos ADRs: lacuna ou repetição num
conjunto que se cita por número (`ver ADR 0007`) transforma a referência em
adivinhação.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
MKDOCS = RAIZ / "mkdocs.yml"

# Não é verificado, e cada exclusão tem motivo escrito aqui.
FORA_DA_NAV = (
    # Documentos congelados: as duas revisões externas e o prompt que encomendou a
    # segunda. Ver docs/historico/LEIA.md.
    ("historico",),
)

# Um item de nav é `- Rótulo: caminho/pagina.md`. Ler por regex, e não com
# `yaml.safe_load`, porque o `mkdocs.yml` usa a tag `!!python/name:` do
# pymdownx — que o loader seguro recusa, e o inseguro não tem por que entrar
# na suíte.
ITEM_DA_NAV = re.compile(r"^\s*- (?:[^:]+: )?([\w./-]+\.md)\s*$", re.M)

STATUS_VALIDOS = frozenset({"Aceita", "Superada", "Template"})


def paginas_no_disco() -> set[str]:
    return {
        pagina.relative_to(DOCS).as_posix()
        for pagina in DOCS.rglob("*.md")
        if not any(pagina.relative_to(DOCS).parts[: len(fora)] == fora for fora in FORA_DA_NAV)
    }


def paginas_na_nav() -> set[str]:
    texto = MKDOCS.read_text(encoding="utf-8")
    nav = texto[texto.index("\nnav:") :]
    return set(ITEM_DA_NAV.findall(nav))


def test_toda_pagina_do_disco_esta_na_navegacao():
    faltando = sorted(paginas_no_disco() - paginas_na_nav())
    assert not faltando, (
        f"página(s) em docs/ fora da nav do mkdocs.yml: {faltando}.\n"
        "Página que ninguém linkou é página que ninguém acha — e o `mkdocs build "
        "--strict` da CI reprova por isso também, só que mais tarde.\n"
        "Acrescente a entrada na `nav`, na seção em que o leitor a procuraria."
    )


def test_toda_pagina_da_navegacao_existe():
    fantasmas = sorted(paginas_na_nav() - paginas_no_disco())
    assert not fantasmas, (
        f"a nav do mkdocs.yml aponta para página(s) que não existem: {fantasmas}.\n"
        "Se o arquivo foi renomeado, corrija a entrada; se foi apagado, remova-a."
    )


def test_a_numeracao_dos_adrs_nao_tem_lacuna_nem_repeticao():
    """ADR é citada por número. `ver ADR 0007` precisa ter uma resposta só."""
    numeros = sorted(int(pagina.stem.split("-")[0]) for pagina in (DOCS / "adr").glob("0*.md"))

    assert len(numeros) == len(set(numeros)), f"número repetido entre os ADRs: {numeros}"
    assert numeros == list(range(len(numeros))), (
        f"a numeração dos ADRs tem lacuna: {numeros}.\n"
        "Ela é sequencial a partir de 0000 (o template). Número pulado faz quem "
        "procura o ADR ausente concluir que a busca dele é que está errada."
    )


@pytest.mark.parametrize(
    "adr", sorted((DOCS / "adr").glob("0*.md")), ids=lambda p: p.stem.split("-")[0]
)
def test_todo_adr_declara_status_e_as_tres_secoes(adr: Path):
    """Um ADR sem Consequências é uma decisão sem custo declarado.

    E decisão sem custo declarado é a que ninguém consegue rever depois: o leitor
    não sabe o que estava sendo trocado por quê.
    """
    texto = adr.read_text(encoding="utf-8")

    status = re.search(r"^\*\*Status:\*\* (\w+)$", texto, re.M)
    assert status and status.group(1) in STATUS_VALIDOS, (
        f"{adr.name} não declara um `**Status:**` de "
        f"{sorted(STATUS_VALIDOS)}.\nADR aceita é imutável; quando a realidade muda, "
        "escreve-se outra que a supersede e esta vira `Superada`."
    )

    faltando = [
        secao for secao in ("## Contexto", "## Decisão", "## Consequências") if secao not in texto
    ]
    assert not faltando, (
        f"{adr.name} não tem a(s) seção(ões) {faltando}.\n"
        "As três são obrigatórias. Consequências é a que mais se esquece e a que "
        "mais importa: uma lista só de vantagens é sinal de que a alternativa não "
        "foi levada a sério."
    )
