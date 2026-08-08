"""O vocabulário HTTP que inventário e manifesto compartilham.

Trinta linhas num arquivo só, e é deliberado. `normalizar_endpoint` e
`METODOS_HTTP` são o **único** vocabulário comum entre `inventario.py` (o que o
backend expõe) e `manifesto.py` (o que o gabarito declara), e cruzar os dois é
exatamente o que o Gate A faz. Dentro de qualquer um deles, o outro precisaria de
um import cruzado — a rota que faz alguém procurar a definição no arquivo errado.

`normalizar_endpoint` espelha `normalizarEndpoint` de `comum.mjs`: as duas formas
canônicas têm de coincidir, ou o diff do Gate A acusa diferença onde não há."""

from __future__ import annotations

import re

METODOS_HTTP: tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
)

# Métodos que o gate considera escrita (campos/reconciliar.mjs).
METODOS_DE_ESCRITA = frozenset({"POST", "PUT", "PATCH"})


def normalizar_endpoint(valor: str) -> str:
    """Forma canônica de um endpoint, igual a `normalizarEndpoint` de comum.mjs."""
    return re.sub(r"\s+", " ", str(valor).strip())
