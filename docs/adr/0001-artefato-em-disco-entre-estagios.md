# ADR 0001 — Artefato em disco entre estágios

**Status:** Aceita

## Contexto

Um pipeline de vários estágios de LLM pode passar contexto de duas formas: mantendo a conversa viva entre eles, ou escrevendo o resultado em disco e relendo. A primeira é o padrão dos frameworks de agente.

## Decisão

O que trafega entre estágios é **artefato em disco**, nunca histórico de conversa. `graph.json`, `inventario.json`, `_support/cobertura.json`, os `.cy.js` e o `report.json` são a única memória compartilhada.

## Consequências

Qualquer agente pode morrer e ser reinstanciado do zero sem perda. Em troca, o pipeline escreve mais em disco e depende de serialização correta — que é onde metade dos defeitos mora, e por isso há goldens congelando o formato de fio.
