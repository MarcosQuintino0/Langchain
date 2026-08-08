"""Resolução da raiz do projeto — **o único lugar que usa `Path(__file__)`**.

Havia cinco módulos calculando a raiz por conta própria com
`Path(__file__).parent`. Depois que o código desceu para `src/orquestrador/`, cada
um deles passaria a apontar para dentro do pacote, e uma saída configurada como
`.execucoes` iria parar em `src/orquestrador/.execucoes/` — sem erro nenhum, só no
lugar errado. Contagem de níveis replicada é exatamente o tipo de coisa que fica
errada em silêncio, então ela mora aqui, uma vez.

Duas situações, não uma
-----------------------
`parents[2]` só é a raiz do projeto quando o pacote está rodando **de dentro do
checkout**. Instalado a partir do wheel, `parents[2]` é o `Lib/` do ambiente
virtual, e `config.toml` passaria a ser procurado lá dentro — o comando falharia
apontando um caminho que não diz nada a quem instalou. Por isso a árvore de fontes
é **detectada por marca** (`src/orquestrador/raiz.py` existir sob a candidata), e
não presumida: instalado, a raiz é o diretório de trabalho, que é onde
`orquestrador init` escreve o `config.toml` do projeto.

Onde moram os prompts
---------------------
`prompts/` continua **fora de `src/`**, na raiz do repositório: é conteúdo
editorial, iterado por quem não mexe em Python (`AGENTS.md`). O wheel, porém,
precisa levá-los — sem eles não há estágio de LLM.

A conciliação é de **empacotamento**, não de cópia: o `pyproject.toml` mapeia o
diretório `prompts/` da raiz para o pacote `orquestrador.prompts`
(`[tool.setuptools.package-dir]`). O build lê os arquivos de onde eles já moram e
os grava no wheel em `orquestrador/prompts/`. Não existe segunda cópia versionada
para divergir da primeira: no checkout só existe `prompts/`, e no ambiente
instalado só existe `orquestrador/prompts/`.

Daí a ordem de `_dir_prompts`: o diretório empacotado, quando existe, é o único que
pode existir — a árvore de fontes nunca o tem. Sem ele, estamos num checkout, e
vale `RAIZ_PROJETO / "prompts"`. Quem quiser apontar outro diretório usa
`[caminhos].prompts` na configuração, que é o override declarado.

`fixtures/` e `config.toml` **não** vão para o wheel, e é deliberado: o primeiro é
dado de desenvolvimento do `--dry-run`, o segundo é do usuário e nasce de
`orquestrador init`.

`ORQUESTRADOR_RAIZ` permite apontar outra raiz — útil para rodar o pacote instalado
contra um checkout, ou para fixar a raiz num agendamento onde o diretório de
trabalho não é confiável.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

VARIAVEL_DE_RAIZ = "ORQUESTRADOR_RAIZ"

NOME_DOS_PROMPTS = "prompts"


def _detectar_arvore_de_fontes() -> Path | None:
    """A raiz do checkout, ou `None` quando o pacote está instalado fora dela.

    A marca é o próprio arquivo: se `<candidata>/src/orquestrador/raiz.py` existe,
    `parents[2]` é mesmo a raiz do repositório. Checar só `src/` seria frouxo —
    qualquer projeto do usuário com um `src/` viraria "checkout do orquestrador".
    """
    # src/orquestrador/raiz.py -> src/orquestrador -> src -> raiz do repositório
    candidata = Path(__file__).resolve().parents[2]
    return candidata if (candidata / "src" / "orquestrador" / "raiz.py").is_file() else None


ARVORE_DE_FONTES: Path | None = _detectar_arvore_de_fontes()


def _resolver() -> Path:
    bruto = os.environ.get(VARIAVEL_DE_RAIZ, "").strip()
    if bruto:
        return Path(bruto).expanduser().resolve()
    if ARVORE_DE_FONTES is not None:
        return ARVORE_DE_FONTES
    # Instalado: a raiz é onde o usuário está. É onde `orquestrador init` escreve o
    # `config.toml` e onde `Config.carregar()` sem argumento vai procurá-lo.
    return Path.cwd()


RAIZ_PROJETO: Path = _resolver()


def _prompts_empacotados() -> Path | None:
    """`orquestrador/prompts/` do wheel, ou `None` num checkout.

    `resources.files()` em vez de `__file__ / "prompts"` porque é a API que continua
    correta se o pacote for relocado — e ela devolve um `Path` real aqui, já que o
    wheel é instalado descompactado. O `isinstance` é o que torna essa suposição
    verificada em vez de presumida: num carregador exótico (zipimport), a resposta
    honesta é "não há prompts empacotados", não um `Path` inventado.
    """
    recurso = resources.files("orquestrador").joinpath(NOME_DOS_PROMPTS)
    return recurso if isinstance(recurso, Path) and recurso.is_dir() else None


def _dir_prompts() -> Path:
    empacotados = _prompts_empacotados()
    if empacotados is not None:
        return empacotados
    return RAIZ_PROJETO / NOME_DOS_PROMPTS


CONFIG_PADRAO: Path = RAIZ_PROJETO / "config.toml"
DIR_PROMPTS_PADRAO: Path = _dir_prompts()
DIR_FIXTURES: Path = RAIZ_PROJETO / "fixtures"
ARQUIVO_ENV: Path = RAIZ_PROJETO / ".env"
