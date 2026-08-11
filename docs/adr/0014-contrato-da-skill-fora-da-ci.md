# ADR 0014 — O contrato com a skill não é verificado na CI

**Status:** Superada

> **O que mudou — desacoplamento de 2026-08-10.** Não há mais contrato com a skill
> para verificar. O orquestrador
> deixou de invocar os `.mjs`, o `graphify` virou dependência declarada do pacote e
> `[caminhos].skill` saiu da configuração. Com isso, o único pré-requisito que
> sobrou para `integration` e `e2e` é o `uv`, que o runner instala com pip — então
> o workflow à mão foi apagado e os dois markers voltaram para o `ci.yml`, num job
> que roda a cada push e reprova se qualquer caso pular.
>
> O registro abaixo fica como estava, porque o **gatilho para rever** que ele
> nomeia foi de fato o que aconteceu, ainda que pelo caminho oposto ao previsto:
> em vez de publicar a skill num repositório alcançável, o projeto parou de
> depender dela.
>
> A perda que o desacoplamento trouxe está em
> [`docs/arquitetura/pendencias.md`](../arquitetura/pendencias.md).

**Status original:** Aceita

## Contexto

O job de integração dependia de `vars.SKILL_REPO`, uma variável que nunca foi definida porque a skill `qa-api` mora fora deste repositório. O efeito era pior que não existir: o job aparecia como `skipped` ao lado dos verdes, e 'a CI está passando' passou a significar algo diferente do que parecia.

## Decisão

O job sai do `ci.yml` e vira um workflow próprio, `contrato-da-skill.yml` em
`.github/workflows/` (apagado em 2026-08-10 — ver o topo), disparado só por
`workflow_dispatch`.

Workflow separado, e não um job com `if:` dentro do `ci.yml`, por um motivo
visível: um job com condição falsa **ainda aparece** na lista da execução, como
`skipped` ao lado dos verdes — que é exatamente o defeito sendo corrigido. Num
arquivo próprio, ele não aparece a menos que alguém o dispare.

O contrato com os `.mjs` é verificado **na máquina de quem desenvolve**, com
`pytest -m "integration or e2e" -rs`, e isso está escrito no `AGENTS.md` e no
cabeçalho do workflow.

## Consequências

A CI passa a dizer a verdade sobre o que cobre. O buraco continua: a única defesa contra a skill mudar por baixo é a impressão digital dela, que detecta **mudança**, não incompatibilidade.

**Gatilho para rever:** publicar `qa-api` num repositório que o runner alcance. As variáveis que o job espera continuam documentadas no `ci.yml`.
