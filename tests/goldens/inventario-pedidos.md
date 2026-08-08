# `inventario-pedidos.json`

O `artefatos/<recurso>/inventario.json` da tentativa aprovada do roteiro: o que o
mapeador afirma ter encontrado no backend.

## O que ele prova

A forma do lado **esquerdo** do diff do Gate A. Cada endpoint carrega método, rota,
handler, arquivo e linha; `rotas_dinamicas_nao_resolvidas` existe mesmo vazia,
porque é ela que impede incerteza de virar ausência — some do JSON e o
`QAORQ-001` deixa de ter de onde sair.

Diferente do manifesto, este artefato não é lido por nenhum `.mjs`: ele fica no
diretório da execução e é lido por humano e pelo diff. O que ele congela é
legibilidade e completude do registro, não interoperabilidade.

## O que é mudança legítima

Campo novo no `Endpoint` — arquivo e linha já entraram assim, e foram o que tornou
o inventário útil para conferir a mão. Rota ou método diferente para o mesmo
roteiro de fixture, não: aí o extrator ou o roteiro mudou, e um dos dois está
errado.
