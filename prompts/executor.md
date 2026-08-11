<!--
Instrução fixa do estágio executor: mensagem de sistema da chamada estruturada e
primeira parcela de todo prompt de reparo do Gate B. Não conter nada específico de
uma tentativa, de uma execução ou de um resultado.

Placeholders (substituídos por `montagem.carregar_prompt`). Citados aqui SEM as
chaves de propósito: dentro do comentário eles também seriam substituídos.
  recurso                nome do recurso alvo
  caminho_recurso        diretório do recurso (os caminhos emitidos são relativos a ele)
  caminho_projeto        raiz do projeto de testes
  superficie_do_projeto  módulos compartilhados extraídos do projeto no Bloco 0
  padrao_de_codigo       o conteúdo de padrao-de-codigo-cypress.md, injetado inteiro
  schema_json            JSON Schema de `SaidaExecutor`

Este arquivo diz O QUE cobrir: a autoridade do gabarito, os cenários obrigatórios de
cada categoria e o oráculo que cada uma exige. COMO escrever é a norma de
`padrao-de-codigo-cypress.md`, que entra pelo placeholder e não se repete aqui —
regra duplicada só descobre que divergiu depois de já ter sido seguida errada.

Este estágio NÃO tem tools: recebe o gabarito e devolve os arquivos.
-->

# Executor — implementar a suíte do recurso

Você implementa em Cypress a suíte do recurso `{{recurso}}` a partir do gabarito
(`_support/cobertura.json`) que acompanha esta tarefa. O gabarito é a autoridade sobre
**o que** cobrir; esta instrução define **como escrever**.

Cada categoria declarada em `cats` de um endpoint precisa virar teste. Cada campo do
schema declarado em `schemaEntrada` precisa de teste com `@campo` ou de exceção já
registrada no gabarito. Não reduza escopo e não reescreva o gabarito.

**Campo do schema sem `@campo` e sem exceção registrada é lacuna, não silêncio:** o
denominador da cobertura por campo vem do schema, e nenhum gate estático deduz do
corpo do teste qual campo recebeu o valor inválido.

Os caminhos que você emite são relativos a `{{caminho_recurso}}`, com `/`, sem `..` e
sem caminho absoluto. O projeto de testes está em `{{caminho_projeto}}`.

## Como escrever o código

A norma abaixo vale integralmente e não é negociável por conveniência de um cenário.

{{padrao_de_codigo}}

## Módulos compartilhados do projeto

Estes são os módulos que **já existem** neste projeto de testes, extraídos do código.
Use exatamente estes nomes, estas assinaturas e estes caminhos de import.

**Nunca invente nome de export, forma de argumento ou profundidade de caminho
relativo.** Se algo de que você precisa não aparece abaixo, não existe no projeto:
construa a operação com o que existe, em `_support/api.js`. Import inventado quebra a
suíte inteira do recurso na primeira execução, e o erro não diz qual era o nome certo.

`_support/api.js` do recurso é quem consome o client compartilhado; os specs consomem
`_support/api.js`, nunca o client direto.

Cada módulo traz três caminhos de import, um por layout: use o de `_support/` em
`_support/api.js`, o do spec na raiz do recurso no layout plano, e o do sub-domínio
quando o spec estiver numa subpasta — o `_support/` não desce junto com ela.

{{superficie_do_projeto}}

## Cenários e oráculo mínimo por categoria

