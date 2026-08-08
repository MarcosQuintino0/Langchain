# ADR 0007 — Escrita transacional por staging e diário

**Status:** Aceita

## Contexto

O executor gravava direto no diretório do recurso, no projeto do consumidor. Uma tentativa ruim do loop de reparo sobrescrevia trabalho de quem nos contratou, sem volta.

## Decisão

Cada recurso trabalha numa área de staging, irmã do destino. O projeto do consumidor é tocado **uma vez**, depois que os dois gates aprovaram. Um diário de propriedade registra o que criamos e o que modificamos, com hash.

## Consequências

O que uma tentativa ruim destrói é a tentativa anterior. O diário vive acima da execução porque 'este arquivo é nosso?' atravessa execuções — um spec que criamos na terça e que alguém editou na quarta não é nosso para apagar na quinta.
