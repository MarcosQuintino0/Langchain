# Como verificar

Do mais barato ao mais caro.

## 1. Lint, formatação e tipos — segundos

```bash
ruff check . && ruff format --check . && pyright
```

## 2. A suíte — meio minuto

```bash
python -m pytest
```

Quase tudo é `unit` e roda com o venv e nada mais. Para rodar só o rápido:

```bash
python -m pytest -m unit
```

## 3. O contrato com a skill — só nesta máquina

```bash
python -m pytest -m "integration or e2e" -rs
```

**A CI não roda isto.** A skill `qa-api` é outro projeto e não há cópia que o runner
alcance; o job ficou em `workflow_dispatch`, com o motivo escrito no `ci.yml` e a
decisão registrada no [ADR 0014](../adr/0014-contrato-da-skill-fora-da-ci.md).

Se esses testes **pularem**, você não os rodou. O `-rs` diz o motivo — normalmente
Node ausente ou `[caminhos].skill` apontando para lugar nenhum.

Enquanto o job estiver desligado, a única defesa contra a skill mudar por baixo é a
impressão digital dela, conferida a cada execução. Ela detecta **mudança**, não
incompatibilidade.

## 4. O pipeline inteiro, sem gastar token — segundos

```bash
python -m orquestrador --dry-run --recurso pedidos
```

Substitui **apenas a resposta do modelo**. Tools, scripts `.mjs`, gates e deltas são
reais, e a escrita vai para uma sandbox dentro do diretório da execução.

O que ele prova: que o fluxo, os dois gates e o loop de reparo funcionam ponta a
ponta. O que ele **não** prova: compatibilidade com provedor real.

## 5. O ambiente de uma execução real

```bash
orquestrador doctor
```

Catorze diagnósticos, cada um com o conserto ao lado. Sai com código 2 se algo
estiver faltando.

## 6. A cobertura — como a CI mede

```bash
python -m pytest --cov --cov-report=term-missing
```

Piso de 85%, com cobertura de **ramo**. Fora do `addopts` de propósito: rodar a
suíte em fatias não pode reprovar por cobertura global.

## 7. O site de documentação

```bash
mkdocs build --strict
```

O `--strict` transforma link quebrado e página fora da navegação em erro. Para ler:
`mkdocs serve`. Precisa do extra: `pip install -e ".[docs]"`.

## Antes de abrir um commit

```bash
ruff check . && ruff format --check . && pyright && python -m pytest
```

E, se você tocou em algo que o pipeline escreve, o dry-run do item 4.
