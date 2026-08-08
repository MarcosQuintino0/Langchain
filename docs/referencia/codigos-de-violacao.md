# Códigos de violação

Duas famílias, com donos diferentes.

## `QAAPI-0xx` — da skill

Vêm dos scripts `.mjs` de `qa-api`, que é **outro projeto**. A lista canônica está
lá, e este site não a copia de propósito: uma segunda fonte de verdade sobre um
contrato que não é nosso envelheceria sem sinal.

Os que aparecem com mais frequência no delta:

| Código | O que ele cobra |
| --- | --- |
| `QAAPI-002` | spec-base ausente para um endpoint declarado |
| `QAAPI-021` | as 12 categorias não estão contabilizadas (`cats` ∪ `naoAplica`) |
| `QAAPI-025` | cobertura por campo abaixo do que o schema de entrada exige |
| `QAAPI-027` | `schemaEntrada` declarado sem o arquivo correspondente |

## `QAORQ-0xx` — do orquestrador

Prefixo próprio para nunca colidir com os da skill. A tabela é **gerada** a partir
de `src/orquestrador/gates/codigos.py` — não edite aqui;
edite o catálogo e regenere.

<!-- INICIO DOS CODIGOS QAORQ: gerado por gates/codigos.py -->
| Código | O que significa |
| --- | --- |
| `QAORQ-001` | aviso: trecho do backend que o diff grafo × manifesto não conseguiu resolver |
| `QAORQ-002` | endpoint presente no backend e ausente do manifesto |
| `QAORQ-003` | endpoint do manifesto sem correspondente no backend |
| `QAORQ-010` | saída do modelo não valida contra o contrato Pydantic do estágio |
| `QAORQ-011` | o modelo não devolveu JSON no formato pedido |
| `QAORQ-020` | prettier reprovou a formatação |
| `QAORQ-021` | eslint reprovou o código |
| `QAORQ-022` | formatador configurado mas ausente do PATH |
| `QAORQ-030` | categoria declarada em cats sem nenhum `it` que a cubra |
| `QAORQ-040` | schema preservado do consumidor não declara campo que o mapeador achou |
<!-- FIM DOS CODIGOS QAORQ -->

`QAORQ-001` é **aviso, não reprovação**, e isso é decisão e não omissão: rota
dinâmica que o extrator não resolveu é registro de incerteza, não ausência. Não há
endpoint a cobrar, e o mapeador não teria o que consertar — reprovar ali gastaria
as tentativas do loop num delta não acionável. Quem reprova de verdade é o
`QAORQ-002`, que é onde estava o defeito de origem do projeto.
