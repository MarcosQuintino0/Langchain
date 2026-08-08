# Glossário

Onze palavras que este projeto usa com sentido próprio. Nenhuma delas é jargão da
indústria com o significado usual.

**Recurso** — a unidade de trabalho: um conjunto de endpoints que se testa junto
(`pedidos`, `clientes`). Um por vez, histórico zerado entre eles (princípio 3). O
nome vira diretório por concatenação, e por isso é tipo e não `str` — ver
`dominio/recurso.py`.

**Bloco** — cada uma das quatro fases da execução. Blocos 0 e 3 são determinísticos
e não gastam token; 1 e 2 chamam modelo. Ver [Visão geral](arquitetura/visao-geral.md).

**Gate** — verificação determinística que **reprova**. Nunca é um LLM. Tem três
vereditos, não dois: `APROVADO`, `REPROVADO` e `ERRO_DA_FERRAMENTA` — script que não
rodou não é aprovação degradada, e também não é violação para o modelo reparar.

**Delta** — o **único** contexto novo que uma tentativa de reparo recebe: as
violações daquela volta, e mais nada. Ver [O loop de reparo](arquitetura/o-loop-de-reparo.md).

**Artefato** — o que trafega entre estágios, sempre em disco (princípio 1):
`inventario.json`, `_support/cobertura.json`, os `.cy.js`, os schemas.

**Manifesto de cobertura** — o `_support/cobertura.json`: o **gabarito**. Declara,
para cada endpoint, quais das 12 categorias se aplicam e por que as outras não. É o
denominador contra o qual a entrega é medida. Não confundir com o próximo.

**Manifesto de execução** — o `manifesto-execucao.json`: ambiente, versões, commits,
hashes e política de privacidade de **uma execução**. Responde ao chamado *"ontem
passou, hoje falhou"*.

**Inventário** — o que o mapeador afirma ter encontrado no backend. É o lado
esquerdo do diff do Gate A; o manifesto é o direito.

**Lacuna** — categoria que o manifesto planejou e que nenhum spec entregou
(`QAORQ-030`). Era o buraco entre os dois scripts da skill: um prova forma, o outro
conta cobertura, e ninguém reprovava por "planejei CAT-07 e não escrevi".

**Staging** — a área de trabalho de um recurso, irmã do diretório de destino. Cada
tentativa do loop reescreve o staging; o projeto do consumidor é tocado **uma vez**,
depois que os dois gates aprovaram.

**Diário de propriedade** — o `diario-de-propriedade.json`, que registra quais
arquivos do projeto do consumidor **nós** criamos e quais modificamos, com hash.
Vive acima da execução porque a pergunta atravessa execuções.

**Impressão da skill** — hash dos `.mjs` de `qa-api` que o orquestrador invoca. A
integração é um contrato implícito (argumentos, código de saída, forma do JSON), e
a impressão é o que faz uma mudança do outro lado virar recusa em vez de defeito
silencioso.

**Superfície do projeto** — o que o projeto de testes do consumidor já oferece:
módulos compartilhados, exports e os caminhos relativos de import. O executor não
tem como adivinhar isso, e o delta do Gate B ("import não resolve") não é acionável.
