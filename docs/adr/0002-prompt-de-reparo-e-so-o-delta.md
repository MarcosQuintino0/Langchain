# ADR 0002 — O prompt de reparo é exatamente instrução + artefato + delta

**Status:** Aceita

## Contexto

Tentativas anteriores de dividir o trabalho reenviavam o contexto inteiro a cada etapa e a cada correção. O custo por recurso crescia com o quadrado do número de tentativas.

## Decisão

O prompt de uma tentativa de reparo é exatamente `instrucao_fixa_do_estagio + artefato_atual + delta.violacoes`. Nada de mensagem anterior, resumo de tentativa, raciocínio ou contexto extra.

## Consequências

O custo passa a ser linear no número de tentativas, e isso é **verificável a partir do log**: `caracteres_instrucao` é a linha de base constante e `caracteres_entrada` de um reparo tem de ser menor que o da primeira tentativa. Em troca, o modelo não vê o que já tentou — o que é aceitável porque o delta diz o que está errado agora, e é isso que ele precisa.
