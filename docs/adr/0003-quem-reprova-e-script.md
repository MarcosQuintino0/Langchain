# ADR 0003 — Quem reprova é script; LLM só cria

**Status:** Aceita

## Contexto

O defeito de origem do projeto é o modelo amostrar em vez de enumerar, e isso passar despercebido porque o gabarito de cobertura é escrito pelo mesmo modelo que depois vai satisfazê-lo.

## Decisão

Nenhum LLM decide se a cobertura está completa, e nenhum LLM valida a saída de outro LLM. Contar 12 categorias × N endpoints × M campos é trabalho de script.

## Consequências

A promessa do projeto passa a ser verificável. Em troca, tudo o que se quer garantir precisa de um verificador determinístico — e onde não há verificador, a resposta honesta é não prometer, que é a razão de existir a matriz de suporte.
