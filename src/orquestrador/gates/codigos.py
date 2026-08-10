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
    "QAORQ-001": "aviso: trecho do backend que o diff grafo × manifesto não conseguiu resolver",
    "QAORQ-002": "endpoint presente no backend e ausente do manifesto",
    "QAORQ-003": "endpoint do manifesto sem correspondente no backend",
    "QAORQ-010": "saída do modelo não valida contra o contrato Pydantic do estágio",
    "QAORQ-011": "o modelo não devolveu JSON no formato pedido",
    "QAORQ-020": "prettier reprovou a formatação",
    "QAORQ-021": "eslint reprovou o código",
    "QAORQ-022": "formatador configurado mas ausente do PATH",
    "QAORQ-030": "categoria declarada em cats sem nenhum `it` que a cubra",
    "QAORQ-040": "schema preservado do consumidor não declara campo que o mapeador achou",
    "QAORQ-050": "o plano de cenários não cobre uma categoria que o gabarito declara em cats",
}


def catalogo_markdown() -> str:
    """A tabela dos códigos `QAORQ-`, como ela é publicada.

    Gerada a partir do dicionário acima, e não escrita à mão, pelo mesmo motivo do
    catálogo de eventos: uma tabela mantida em paralelo diverge, e diverge no código
    novo — que é justamente o que ninguém conhece de cabeça.

    Os `QAAPI-` não saem daqui. Eles são da skill, e inventar uma tabela nossa para
    eles criaria uma segunda fonte de verdade sobre um contrato que não é nosso.
    """
    linhas = ["| Código | O que significa |", "| --- | --- |"]
    linhas += [
        f"| `{codigo}` | {texto} |" for codigo, texto in sorted(CODIGOS_DO_ORQUESTRADOR.items())
    ]
    return "\n".join(linhas)
