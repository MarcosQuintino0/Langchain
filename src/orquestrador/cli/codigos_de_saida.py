"""O contrato com quem automatiza: o que cada código de saída significa.

Módulo próprio, e tão pequeno, por necessidade estrutural: os outros três de
`cli/` precisam das constantes, e `doctor` importando de `principal` enquanto
`principal` importa `doctor` para o despacho é ciclo em tempo de importação.

Ele também é o dono de um contrato que só existia como comentário. Um pipeline
que sai com 3 e um que sai com 1 pedem coisas diferentes de quem agenda a
execução, e essa diferença precisa estar num lugar citável."""

from __future__ import annotations

#
# `3` é o terceiro estado do recurso: tudo passou, o artefato foi publicado, e
# alguma coisa precisa de olho humano — hoje, schema do consumidor que não declara
# campo que existe no backend. Não é 0 porque um pipeline verde esconderia a
# revisão pendente, e não é 1 porque nenhum gate reprovou e não há nada para o
# modelo consertar.
SUCESSO = 0
FALHA_DE_GATE = 1
ERRO_DE_USO = 2
REQUER_REVISAO = 3
# Indisponibilidade do provedor de LLM tem código próprio porque a resposta do
# operador é outra: `2` pede para arrumar a configuração ou o ambiente, `4` pede
# para esperar e repetir. Num agendamento, é a diferença entre alertar alguém e
# reenfileirar sozinho.
ERRO_DE_PROVEDOR = 4
