# ADR 0008 — Nenhum nome de modelo em código ou prompt

**Status:** Aceita

## Contexto

Nome de modelo em código transforma trocar de provedor num diff de código, e faz um teste depender de um identificador que muda por decisão comercial de terceiro.

## Decisão

Modelo, provedor e parâmetros vêm de configuração. O template do `orquestrador init` deixa `modelo = ""` — string vazia, e não um placeholder, porque um identificador falso seria enviado ao provedor como se fosse real.

## Consequências

O `doctor` consegue distinguir 'não preenchido' de 'escolha deliberada'. Em troca, não há padrão razoável: a primeira execução exige que alguém escolha.
