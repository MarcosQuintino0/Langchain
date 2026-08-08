# ADR 0014 — O contrato com a skill não é verificado na CI

**Status:** Aceita

## Contexto

O job de integração dependia de `vars.SKILL_REPO`, uma variável que nunca foi definida porque a skill `qa-api` mora fora deste repositório. O efeito era pior que não existir: o job aparecia como `skipped` ao lado dos verdes, e 'a CI está passando' passou a significar algo diferente do que parecia.

## Decisão

O job sai do gatilho de `push` e vira `workflow_dispatch`. O contrato com os `.mjs` é verificado **na máquina de quem desenvolve**, com `pytest -m "integration or e2e" -rs`, e isso está escrito no `AGENTS.md` e no cabeçalho do `ci.yml`.

## Consequências

A CI passa a dizer a verdade sobre o que cobre. O buraco continua: a única defesa contra a skill mudar por baixo é a impressão digital dela, que detecta **mudança**, não incompatibilidade.

**Gatilho para rever:** publicar `qa-api` num repositório que o runner alcance. As variáveis que o job espera continuam documentadas no `ci.yml`.
