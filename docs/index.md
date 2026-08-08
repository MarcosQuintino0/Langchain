# Orquestrador `qa-api`

Orquestrador em Python que coordena três estágios — dois com LLM, um determinístico
— para gerar suítes de teste Cypress de API, dirigido pela skill externa `qa-api`.

A promessa é **cobertura provada por verificador determinístico**, nunca cobertura
afirmada por um LLM. Quase toda decisão registrada aqui existe para proteger isso.

## Por onde começar

| Se você quer… | Leia |
| --- | --- |
| entender por que o desenho é este | [Visão geral](arquitetura/visao-geral.md) e [Os seis princípios](arquitetura/os-seis-principios.md) |
| saber se serve para o seu projeto | [Matriz de suporte](referencia/matriz-de-suporte.md) |
| instalar e rodar | o `README.md` do repositório |
| saber o que uma palavra significa aqui | [Glossário](glossario.md) |
| mexer no código | [Convenções](desenvolvimento/convencoes.md) e [Ferramental](desenvolvimento/ferramental.md) |
| entender uma decisão | [Decisões (ADR)](adr/index.md) |

## O que este site não é

Não é o `README.md`, que ensina **o que o projeto é e como rodá-lo**, nem o
`AGENTS.md`, que diz **o que não pode ser quebrado**. Aqui fica o que não cabe em
nenhum dos dois: a referência completa, o raciocínio por trás das escolhas e os
diagramas.

Os três não se repetem de propósito. Regra duplicada só descobre que divergiu
depois que já foi seguida errada.

## Nada aqui é publicado

O site é construído na CI com `mkdocs build --strict` — que reprova link quebrado
e página fora da navegação — e o resultado é descartado. Para ler localmente:

```bash
pip install -e ".[docs]" && mkdocs serve
```

O gatilho para publicar está no [ADR 0011](adr/0011-documentacao-sem-publicacao.md).
