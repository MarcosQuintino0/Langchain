# ADR 0006 — `__init__.py` não reexporta

**Status:** Aceita

## Contexto

Um `__init__.py` que reexporta cria uma segunda rota de import para o mesmo símbolo. Com duas rotas, some a resposta para 'quem é o dono disto?' — e um agente importa de onde encontrou primeiro.

## Decisão

Nenhum `__init__.py` do pacote tem import ou `__all__`. Só docstring.

## Consequências

`import orquestrador.gates` deixa de puxar todos os gates para ler um código de violação. Em troca, os imports ficam mais longos e explícitos, o que é o objetivo.
