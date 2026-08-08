# ADR 0004 — Três estados de veredito, não um booleano

**Status:** Aceita

## Contexto

Falha de ferramenta era tratada como aprovação. Um `qa-cobertura.mjs` que não devolvia contadores produzia um gate aprovado.

## Decisão

`VereditoDeGate` tem `APROVADO`, `REPROVADO` e `ERRO_DA_FERRAMENTA`. O terceiro interrompe o recurso com mensagem acionável e **nunca** entra em `delta.violacoes`.

## Consequências

Gate falha fechado: ausência de evidência nunca produz aprovado. Mandar o modelo consertar um script que não rodou queimaria tentativa sem chance de convergir, e é por isso que o terceiro estado não vira violação.
