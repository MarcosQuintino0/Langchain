"""A documentação aponta para arquivos que existem.

Por que este arquivo existe
---------------------------
Três documentos deste repositório citaram, ao mesmo tempo,
`src/orquestrador/javascript.py`, `ferramentas/superficie.py` e `gates/parser.py` —
os três já movidos pela Etapa 3. Nenhuma checagem reprovou, porque documentação não
compila. O leitor seguinte gasta o tempo dele descobrindo que o mapa está errado, e
é pior que mapa nenhum: um mapa errado é seguido.

O que é verificado
------------------
* **Link Markdown relativo** — `](caminho)` que não seja `http`, `#` ou `mailto`
  resolve a partir do diretório do próprio arquivo.
* **Caminho entre crases** — só quando ele nomeia **um diretório nosso**. Ver
  `parece_nosso` para a razão de a checagem ser tão estreita.

O que não é verificado, e por quê
---------------------------------
* `docs/historico/` — documentos congelados. Corrigi-los destruiria o registro do
  que se pensava naquele dia. Ver `docs/historico/LEIA.md`.
* `prompts/` — conteúdo editorial dirigido ao LLM, que descreve o **projeto do
  consumidor** (`_support/api.js`, `crud.cy.js`). Nada ali é caminho deste
  repositório.
* Os caminhos entre crases de `docs/plano-de-execucao.md` — um roteiro nomeia, por
  construção, o que já não existe (a evidência que motivou um item) e o que ainda
  não existe (a estrutura-alvo). Exigir existência o obrigaria a mentir sobre os
  dois. Os **links** dele continuam verificados.
* Blocos cercados — árvore de diretórios e exemplo de saída não são citação.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DIR_TESTES = Path(__file__).resolve().parent
RAIZ_DO_REPOSITORIO = DIR_TESTES.parent
PACOTE = RAIZ_DO_REPOSITORIO / "src" / "orquestrador"

# Prefixos de caminho que não são verificados. Cada um tem um motivo escrito na
# docstring do módulo; acrescentar um aqui sem escrever o motivo lá é como desligar
# a checagem.
FORA_DA_VERIFICACAO = (("docs", "historico"), ("prompts",))

# Diretórios que não contêm fonte deste repositório: ambiente virtual, cache de
# ferramenta e artefato de execução. Um `LICENSE.md` de dependência não é
# documentação nossa, e um `.md` sob `.execucoes/` é saída, não fonte.
NAO_E_FONTE = frozenset(
    {
        ".venv",
        ".git",
        ".execucoes",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
    }
)

# Só os links deste arquivo são verificados; os caminhos entre crases, não.
SEM_CHECAGEM_DE_CRASE = Path("docs/plano-de-execucao.md")

# Um caminho entre crases é cobrado só quando o primeiro segmento é um diretório
# nosso. Fora disso ele pertence a outro projeto — a skill `qa-api`
# (`references/`, `scripts/cobertura/`) ou o projeto Cypress do consumidor
# (`_support/`) — e este repositório não tem como saber se existe.
RAIZES_DO_REPOSITORIO = frozenset({"src", "tests", "docs", "prompts", "fixtures", ".github"})

LINK = re.compile(r"\]\(([^)\s]+)\)")

# Exige a barra: `cli.py` sozinho é ambíguo (raiz do pacote? nome proibido citado
# numa regra?), e a célula da raiz do `AGENTS.md` já é conferida contra
# `RAIZ_PERMITIDA` por `test_estrutura_do_codigo.py`. Com barra, o caminho afirma
# onde o arquivo mora, e essa afirmação é verificável.
CRASE = re.compile(r"`([A-Za-z0-9_.][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*\.[a-z]{2,4})`")

# `pipeline.py:288-328` é citação de trecho, não de arquivo diferente.
ANCORA_DE_LINHA = re.compile(r":\d+(-\d+)?$")

CERCA = re.compile(r"^\s*(```|~~~)")


def documentos() -> list[Path]:
    """Todo `.md` do repositório, menos o que está declarado fora da verificação.

    Varredura do disco e não `git ls-files`: um subprocesso tornaria esta checagem
    `integration`, e o job de integração não roda na CI — a documentação deixaria de
    ser verificada exatamente onde mais importa. O preço é a lista
    `NAO_E_FONTE`, que precisa acompanhar as pastas de cache e de artefato.
    """
    achados: list[Path] = []
    for arquivo in RAIZ_DO_REPOSITORIO.rglob("*.md"):
        relativo = arquivo.relative_to(RAIZ_DO_REPOSITORIO)
        if set(relativo.parts) & NAO_E_FONTE:
            continue
        if any(relativo.parts[: len(fora)] == fora for fora in FORA_DA_VERIFICACAO):
            continue
        achados.append(relativo)
    return sorted(achados)


def linhas_fora_de_bloco(texto: str) -> list[tuple[int, str]]:
    """As linhas em prosa, numeradas a partir de 1.

    Alterna a cada cerca em vez de casar abertura com fechamento: bloco não fechado
    é erro de Markdown, e o efeito aqui — parar de verificar até o fim do arquivo —
    é conservador na direção certa.
    """
    dentro = False
    fora: list[tuple[int, str]] = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if CERCA.match(linha):
            dentro = not dentro
            continue
        if not dentro:
            fora.append((numero, linha))
    return fora


def parece_nosso(caminho: str) -> bool:
    primeiro = caminho.split("/")[0]
    return primeiro in RAIZES_DO_REPOSITORIO or (PACOTE / primeiro).is_dir()


def existe(caminho: str) -> bool:
    """Resolve contra as três bases em que um caminho é escrito neste repositório.

    A raiz, porque `tests/test_x.py` é como um documento cita um teste. E o pacote,
    porque a tabela do `AGENTS.md` escreve `llm/montagem.py` — dentro dela, o
    `src/orquestrador/` é subentendido, e escrevê-lo por extenso em toda linha
    tornaria a tabela ilegível.
    """
    return any((base / caminho).exists() for base in (RAIZ_DO_REPOSITORIO, PACOTE))


@pytest.mark.unit
@pytest.mark.parametrize("documento", documentos(), ids=lambda p: p.as_posix())
def test_link_relativo_resolve(documento: Path):
    texto = (RAIZ_DO_REPOSITORIO / documento).read_text(encoding="utf-8")
    quebrados: list[str] = []

    for numero, linha in linhas_fora_de_bloco(texto):
        for alvo in LINK.findall(linha):
            if alvo.startswith(("http://", "https://", "#", "mailto:")):
                continue
            arquivo = alvo.split("#")[0]
            if not arquivo:
                continue
            if not (RAIZ_DO_REPOSITORIO / documento.parent / arquivo).exists():
                quebrados.append(f"linha {numero}: {alvo}")

    assert not quebrados, (
        f"{documento.as_posix()} tem link para arquivo que não existe:\n  "
        + "\n  ".join(quebrados)
        + "\nO link é relativo ao diretório do próprio documento. Se o arquivo "
        "mudou de lugar, aponte para onde ele está; se ele deixou de existir, "
        "apague o link em vez de deixá-lo apontando para o vazio — link morto é "
        "seguido antes de ser conferido."
    )


@pytest.mark.unit
@pytest.mark.parametrize("documento", documentos(), ids=lambda p: p.as_posix())
def test_caminho_entre_crases_existe(documento: Path):
    if documento == SEM_CHECAGEM_DE_CRASE:
        pytest.skip("roteiro: nomeia o que já não existe e o que ainda não existe")

    texto = (RAIZ_DO_REPOSITORIO / documento).read_text(encoding="utf-8")
    inexistentes: list[str] = []

    for numero, linha in linhas_fora_de_bloco(texto):
        for citado in CRASE.findall(linha):
            caminho = ANCORA_DE_LINHA.sub("", citado)
            if not parece_nosso(caminho):
                continue
            if not existe(caminho):
                inexistentes.append(f"linha {numero}: {citado}")

    assert not inexistentes, (
        f"{documento.as_posix()} cita caminho que não existe:\n  "
        + "\n  ".join(inexistentes)
        + "\nA citação é resolvida a partir da raiz do repositório e de "
        "src/orquestrador/. Um documento que nomeia um módulo movido manda o "
        "leitor para o lugar errado com a confiança de quem sabe."
    )
