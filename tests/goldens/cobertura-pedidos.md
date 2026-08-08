# `cobertura-pedidos.json`

O `_support/cobertura.json` que o mapeador entrega na tentativa aprovada do
roteiro de `--dry-run`. **É o contrato de fio com a skill.**

## O que ele prova

Que `Manifesto.para_json()` produz exatamente o dialeto que o
`validar-suite-gerada.mjs` lê:

* chaves em `camelCase` (`naoAplica`, `schemaEntrada`) — `by_alias=True`;
* campo ausente **omitido**, nunca `null` — `exclude_none=True`;
* `indent=2`, `ensure_ascii=False`, `\n` no fim e sem CRLF.

Nenhuma dessas quatro é decorativa. Um `by_alias` esquecido produz um arquivo que o
Python relê sem reclamar e que o Node recusa — e a recusa chega como falha de gate
num recurso, longe da causa.

## O que é mudança legítima

Mudança de forma acordada com o repositório da skill, sempre acompanhada de uma
nova impressão digital em `[skill].impressao_esperada`. Se a skill não mudou e este
arquivo mudou, o defeito é nosso.

## Por que não vem de uma execução real

Vem do roteiro de fixture, e não do dry-run: o que está sob teste é a
**serialização**. Amarrá-la ao Node faria este golden só rodar onde a máquina está
preparada — que é justamente onde ele menos importa.
