# ADR 0015 — O teto para antes da próxima chamada, e não promete mais

**Status:** Aceita

## Contexto

O item 5.3 do plano pedia "tetos duros por execução e por recurso: chamadas,
tokens, bytes de ferramenta, tempo e moeda", com "interrupção antes da chamada que
estouraria o teto".

A frase esconde uma impossibilidade. O custo de uma chamada só é conhecido
**depois** dela: o modelo decide quantos tokens de saída emite, e a resposta de uma
tool pode vir muito maior do que o esperado. Não há como saber, antes de enviar, se
*aquela* chamada vai estourar.

## Decisão

O teto para **antes da próxima chamada, depois de ter sido alcançado** — comparação
com `>=` sobre o consumo acumulado. A última chamada pode ultrapassar, e isso está
escrito na docstring do módulo, na página de referência e no template do
`orquestrador init`.

Moeda fica de fora. Preço é do modelo, e nenhum nome de modelo aparece em código
(princípio 6); uma tabela de preços aqui seria um dado de terceiro envelhecendo
dentro do repositório.

A decisão de parar é pura (`dominio/orcamento.py`), a medição é da telemetria, e a
interrupção é do agente (`agentes/guarda_de_orcamento.py`). A checagem fica **dentro
dos agentes**, e não no ciclo de reparo, porque o mini-loop de schema do mapeador dá
várias voltas de modelo por tentativa — e é o laço mais caro do pipeline.

## Consequências

Quem configura um teto de 800 mil tokens pode terminar com 830 mil. É pouco, é
limitado pelo tamanho de uma chamada, e é honesto — prometer o contrário
transformaria o orçamento numa garantia que ele quebraria na primeira vez que
importasse.

A checagem não pôde morar na `Telemetria`, que é onde os números já estão:
observabilidade nunca decide fluxo (`AGENTS.md`). O custo é uma peça a mais; o
ganho é que a telemetria continua sendo instrumento, e não um gate escondido.

Código de saída próprio (`5`), porque a resposta de quem opera é distinta: não é
arrumar o ambiente nem esperar, é decidir se o trabalho valia mais do que o teto
autorizava.
