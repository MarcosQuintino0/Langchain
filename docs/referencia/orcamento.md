# Orçamento

Um teto de custo que **para antes da próxima chamada** — e que não promete mais
que isso.

## O que ele promete, e o que não

**Promete:** nenhuma chamada nova começa depois que o teto foi alcançado.

**Não promete:** que o teto não será ultrapassado. O custo de uma chamada só se
conhece depois dela — o modelo decide quantos tokens de saída emite, e a resposta
de uma tool pode vir muito maior do que o esperado. A última chamada pode passar.

A distinção está no nome dos métodos (`excedido`, e não `garantido`) e nesta
página porque prometer o contrário seria a mesma categoria de mentira que o
projeto inteiro existe para evitar.

## Dois escopos, sempre

```toml
[orcamento.por_execucao]
chamadas = 200
tokens = 5_000_000

[orcamento.por_recurso]
chamadas = 40
tokens = 800_000
```

Os dois valem juntos, e é preciso: só o de execução deixaria um recurso patológico
consumir tudo antes de o segundo começar; só o de recurso não pararia uma lista de
trinta recursos que sangram devagar.

Quando os dois estouram, a mensagem cita o **do recurso** — é o mais específico e o
mais acionável. Quem lê "teto por recurso alcançado em `pedidos`" sabe onde olhar;
quem lê o da execução inteira ainda precisa descobrir qual recurso consumiu.

## Quatro tetos por escopo

| Campo | O que conta |
| --- | --- |
| `chamadas` | chamadas ao modelo, incluindo as voltas do mini-loop de schema |
| `tokens` | entrada + saída, somados de todas as chamadas |
| `caracteres_de_tools` | o que as tools devolveram — é multiplicador, não parcela |
| `segundos` | tempo de chamada de modelo e de tool, não o do pipeline em volta |

**Campo ausente significa sem teto, e zero é um teto legítimo.** A distinção existe
porque `0` é uma escolha válida — "não me deixe chamar o modelo" — e um campo
ausente que virasse zero mataria toda execução sem `[orcamento]` na primeira
tentativa, com uma mensagem sobre um teto que ninguém configurou.

Sem nenhum teto, o custo da checagem é uma comparação por chamada. É o padrão:
orçamento que aparece sem ninguém pedir interrompe execução legítima e ensina a
desligá-lo.

## Onde a checagem acontece

Dentro dos **agentes**, antes de cada volta de modelo — inclusive dentro do
mini-loop de schema do mapeador, que dá várias voltas por tentativa e é o laço mais
caro do pipeline. Uma guarda no ciclo de reparo deixaria esse laço inteiro passar
batido.

Três peças, três donos, e é a separação que faz a regra caber em cada um:

| Peça | Papel |
| --- | --- |
| `dominio/orcamento.py` | **decide** — compara gasto com teto, sem saber de nada |
| `observabilidade/telemetria.py` | **mede** — devolve o `Consumo` dos dois escopos |
| `agentes/guarda_de_orcamento.py` | **interrompe** — a única linha que levanta |

A alternativa óbvia era pôr a checagem dentro da `Telemetria`, que é onde os
números já estão. Ela está errada por uma regra do `AGENTS.md`: observabilidade
nunca decide fluxo. Uma telemetria que levanta deixa de ser instrumento e vira
gate — e um gate que ninguém procura ali é o pior tipo de gate.

## O código de saída é `5`

Não é falha: é a execução obedecendo. `2` pede para arrumar o ambiente, `4` para
esperar e repetir; `5` pede uma decisão — se o trabalho valia mais do que o teto
autorizava. Num agendamento, as três levam a coisas diferentes.

## `--estimar`: o tamanho antes de gastar

```bash
orquestrador --estimar
```

Roda **só o Bloco 0**, conta os endpoints do backend e multiplica pela faixa de
`[orcamento.estimativa]`. Nenhum modelo é chamado, nenhum arquivo é tocado, e nem
sequer um diretório de execução é criado.

O agrupamento é por **classe controladora**, e não por recurso: o extrator lê o
backend e não sabe a que recurso um endpoint pertence — quem decide isso é o
mapeador, no Bloco 1.

**A faixa é ordem de grandeza, não previsão.** Os padrões saíram de *uma* execução
medida, e uma medição é pouca evidência — por isso eles estão em configuração, para
serem trocados pelos números da sua própria execução. O que não se sabe antes de
rodar é justamente o que domina o custo: quantas voltas de reparo cada gate vai
exigir.

**Não estima moeda.** Preço é do modelo, e nenhum nome de modelo aparece em código
(princípio 6). Quem quiser moeda multiplica a faixa de token pelo preço que o
provedor cobra pelo modelo que *ele* configurou — número que muda por decisão
comercial de terceiro, não por mudança nossa.
