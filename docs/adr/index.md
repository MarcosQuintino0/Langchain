# Decisões de arquitetura

Decisão que atravessa arquivos mora aqui. Quatro cabeçalhos: Contexto, Decisão,
Consequências, Status.

## O regime

**ADR aceita é imutável.** Ela não é editada quando a realidade muda: escreve-se
outra, que a supersede. É isso que impede o conjunto de virar wiki — um documento
que se reescreve perde exatamente o que se procura nele, que é o que se pensava na
época.

As catorze primeiras não são prosa nova. São justificativas que já existiam,
enterradas em docstring e em comentário, movidas para um lugar com nome e número
citável.

## As decisões

| # | Decisão |
| --- | --- |
| [0001](0001-artefato-em-disco-entre-estagios.md) | Artefato em disco entre estágios |
| [0002](0002-prompt-de-reparo-e-so-o-delta.md) | O prompt de reparo é exatamente instrução + artefato + delta |
| [0003](0003-quem-reprova-e-script.md) | Quem reprova é script; LLM só cria |
| [0004](0004-tres-estados-de-veredito.md) | Três estados de veredito, não um booleano |
| [0005](0005-raiz-do-pacote-e-lista-fechada.md) | A raiz do pacote é lista fechada |
| [0006](0006-init-nao-reexporta.md) | `__init__.py` não reexporta |
| [0007](0007-escrita-transacional-por-staging.md) | Escrita transacional por staging e diário |
| [0008](0008-nenhum-nome-de-modelo-em-codigo.md) | Nenhum nome de modelo em código ou prompt |
| [0009](0009-ci-so-windows-e-python-313.md) | CI só em Windows e Python 3.13 |
| [0010](0010-path-como-valor-em-dominio.md) | `Path` como valor é permitido em `dominio/` |
| [0011](0011-documentacao-sem-publicacao.md) | Documentação com MkDocs, sem publicação |
| [0012](0012-marker-classifica-por-dependencia.md) | Marker classifica por dependência, não por escopo |
| [0013](0013-sem-fachada-em-movimento-de-modulo.md) | Sem fachada de compatibilidade em movimento de módulo |
| [0014](0014-contrato-da-skill-fora-da-ci.md) | O contrato com a skill não é verificado na CI |

Para escrever a próxima, copie o [template](0000-template.md).
