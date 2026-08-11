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

**O interruptor também saiu.** `[gates.b].exigir_cobertura` sobreviveu ao
desacoplamento por alguns dias com valor `true` e nenhum leitor: a configuração
afirmava que a checagem estava ligada enquanto ela não existia mais. Foi removido
junto com `[caminhos].skill` e as `flags` dos gates. Quando a reconciliação
voltar, o campo volta com ela — e aí ligado vai querer dizer ligado.

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

## Import que não resolve não tem gate

Medido em 2026-08-11, execução `20260811-200736-32500-5ff58905`: o
`criar-customers.cy.js` gerado chamava `validarNaoVazaInterno(resposta)` sem
importar a função. O spec quebraria na primeira execução com
`validarNaoVazaInterno is not defined`, e nada reprovou — nem o `padrao_cypress`,
que não confere import, nem o eslint, que só roda quando o projeto do cliente o
tem configurado.

O `prompts/executor.md` alegava que este caso reprovava, herança da skill; a
alegação saiu, porque prompt que ameaça gate inexistente ensina o modelo a não
levar a sério o que está escrito.

Conferir isto exige resolver o que a **superfície do projeto** oferece contra o
que o spec importa e usa — o extrator já lê os exports compartilhados
(`analise_estatica/extrator_de_superficie.py`), então falta cruzar as duas
listas com os identificadores chamados em cada arquivo.

## Execução interrompida deixa staging no projeto do cliente

`AreaDeStaging.descartar()` só roda no caminho feliz. Recurso reprovado mantém o
staging de propósito — é o que se inspeciona —, mas execução que morre por erro
de provedor ou por corte de token deixa o diretório `.qa-staging-<execucao>-<recurso>`
para trás sem que ninguém o reivindique depois.

Medido em 2026-08-11: oito diretórios órfãos acumulados em
`cypress/e2e/apis/` do projeto de referência, de execuções de dias anteriores. O
ponto inicial do nome os mantém fora do `specPattern` do Cypress, então eles não
quebram nada — só sujam o repositório de quem paga pela ferramenta, e ninguém
sabe quais podem ser apagados sem ler o diário.

A correção provável é uma varredura no início da execução: staging cujo `run_id`
não corresponde a nenhuma execução em curso é lixo de execução morta, e o diário
já sabe dizer que o criamos.

## O que a norma de código deixa para julgamento

`gates/padrao_cypress.py` cobra oito regras. Três da norma ficaram sem fiscal
porque nenhuma delas decide sem interpretar:

- se o import do spec veio de camada permitida (depende da superfície de cada
  projeto, que varia por cliente);
- se a mensagem da asserção **explica** alguma coisa, em vez de repetir o nome do
  campo;
- se o nome do teste descreve comportamento em vez de mecanismo.

Elas valem igual e estão escritas na norma. O que não existe é quem as cobre — e
esta seção existe para que isso não passe por garantido.
