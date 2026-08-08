"""Catálogo dos códigos de violação emitidos pelo próprio orquestrador.

Os `QAAPI-0xx` vêm dos scripts da skill e são catalogados lá. Os daqui usam o
prefixo `QAORQ-` para nunca colidirem com eles.

O catálogo mora num módulo próprio, e não no `__init__.py` do pacote, porque
importar um código de violação não pode custar a importação de todos os gates —
e porque `__init__` que carrega conteúdo é o lugar onde nomes vão parar por
acidente, em vez de por decisão.
"""

from __future__ import annotations

CODIGOS_DO_ORQUESTRADOR: dict[str, str] = {
    "QAORQ-001": "diff grafo × manifesto ainda não implementado (stub da Fase 1)",
    "QAORQ-002": "endpoint presente no grafo/inventário e ausente do manifesto",
    "QAORQ-003": "endpoint do manifesto sem correspondente no inventário",
    "QAORQ-010": "saída do modelo não valida contra o contrato Pydantic do estágio",
    "QAORQ-011": "o modelo não devolveu JSON no formato pedido",
    "QAORQ-020": "prettier reprovou a formatação",
    "QAORQ-021": "eslint reprovou o código",
    "QAORQ-022": "formatador configurado mas ausente do PATH",
    "QAORQ-030": "categoria declarada em cats sem nenhum `it` que a cubra",
}
