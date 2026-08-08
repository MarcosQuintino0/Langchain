# ADR 0013 — Sem fachada de compatibilidade em movimento de módulo

**Status:** Aceita

## Contexto

Ao dividir `contratos.py` em `dominio/`, a alternativa óbvia era manter uma fachada reexportando as mesmas classes, para dividir o commit.

## Decisão

Movimento de módulo é atômico: os importadores mudam no mesmo commit. Sem fachada, sem shim, sem período de transição.

## Consequências

O diff é maior, e é legível — `git diff -M` o mostra como rename parcial. Em troca, não existe o estado intermediário em que o teste da lista fechada fica verde com o arquivo antigo presente, e nada pressiona para terminar. Vale porque os importadores são todos daqui: numa biblioteca com consumidores externos a decisão seria outra.
