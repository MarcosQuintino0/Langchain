<!--
PLACEHOLDER DA FASE 1 — o conteúdo desta instrução é a FASE 2.

Instrução fixa do estágio executor: mensagem de sistema da chamada estruturada e
primeira parcela de todo prompt de reparo do Gate B.

O que a Fase 2 escreve aqui: a fatia da skill `qa-api` relativa à implementação —
padrões de código Cypress, arquitetura do recurso (specs-base, `_support/api.js`,
`factories.js`, `helpers.js`, `asserts.js`), as tags obrigatórias `@endpoint`,
`@cat`, `@campo` e `@alvo`, e as invariantes de oráculo. Fontes: `SKILL.md`
passos 8–10, `references/padroes-de-codigo-cypress.md`,
`references/organizar-codigo.md`.

Placeholders disponíveis:
  {{recurso}}          nome do recurso alvo
  {{caminho_recurso}}  diretório do recurso (os caminhos emitidos são relativos a ele)
  {{caminho_projeto}}  raiz do projeto de testes
  {{schema_json}}      JSON Schema de `SaidaExecutor`

Este estágio NÃO tem tools: recebe o gabarito do recurso e devolve os arquivos.
-->

# Executor — instrução do estágio

<!-- FASE 2: padrões de código Cypress, arquitetura do recurso e tags. -->

## Contrato de saída

Responda **apenas** com um objeto JSON que valide contra o schema abaixo. Cada
`caminho` é relativo ao diretório do recurso (`{{caminho_recurso}}`), com `/` como
separador, sem `..` e sem caminho absoluto.

```json
{{schema_json}}
```
