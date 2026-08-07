"""Resolução da raiz do projeto — **o único lugar que usa `Path(__file__)`**.

Havia cinco módulos calculando a raiz por conta própria com
`Path(__file__).parent`. Depois que o código desceu para `src/orquestrador/`, cada
um deles passaria a apontar para dentro do pacote, e uma saída configurada como
`.execucoes` iria parar em `src/orquestrador/.execucoes/` — sem erro nenhum, só no
lugar errado. Contagem de níveis replicada é exatamente o tipo de coisa que fica
errada em silêncio, então ela mora aqui, uma vez.

Os diretórios abaixo ficam **fora** do pacote de propósito:

* `prompts/` é conteúdo editorial, iterado na Fase 2 por quem não mexe em código;
* `fixtures/` e `config.toml` são dados do projeto, não do pacote instalável.

`ORQUESTRADOR_RAIZ` permite apontar outra raiz — útil se um dia o pacote for
instalado fora da árvore de fontes, quando `__file__` deixa de valer.
"""

from __future__ import annotations

import os
from pathlib import Path

VARIAVEL_DE_RAIZ = "ORQUESTRADOR_RAIZ"


def _resolver() -> Path:
    bruto = os.environ.get(VARIAVEL_DE_RAIZ, "").strip()
    if bruto:
        return Path(bruto).expanduser().resolve()
    # src/orquestrador/raiz.py -> src/orquestrador -> src -> raiz do projeto
    return Path(__file__).resolve().parents[2]


RAIZ_PROJETO: Path = _resolver()

CONFIG_PADRAO: Path = RAIZ_PROJETO / "config.toml"
DIR_PROMPTS_PADRAO: Path = RAIZ_PROJETO / "prompts"
DIR_FIXTURES: Path = RAIZ_PROJETO / "fixtures"
ARQUIVO_ENV: Path = RAIZ_PROJETO / ".env"
