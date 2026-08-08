# ADR 0005 — A raiz do pacote é lista fechada

**Status:** Aceita

## Contexto

A raiz tinha doze arquivos sem dono declarado. Foi assim que `parser.py`, `textos.py` e `javascript.py` chegaram onde estavam: um agente lê a regra pela metade, acrescenta um arquivo na raiz, e o revisor não repara.

## Decisão

A raiz aceita cinco arquivos, listados em `RAIZ_PERMITIDA`. Arquivo novo ali reprova a suíte. A lista **só encolhe**.

## Consequências

Escolher diretório passa a ser escolher o motivo dominante de mudança, e a raiz deixa de ser 'onde ainda não decidi'. O custo é que criar um módulo novo exige decidir a que pacote ele pertence — que é exatamente a decisão que estava sendo adiada.
