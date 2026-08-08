"""Tags `@endpoint` / `@cat` de um spec Cypress — parser puro.

Entra o texto do `.cy.js`, sai o conjunto de pares que ele declara. Sem I/O e sem
dependência do projeto, como todo módulo deste pacote.

Autoridade não mora aqui
------------------------
Quem tem autoridade sobre a contagem de cobertura é o `qa-cobertura.mjs` da skill.
O que sai daqui serve para **nomear** o que faltou num delta, nunca para decidir
se faltou. Por isso o parser é deliberadamente literal: ele não resolve template,
e avisa quantas tags ficaram por resolver em vez de fingir que leu todas.

Separado de `exports_javascript.py` porque muda por outro motivo: aquele acompanha
a sintaxe de `export` do JavaScript, este acompanha a convenção de marcação que a
skill `qa-api` exige em cada `it`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `@endpoint MÉTODO /rota  @cat CAT-07` — a marcação que a skill exige em cada `it`.
# A rota vai até dois espaços ou o fim da linha, porque `@cat` costuma vir alinhado
# depois dela.
_TAG = re.compile(r"@endpoint\s+(?P<endpoint>[A-Z]+\s+\S+?)\s{1,}@cat\s+(?P<cat>CAT-\d{2}|\S+)")


@dataclass(frozen=True)
class TagsDoSpec:
    """O que as tags de um spec dizem, e o quanto disso é confiável.

    `dinamicas` conta as tags cujo valor é template (`@cat ${...}`), usadas na forma
    data-driven que a skill permite. Elas existem, mas só resolvem em tempo de
    execução — este parser não as resolve, e quem usa `pares` precisa saber que a
    lista está incompleta quando `dinamicas` não é zero.
    """

    pares: set[tuple[str, str]]
    dinamicas: int


def extrair_tags(fonte: str) -> TagsDoSpec:
    """Pares `(endpoint, categoria)` marcados no spec."""
    pares: set[tuple[str, str]] = set()
    dinamicas = 0
    for casamento in _TAG.finditer(fonte):
        endpoint = " ".join(casamento.group("endpoint").split())
        cat = casamento.group("cat")
        if "${" in endpoint or "${" in cat:
            dinamicas += 1
            continue
        pares.add((endpoint, cat))
    return TagsDoSpec(pares=pares, dinamicas=dinamicas)