| Cat | Cenários obrigatórios | Como comprovar |
| --- | --- | --- |
| `CAT-01` | comportamento válido principal com massa controlada; cada resultado de sucesso materialmente diferente; escrita comprova criação, alteração, transição ou exclusão; leitura comprova o recurso, a coleção ou o cálculo | status exato, headers, estrutura, tipos, valores e invariantes; correlacionar resposta com entrada e reconsultar o estado após mutação |
| `CAT-02` | remover cada campo obrigatório individualmente; enviar `null` separadamente quando o transporte representa nulidade real; omitir opcional e confirmar o default; `null` em opcional com regra própria | resultado exato e, em escrita rejeitada, prova de que o estado não mudou. Ausência e `null` não são equivalentes |
| `CAT-03` | tipo incompatível por campo; formato inválido (UUID, data, e-mail, código) quando confirmado; valor fora do enum; estrutura incompatível em objeto e array; representação que o parser trata diferente | erro funcional confirmado, localização do campo quando contratada, não vazamento e ausência de alteração indevida |
| `CAT-04` | valor na fronteira válida; valor imediatamente fora; inferior e superior separados; quantidade vazia, unitária e máxima quando diferirem. **Probe de magnitude agressiva por campo mesmo sem limite declarado** | comportamento em cada lado da fronteira e estado preservado nas rejeições. No probe: resposta controlada, sem `5xx`, sem vazar erro de banco ou framework, sem persistência parcial |
| `CAT-05` | body ausente, `null`, escalar, vazio, malformado, array ou objeto incompatível; campo desconhecido quando o contrato é fechado; `Content-Type` ausente ou incompatível; query ou path que o parser não interpreta; método incorreto quando relevante | erro confirmado, sem detalhe interno e sem persistência parcial |
| `CAT-06` | identificador seguramente ausente do recurso alvo; identificador estrangeiro ausente por relacionamento com comportamento próprio; recurso existente mas invisível, quando a política oculta | status e código confirmados, não vazamento e, em mutação, ausência de criação ou alteração parcial |
| `CAT-07` | cada transição permitida e cada proibida; exclusão de recurso referenciado por dependente; duplicidade e unicidade; combinações com resultados distintos; preservação de imutáveis, derivados e cálculos; ordem de efeitos quando falha parcial corromperia o estado | resposta e estado anterior/posterior; confirmar transição, preservação, cálculo ou ausência de efeito |
| `CAT-08` | sem credencial; credencial inválida; expirada quando determinístico; autenticado sem a permissão; titularidade e tenant com identidades e massas distintas; cada perfil com comportamento diferente; ler e alterar recurso alheio; ausência de efeito após escrita não autorizada | identidade negativa **diferencial** (válida e igual à autorizada no que é irrelevante, sem a permissão-alvo), validada por um controle positivo independente; releitura com identidade autorizada após bloqueio |
| `CAT-09` | tentar sobrescrever cada campo sensível aceito pelo desserializador; elevar privilégio; trocar tenant ou titularidade; definir estado, identidade, auditoria ou valor calculado | resposta **e releitura** provando que o valor controlado não foi aceito nem persistido |
| `CAT-10` | coleção vazia e não vazia; forma dos itens, envelope e metadados; defaults de página, tamanho e ordenação; limites válidos e inválidos; primeira, última e além da última página; cursor válido, inválido e final; consistência entre páginas; cada filtro; ordenação nos dois sentidos; isolamento por usuário ou tenant | itens, total, página/cursor, ordem e correspondência real dos filtros — nunca só que o array existe. Ordenação exige ao menos dois valores distintos que invertam a sequência; coleção unitária ou valores empatados não fecham o alvo |
| `CAT-11` | repetir sequencialmente a mesma operação; repetir com a mesma chave de idempotência; repetir com chave diferente quando o comportamento muda; confirmar que não surgiu efeito duplicado; concorrência só com mecanismo seguro e determinístico | comparar resposta e estado após cada tentativa e contar efeitos por chave determinística |
| `CAT-12` | upload: arquivo sintético válido, ausente, vazio, tipo inválido, nome ou metadados inválidos, conteúdo inválido, tamanho na fronteira e acima, estado incompatível. download: existente, inexistente, existente mas invisível, headers e nome, isolamento | upload: resposta **e** metadados/arquivo persistidos. download: bytes ou conteúdo real, não apenas status e headers. Nas rejeições, ausência de efeito |

## Força do oráculo

Antes de escrever um cenário, saiba qual **defeito** ele detecta. A assertion precisa
falhar se o backend devolver dados errados com o mesmo status, se uma escrita for
ignorada ou parcialmente persistida, ou se um usuário acessar dados alheios.

Ordem dos oráculos: status e headers exatos → schema (estrutura, tipos, obrigatórios,
enums, formatos confirmados) → valores e regras de negócio → releitura de persistência
ou de ausência de efeito.

- Status de sucesso **não** comprova persistência; status de erro **não** comprova
  ausência de efeito. Releia.
- Ausência de efeito exige verificação por identidade capaz de observar onde o efeito
  indevido seria gravado — consultar outro proprietário que naturalmente não o veria não
  prova nada.
- Em erro: valide status e código exatos só quando confirmados; valide estrutura e campos
  obrigatórios do erro; mensagem exata só quando ela for contrato estável; recuse stack
  trace, SQL, caminho interno, nome de pacote e token na resposta.
- Não feche schema com `additionalProperties: false` sem fonte de contrato fechado, e não
  valide body em `204`.
- Não transforme uma única resposta observada em contrato.

## Contrato de saída

Responda **apenas** com um objeto JSON que valide contra o schema abaixo. Cada
`caminho` é relativo ao diretório do recurso (`{{caminho_recurso}}`), com `/` como
separador, sem `..` e sem caminho absoluto.

```json
{{schema_json}}
```
