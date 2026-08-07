"""Gates determinísticos. Princípio 4: quem reprova é script; LLM só cria.

Os códigos `QAAPI-0xx` vêm dos scripts da skill. Os códigos abaixo são do próprio
orquestrador e usam o prefixo `QAORQ-` para nunca colidirem com os da skill.
"""

CODIGOS_DO_ORQUESTRADOR: dict[str, str] = {
    "QAORQ-001": "diff grafo × manifesto ainda não implementado (stub da Fase 1)",
    "QAORQ-002": "endpoint presente no grafo/inventário e ausente do manifesto",
    "QAORQ-003": "endpoint do manifesto sem correspondente no inventário",
    "QAORQ-010": "saída do modelo não valida contra o contrato Pydantic do estágio",
    "QAORQ-011": "o modelo não devolveu JSON no formato pedido",
    "QAORQ-020": "prettier reprovou a formatação",
    "QAORQ-021": "eslint reprovou o código",
    "QAORQ-022": "formatador configurado mas ausente do PATH",
}
