# `endpoints-java-spring.json`

O que o adaptador Java/Spring extrai de `fixtures/backends/java-spring/`. É o golden
que responde **"a precisão caiu?"** depois de mexer no parser de rotas.

## O que o fixture exercita, arquivo a arquivo

| Arquivo | Caso |
| --- | --- |
| `PedidoController` | prefixo `@RequestMapping` na classe + as cinco anotações derivadas |
| `RelatorioController` | `@RequestMapping` com `method` explícito, sem prefixo de classe |
| `LegadoController` | rota montada em constante — **não resolve**, e vira `RotaDinamica` |
| `ControllerDeTesteController` | sob `src/test/`: precisa ser **ignorado** |
| `Pedido` | classe sem anotação: está no grafo e não gera endpoint |

Os números do rodapé são parte do golden: `arquivos_no_grafo: 5`,
`arquivos_analisados: 4`, `arquivos_de_teste: 1`. Se o de teste passar a ser
analisado, o denominador do Gate A infla com uma rota que nenhuma suíte de API
alcança — e o sintoma seria "cobertura caiu" num recurso que não mudou.

## O que é mudança legítima

O diff deve **crescer**: um caso novo que o parser passou a reconhecer, com o
arquivo correspondente acrescentado ao fixture.

Endpoint que **some** é regressão até prova em contrário. O denominador encolheu, e
cobertura sobe quando a régua encolhe — que é exatamente o defeito que o Gate A
existe para pegar.

Uma rota saindo de `nao_resolvidas` para `endpoints` é melhoria legítima e deve vir
com a retirada do falso negativo correspondente da `MATRIZ_DE_SUPORTE`. O contrário
— endpoint virando `nao_resolvidas` — é perda de precisão.
