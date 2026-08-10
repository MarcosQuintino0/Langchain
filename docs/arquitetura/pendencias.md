# Pendências

O que este projeto sabe que ainda não faz. Um roteiro nomeia, por construção, o
que ainda não existe — por isso este arquivo fica fora da verificação de caminhos
de `tests/test_invariante_documentacao.py`, como o plano de execução.

## O desacoplamento da skill `qa-api` (2026-08-10)

O orquestrador deixou de invocar scripts de um repositório externo. O que era
`C:\Agents\skills\qa-api` — três `.mjs`, uma skill irmã do Graphify, Node 24 e uma
impressão digital que travava a execução quando qualquer um dos vinte scripts
mudava — saiu do caminho crítico. Um cliente instala com `pip install` e roda.

**O que se perdeu, e é preciso reconstruir.** A troca não foi neutra: com os
`.mjs` foi embora a reconciliação de cobertura, que era a peça mais valiosa deles.

| O que sumiu | O que ela respondia |
| --- | --- |
| Contabilidade das 12 categorias (Gate A) | `cats` ∪ `naoAplica` cobre CAT-01..12 sem interseção? |
| Cobertura por campo `@campo` (Gate B) | todo campo do schema tem teste que o exercita? |
| Lacuna `QAORQ-030` (Gate B) | categoria declarada em `cats` virou `it` de verdade? |
| Padrões de código Cypress | cabeçalho de spec, export sem consumidor, nomenclatura |

As três primeiras são **prova de cobertura** e a última é **estilo**. A decisão de
2026-08-10 foi descartar o estilo de propósito — o dono do produto quer desenhar o
próprio padrão — e reconstruir a prova de cobertura em Python.

**O que continua provado, sem a skill.** Nem tudo dependia dela:

- `QAORQ-002/003` — diff grafo × manifesto: nenhum endpoint do backend ficou fora
  do gabarito. É a única checagem cujo denominador não passa por LLM nenhum.
- `QAORQ-050/051/052` — o plano cobre toda categoria declarada, cenário de escrita
  prova estado, e variação repetida do mesmo campo é podada.
- `QAORQ-060..063` — a evidência do dossiê aponta arquivo e linha que existem.
- `QAORQ-031/032/033` — a receita de limpeza virou código que confere o resultado.

**O caminho de volta.** A reconciliação por categoria e por campo é conhecida: as
tags `@endpoint`/`@cat`/`@campo` já são parseadas por
`analise_estatica/tags_cypress.py`, hoje sem autoridade — ele serve só para nomear
o que faltou num delta. Dar autoridade a ele, cruzando as tags dos specs gerados
com `cats` do manifesto e com as `properties` dos schemas, devolve a invariante 4
sem Node e sem repositório de terceiro.

## O produto não prepara o projeto Cypress do cliente

O orquestrador escreve testes **dentro** de um projeto existente: os specs gerados
importam de `cypress/support/api/` (cliente HTTP, rotas, autenticação, asserts
base), e sem esses módulos com exports reais ele falha antes da primeira chamada
de modelo (`analise_estatica/extrator_de_superficie.py` → `ProjetoNaoPreparado`).

Para vender, ou o cliente já tem o projeto pronto, ou o produto precisa montar o
esqueleto — algo como `orquestrador init --projeto`. A checagem **não** deve ser
afrouxada: gerar teste que importa de módulo inexistente produz suíte que não roda,
e o erro apareceria só no Cypress do cliente.

## Backend fora de Java/Spring gera, mas não é verificado

A matriz de suporte de `analise_estatica/extrator_de_endpoints.py` tem um adaptador
só. Em backend de outra linguagem a IA explora normalmente pelas tools e escreve os
testes, mas o `QAORQ-002/003` não tem denominador — ninguém confere se algum
endpoint ficou de fora. Desde 2026-08-10 isso é **aviso destacado**, não
interrupção: backend fora da matriz não pode impedir a geração, mas a ausência da
prova precisa ficar visível no log e no manifesto da execução.

Acrescentar adaptador é trabalho localizado: a matriz fica declarada num lugar só.

## O reparo do executor pede a suíte inteira numa resposta

Medido em 2026-08-10: para corrigir três cabeçalhos e remover quatro exports
mortos, `agentes/executor.py::_reparar` mandou reescrever os seis arquivos
implicados — 225 KB, ~56 mil tokens — numa chamada só, e o modelo via apenas 16%
desse conteúdo, porque `recortar_por_violacoes` corta o artefato em 60 KB. Resultado
medido: 120.000 tokens de saída e 24 minutos até o provedor cortar.

A geração já resolveu esse problema fatiando (um arquivo por chamada, 20-24 mil
tokens cada, todas bem-sucedidas). O reparo é a única parte que continua
monolítica — ele desfaz o fatiamento que faz a geração funcionar. A correção é
fatiá-lo igual: uma chamada por arquivo implicado, cada uma vendo o arquivo
inteiro.
