# ADR 0009 — CI só em Windows e Python 3.13

**Status:** Aceita

## Contexto

A revisão externa sugeriu matriz com Ubuntu e Python 3.12. Este projeto conversa com o sistema de arquivos e com subprocessos o tempo todo — confinamento de caminho, `node ... .mjs`, comparação sem diferenciar caixa, junction do Windows.

## Decisão

`windows-latest` e Python 3.13, e nada mais.

## Consequências

O verde da CI diz algo sobre a máquina de quem usa isto. Rodar em Ubuntu daria um verde que não diz nada sobre o caso que importa. Se aparecer usuário em Linux ou em 3.12, a matriz volta — mas por demanda, não por simetria.
