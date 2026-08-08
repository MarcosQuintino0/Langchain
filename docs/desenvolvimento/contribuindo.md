# Contribuindo

## Preparar

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
```

Depois, os hooks de commit:

```bash
pipx install pre-commit && pre-commit install
```

`pre-commit` fica fora do extra `dev` de propósito: é ferramenta de máquina, não
dependência do projeto. Para o site, o extra é outro: `pip install -e ".[docs]"`.

## O ciclo

1. Branch a partir de `main`. O padrão em uso é `melhorias/<assunto>`.
2. Escreva o teste que falha.
3. Escreva o código.
4. Rode [as verificações](como-verificar.md), do mais barato ao mais caro.
5. Commit. A mensagem diz **por quê**, não o quê — o diff já diz o quê.

## O que a CI roda, e o que ela não roda

Roda: `ruff check`, `ruff format --check`, `pyright`, a suíte com cobertura, e
`mkdocs build --strict`. Tudo em `windows-latest` e Python 3.13, porque este projeto
conversa com o sistema de arquivos e com subprocessos o tempo todo — é justamente
onde Windows e Linux divergem.

**Não roda:** o contrato com a skill `qa-api`. Ver
[Como verificar, item 3](como-verificar.md).

## Antes de mexer

Leia o `AGENTS.md`. Ele é curto e é o único documento cujas regras
são **limites**: se uma tarefa exigir contrariar uma das seis invariantes, pare e
exponha o conflito, em vez de contornar em silêncio.

Três coisas que costumam surpreender:

- **a skill `qa-api` é somente leitura.** Nunca modifique, formate, mova ou commite
  nada dela. Este repositório é consumidor.
- **`prompts/` é conteúdo editorial.** Mudança de prompt é tarefa própria, com diff
  próprio — nunca efeito colateral de uma mudança de código.
- **um teste que reprova por organização é para ser resolvido movendo o código**, ou
  alterando a regra *e* o teste na mesma mudança. Nunca só o teste.

## Decisão que atravessa arquivos

Vira [ADR](../adr/index.md). Quatro cabeçalhos, e o regime é: ADR aceita é imutável,
muda-se por outra que a supersede.
