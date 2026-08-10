"""O vocabulário HTTP que os artefatos de domínio compartilham.

Poucas linhas num arquivo só, e é deliberado. `normalizar_endpoint`,
`exigir_endpoint_canonico` e `METODOS_HTTP` são o **único** vocabulário comum
entre `inventario.py` (o que o backend expõe), `manifesto.py` (o que o gabarito
declara) e `dossie.py` (o que o mapeador leu sobre comportamento). Cruzar
inventário e manifesto é o que o Gate A faz, e a fatia por endpoint do
planejador só encontra as regras do dossiê se as grafias coincidirem. Dentro de
qualquer um deles, os outros precisariam de import cruzado — a rota que faz
alguém procurar a definição no arquivo errado.

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


def exigir_endpoint_canonico(valor: str) -> str:
    """A forma canônica `MÉTODO /rota/completa`, recusando em vez de corrigir.

    Normalizar em silêncio esconderia o desvio de quem o emitiu; recusar o
    transforma num delta de schema — o reparo mais barato que existe. As
    mensagens citam a forma esperada porque são elas que voltam ao modelo.
    """
    canonico = normalizar_endpoint(valor)
    if valor != canonico:
        raise ValueError(f'endpoint fora da forma canônica "{canonico}": {valor!r}')
    partes = canonico.split(" ", 1)
    if len(partes) != 2 or partes[0] not in METODOS_HTTP or not partes[1].startswith("/"):
        raise ValueError(f'endpoint deve ter a forma "MÉTODO /rota/completa": {valor!r}')
    return canonico
