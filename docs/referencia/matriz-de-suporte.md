# Matriz de suporte

Esta seção existe para você decidir, antes de instalar, se o orquestrador serve
para o seu projeto — e para que a resposta seja a mesma dada aqui, no código e na
mensagem de erro. **Fora da matriz, a ferramenta falha com diagnóstico**: nunca
aprova, nunca promete cobertura que não sabe medir.

A tentação óbvia é a oposta — acrescentar uma frase ao prompt dizendo "suporte
também FastAPI" e chamar isso de suporte. É o defeito de origem do projeto com
outra roupa: um LLM sempre devolve *alguma* coisa, e o que falta não é a capacidade
de escrever teste, é o **denominador determinístico** que prova que o teste cobre o
que existe. Suporte, aqui, significa que existe um verificador que sabe reprovar.

### Tier A — avaliado e suportado

| Papel | O que é |
| --- | --- |
| Backend | Java com Spring MVC (`@RestController`, `@RequestMapping`, `@GetMapping` e irmãos) |
| Projeto de testes | Cypress em JavaScript — `.js`, `.mjs`, `.cjs` |

É a combinação que roda contra backend real aqui, e a única em que os quatro
blocos funcionam inteiros: o Bloco 0 indexa o backend,
`src/orquestrador/analise_estatica/rotas_java_spring.py`
lê as rotas do fonte e dá ao Gate A o **denominador** do diff grafo × manifesto, o
Gate B roda o validador da skill e a lacuna de cobertura, e o Bloco 3 executa a
suíte.

Esta seção declara para quais linguagens existe denominador; em que fase está o
gate que o consome é assunto de O que é stub. São perguntas
diferentes e envelhecem em ritmos diferentes — juntá-las numa lista só é como as
duas ficam desatualizadas ao mesmo tempo.

### Precisão medida — Java/Spring

| O quê | Número | Onde |
| --- | --- | --- |
| endpoints extraídos do backend de exemplo | **27 de 27** | 5 classes controladoras |
| endpoints do projeto-fixture do adaptador | **7 de 8** | 1 fica em `nao_resolvidas` |
| rotas que o parser declara não resolver | 4 categorias | `MATRIZ_DE_SUPORTE.falsos_negativos` |

O oitavo endpoint do fixture é uma rota montada em constante (`BASE + "/itens"`). Ela
**não some**: vira `RotaDinamica` com a expressão original e sai como aviso
`QAORQ-001`. Incerteza registrada não é ausência — é essa distinção que impede o
buraco de virar cobertura completa por omissão.

Os dois números têm golden (`tests/goldens/endpoints-java-spring.json`), e o
projeto-fixture existe para que "a precisão caiu?" tenha resposta sem rodar contra
um backend real.

**Todo adaptador precisa dos três**: projeto-fixture com `graph.json`, golden dos
endpoints extraídos, e falsos negativos declarados — lista vazia reprova. Um
adaptador que afirma cobertura total de um framework inteiro está afirmando algo que
nunca é verdade; o que a lista vazia significa é que ninguém procurou os buracos.
Quem cobra é `tests/test_analise_estatica_extrator_de_endpoints.py`.

Os limites que valem **mesmo dentro do Tier A**, porque suporte avaliado não é
suporte perfeito:

* **A superfície do projeto é lida por heurística, não por AST.**
  `analise_estatica/exports_javascript.py` reconhece as formas de `export` que a
  arquitetura-base da skill usa; um módulo escrito de forma exótica (reexport
  dinâmico, `Object.assign(module.exports, …)`) some da superfície, e o executor
  passa a não saber que aquele símbolo existe. O sintoma é import que não resolve
  no Gate B, não silêncio.
* **Rota que o parser não consegue resolver não é adivinhada.** Caminho montado
  por constante, concatenação ou `${propriedade}` vira o aviso `QAORQ-001`, com a
  expressão original — nem endpoint inventado, nem omissão silenciosa.
* **TypeScript no projeto de testes não é lido.** O extrator aceita as três
  extensões acima e só elas.

### Tier B — o Graphify indexa, a descoberta é genérica

Vale para backend em linguagem que o extrator AST do Graphify indexa, mas para a
qual **não existe adaptador de rota** em
`analise_estatica/extrator_de_endpoints.py`. Quais linguagens são essas é
informação do Graphify, e este README de propósito não a repete: lista copiada
envelhece, e o que vale é o que aquela versão fixada realmente indexou.

O que você ganha: o Bloco 0 roda, o grafo existe, e as tools do mapeador
(`query`, `affected`) localizam código sem varrer o backend. O Bloco 2 e o Gate B
funcionam normalmente — eles olham o projeto de testes, não o backend.

O que você **não** ganha, e é declarado: sem adaptador não há denominador, então o
Gate A não consegue provar *"planejei tudo que existe"*. Quando nenhum adaptador lê
um endpoint sequer do backend, a resposta é **erro de ferramenta** — nem aprovado,
nem reprovado —, com a mensagem dizendo quantos arquivos o grafo tinha, quantos
foram analisados e quais extensões ficaram de fora. Aprovar ali seria declarar
cobertura completa sem ter contra o que comparar; reprovar mandaria o mapeador
consertar um artefato correto e queimaria as tentativas sem chance de convergir. É
a mesma escolha que `gates/evidencias.py` faz quando o backend não é legível.

Confiança declarada: **menor**. A cobertura medida continua sendo a do gabarito
contra si mesmo, que é exatamente o que o projeto existe para superar.

### Tier C — experimental ou bloqueado

| Item | Estado | O que acontece |
| --- | --- | --- |
| Projeto de testes que não seja Cypress/JS | bloqueado | o Bloco 0 falha antes de chamar qualquer modelo: sem export lido, não há superfície |
| Backend que o Graphify não indexa | bloqueado | sem `graph.json` não há Bloco 0, e o Bloco 1 recusa rodar |
| Segundo adaptador de linguagem | não existe | a matriz de `extrator_de_endpoints.py` tem uma linha hoje |
| Auditor semântico | stub | `--auditor` encerra com erro em vez de fingir auditoria |
| Execução do Cypress (Bloco 3) | opcional | pulado por padrão; sem `--rodar-cypress` o resumo diz `NAO_EXECUTADO`, e a cobertura relatada é estática |

**Onde a matriz mora no código.** A do denominador é a constante
`MATRIZ_DE_SUPORTE`, em
`src/orquestrador/analise_estatica/extrator_de_endpoints.py`;
a do projeto de testes é `EXTENSOES`, em
`src/orquestrador/analise_estatica/extrator_de_superficie.py`.
Adaptador novo entra lá, com projeto-fixture e teste — não aqui.

---
