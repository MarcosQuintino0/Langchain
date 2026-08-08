# Goldens

Cada `.json` aqui é uma saída congelada. Cada um tem um `.md` ao lado dizendo o que
ele prova e o que conta como mudança legítima.

## O regime

**Não existe `--update-goldens`.** Quando um golden diverge, o teste imprime o diff
e o comando que **mostra** o conteúdo novo no stdout — nunca o que o grava. A
ausência do atalho é deliberada: `--update` transforma "por que isto mudou?" num
reflexo de teclado.

Atualizar um golden é sempre três passos, na ordem:

1. entenda por que ele mudou;
2. cole o conteúdo novo;
3. escreva no `.md` ao lado o que mudou e por quê, **no mesmo commit**.

## O que um golden pega, e o que ele não pega

Ele pega mudança **não intencional** — o formato de saída mexeu e ninguém percebeu.

Ele **não** pega mudança errada, porque quem regenera o golden regenera junto o
erro. Por isso todo golden aqui tem, ao lado, pelo menos uma **asserção estrutural
independente** sobre o mesmo dado: uma que verifica a propriedade em vez do texto.
`test_invariante_goldens.py` sempre escreve a asserção estrutural **primeiro**; se
ela for difícil de escrever para um artefato, esse artefato não merece golden —
merece um teste.

A honestidade sobre o limite: nada aqui impede um agente de reescrever o `.json`
para fazer a suíte passar. O que existe é (a) nenhum mecanismo que torne isso um
reflexo, (b) o `.md` ao lado, que faz o padrão *"o `.json` mudou e o `.md` não"*
aparecer no `git diff`, e (c) a asserção estrutural, que quem regenera o golden não
cala.

## Os quatro

| Golden | O que congela |
| --- | --- |
| `schemas-do-dominio.json` | nome, tipo, alias e obrigatoriedade de cada campo dos contratos públicos |
| `superficie-do-projeto.json` | o que o extrator enxerga em `fixtures/projeto-testes/` |
| `cobertura-pedidos.json` | o **contrato de fio** com `validar-suite-gerada.mjs` |
| `inventario-pedidos.json` | a forma do inventário no diretório da execução |

`manifesto-execucao.json` **não** vira golden: timestamps, hashes de git e caminhos
absolutos mudam a cada execução por construção, e normalizá-los apagaria justamente
o que o manifesto é.
