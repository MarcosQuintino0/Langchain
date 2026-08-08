# Orquestrador multi-agente da skill `qa-api`

Orquestrador em Python que coordena três estágios (dois com LLM, um determinístico)
para gerar suítes de teste Cypress de API, dirigido pela skill `qa-api`.

Este projeto é **independente** do repositório da skill. Ele apenas **consome** a
skill: invoca os scripts `.mjs` dela por subprocess e nunca modifica nada dentro
dela. O caminho é configuração — veja `[caminhos].skill` em
[`config.toml`](config.toml).

A integração é um contrato **implícito**: formato dos argumentos, código de saída,
forma do JSON e semântica dos códigos `QAAPI-`. E os prompts em `prompts/` são
condensação **manual** das `references/` dela. Por isso
`[skill].impressao_esperada` fixa o hash dos `.mjs` invocados — se a skill mudar,
o pipeline recusa rodar até alguém conferir o contrato.

> **O que ainda é stub:** o auditor semântico. Veja
> [O que é stub](#o-que-é-stub). O resto do fluxo roda ponta a ponta, com escrita
> transacional e verificação determinística em cada gate — inclusive o diff
> grafo × manifesto, que só existe hoje para backend Java/Spring (veja
> [Matriz de suporte](#matriz-de-suporte)).

---

## Por que esta arquitetura existe

A skill `qa-api` executada por um único modelo, numa sessão só, apresenta dois
defeitos medidos:

**Completude.** O modelo não implementa todos os testes que a própria skill exige.
A causa não é falta de capacidade: é horizonte longo somado a instrução densa. O
modelo amostra em vez de enumerar, e isso passa despercebido porque o gabarito de
cobertura é escrito pelo mesmo modelo que depois vai satisfazê-lo.

**Custo quadrático de token.** Tentativas anteriores de dividir o trabalho
reenviavam o contexto inteiro a cada etapa e a cada correção.

O desenho ataca os dois: **decomposição por estágio** (cada agente recebe só a
fatia da skill do seu estágio) e **verificação determinística** (contar 12
categorias × N endpoints × M campos é trabalho de script, não de LLM) contra o
primeiro; **handoff por artefato em disco** e **loops de reparo que enviam só o
delta** contra o segundo.

É o padrão *Assured LLM-based Software Engineering*: o LLM gera, filtros
determinísticos descartam o que não presta. O LLM nunca é o verificador primário.

### Os seis princípios

1. **O que trafega entre estágios é artefato em disco, nunca histórico de
   conversa.** Os arquivos (`graph.json`, `cobertura.json`, `inventario.json`, os
   `.cy.js`, `report.json`) são a única memória compartilhada. Qualquer agente
   pode morrer e ser reinstanciado do zero sem perda.
2. **Loop de reparo envia apenas o delta:** `instrucao_fixa_do_estagio +
   artefato_atual + delta.violacoes`. Nunca o histórico das tentativas. Vive em
   [`llm/montagem.py`](src/orquestrador/llm/montagem.py), num lugar só, para não
   escapar por descuido.
3. **Agentes são stateless entre unidades de trabalho.** Um recurso por vez,
   histórico zerado entre recursos.
4. **Quem reprova é script; LLM só cria.** Nenhum LLM decide se a cobertura está
   completa.
5. **O auditor semântico fica fora do loop quente.** Caro, com falso positivo
   alto, sob demanda, com humano triando.
6. **Modelo configurável por estágio.** Nenhum nome de modelo no código.

---

## Os quatro blocos

```
BLOCO 0  qa-reindex.mjs (Graphify, AST)          determinístico, zero token
         → .agents/state/qa-api/graphify-out/graph.json
BLOCO 1  MAPEADOR (LLM + tools, ReAct)           um recurso por vez
         → inventario.json + _support/cobertura.json
         GATE A: validar-suite-gerada --so-manifesto  +  diff grafo × manifesto
         reprova → delta → volta ao mapeador
BLOCO 2  EXECUTOR (LLM, sem tools)               um recurso por vez
         → specs *.cy.js com tags @endpoint @cat @campo
         GATE B: prettier + eslint + validar-suite-gerada + lacuna (QAORQ-030)
         reprova → delta (QAAPI-0xx) → volta ao executor
BLOCO 3  Cypress + qa-cobertura.mjs --json       determinístico
         AUDITOR SEMÂNTICO [STUB] — sob demanda, fora do loop
```

**Por que o Gate A tem duas checagens.** O validador da skill enxerga apenas o
projeto de testes, nunca o backend — limite deliberado, documentado em
`skills/qa-api/scripts/cobertura/handlers.mjs:15`. Ele prova *"entreguei o que
planejei"*, nunca *"planejei tudo que existe"*. O diff grafo × manifesto é o que
fecharia esse elo, e continua **stub** — é o item que ainda não fecha o defeito
de completude que originou o projeto.

---

## Matriz de suporte

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
[`analise_estatica/rotas_java_spring.py`](src/orquestrador/analise_estatica/rotas_java_spring.py)
lê as rotas do fonte e dá ao Gate A o **denominador** do diff grafo × manifesto, o
Gate B roda o validador da skill e a lacuna de cobertura, e o Bloco 3 executa a
suíte.

Esta seção declara para quais linguagens existe denominador; em que fase está o
gate que o consome é assunto de [O que é stub](#o-que-é-stub). São perguntas
diferentes e envelhecem em ritmos diferentes — juntá-las numa lista só é como as
duas ficam desatualizadas ao mesmo tempo.

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
a mesma escolha que `gates/lacunas.py` faz quando o contador de cobertura não vem.

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
[`analise_estatica/extrator_de_endpoints.py`](src/orquestrador/analise_estatica/extrator_de_endpoints.py);
a do projeto de testes é `EXTENSOES`, em
[`analise_estatica/extrator_de_superficie.py`](src/orquestrador/analise_estatica/extrator_de_superficie.py).
Adaptador novo entra lá, com projeto-fixture e teste — não aqui.

---

## Instalação

Requer Python 3.13, Node 24+ e Git já instalados. Sem Docker, sem container.

### A partir do wheel (uso)

```bash
pip install orquestrador-0.1.0-py3-none-any.whl
orquestrador init      # escreve o config.toml do projeto, comentado
orquestrador doctor    # diagnostica o ambiente e diz o que falta
```

`init` escreve um `config.toml` no diretório atual (ou no de `--em`) com os campos
marcados `PREENCHA` e os dois `[estagios.*].modelo` vazios — os únicos que não têm
padrão possível. Ele **recusa sobrescrever** um arquivo existente; `--forcar` cede.

`doctor` verifica, com veredito e instrução de conserto em cada item: versão do
Python, de onde o pacote está rodando, os prompts empacotados, o `config.toml`, o
modelo de cada estágio, a skill `qa-api` e sua impressão, o Node (24+), o Graphify
contra a versão fixada no `manifest.json` da skill, o projeto de testes e seus
módulos compartilhados, o backend e a presença da chave do provedor — **a presença,
nunca o valor**. Sai com código 2 se algum item reprovar, para servir de porta de
CI.

O `--dry-run` **não** existe na instalação pelo wheel: ele depende de `fixtures/`,
que é material de desenvolvimento deste repositório e não vai no pacote. A recusa é
explícita e manda usar o `doctor`.

### A partir do checkout (desenvolvimento)

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

O projeto é instalável (`src/orquestrador`), então `orquestrador` fica importável
de qualquer diretório de trabalho — é o que permite rodar `pytest` de onde for.
Sem o extra `[dev]` você fica sem `pytest`, `ruff` e `pyright`. Dependências e configuração de teste
vivem num arquivo só: [`pyproject.toml`](pyproject.toml).

Confira também `[caminhos].skill` no `config.toml`: ele aponta para o repositório
da skill `qa-api`, que é outro projeto. Sem ele, os gates não têm o que invocar.

`orquestrador doctor` também vale no checkout, e é a forma mais rápida de conferir
tudo isso de uma vez.

### Como construir e testar o wheel

```bash
uv build --wheel
```

Toda release constrói o wheel e o **testa num ambiente vazio** — é o que
`tests/test_invariante_empacotamento.py::test_wheel_limpo` faz: constrói, cria um venv novo,
instala só o wheel, e roda `orquestrador --help`, `doctor`, `init`, `doctor` de
novo e um `--dry-run`. O teste é marcado `integration` e pula sozinho sem o `uv`.

Ele existe porque nenhum outro teste da suíte podia pegar o defeito que motivou
isto: todos rodam de dentro do checkout, onde `prompts/`, `fixtures/` e
`config.toml` estão a um `parents[2]` de distância. Instalado, não estão — e o
wheel instalava com sucesso para falhar no primeiro comando.

### Pré-condição: o projeto de testes já preparado

O orquestrador **gera testes**; ele não prepara o projeto. Antes da primeira
execução real, o projeto de testes precisa ter os módulos compartilhados no lugar —
por padrão em `cypress/support/api/` (`[caminhos].support_compartilhado`): o client
HTTP com o `cy.request` centralizado, as rotas e os asserts base.

O Bloco 0 lê esses módulos e extrai a **superfície do projeto**: nome de cada
export, a declaração verbatim e os caminhos de import já calculados. Essa superfície
entra na instrução do executor — é o que impede que ele invente `apiRequest`,
`RotasApi` ou a profundidade de um `../../../..`.

Se o diretório não existir ou não tiver export algum, o pipeline **falha antes de
chamar qualquer modelo**, com a mensagem dizendo o que fazer. Ele não substitui pela
arquitetura-base da skill: isso produziria imports que não existem no seu projeto, e
o loop de reparo não converge sobre nome de símbolo que o executor nunca teve — o
delta do gate diz "import não resolve", não diz qual era o nome certo.

Preparar o projeto é outro fluxo da skill: `references/preparar-projeto.md`, ou a
arquitetura-base executável em `assets/cypress-api-base/`.

Para rodar de verdade (não é preciso para o `--dry-run`):

```bash
copy .env.exemplo .env
```

e preencha `OPENROUTER_API_KEY`. A chave é lida do ambiente; nunca fica no
`config.toml` nem no código.

---

## Configuração

Tudo em [`config.toml`](config.toml). Caminhos relativos são resolvidos contra o
diretório do próprio arquivo.

| Bloco | O que define |
| --- | --- |
| `[caminhos]` | skill, backend, projeto de testes, `graph.json`, `prompts/`, onde vão os logs |
| `[openrouter]` | `base_url`, nome da variável de ambiente da chave, timeout |
| `[estagios.*]` | **modelo por estágio**, temperatura, modo de saída estruturada, tentativas de schema |
| `[gates.a]` / `[gates.b]` | flags do validador, `max_tentativas` e `exigir_cobertura` |
| `[skill]` | `impressao_esperada`: hash dos `.mjs` invocados; vazio desliga a trava |
| `[execucao]` | executáveis (node, graphify, prettier, eslint, cypress) e limites |

Antes do primeiro uso real, ajuste `backend`, `projeto_testes` e os três
`estagios.*.modelo`. O `config.toml` versionado aponta para o backend e o projeto
de exemplo desta máquina — troque pelos seus, ou comece do zero com `orquestrador
init`, que escreve um arquivo comentado com os campos por preencher e **não** vai
no pacote instalável, porque configuração é do usuário.

**Escolha de modelo por estágio.** O mapeador tem o julgamento mais difícil
(inferir regra implícita, decidir honestamente o que é `naoAplica`) e o menor
volume de saída — pede o modelo mais capaz. O executor tem o volume de tokens e
trabalho mecânico se o gabarito for bom, com o Gate B pegando os erros de forma
determinística — aceita um modelo mais barato.

---

## Rodar em `--dry-run` (sem gastar token)

```bash
python -m orquestrador --dry-run --recurso pedidos
```

O dry-run substitui **apenas a resposta do modelo**, que passa a vir de fixture.
Todo o resto é real: o loop ReAct do LangGraph roda, as tools são chamadas, os
scripts `.mjs` da skill são invocados sobre arquivos escritos em disco, os deltas
são montados e reenviados. Ele trabalha numa sandbox por execução
(`.execucoes/<timestamp>/sandbox/`), então as fixtures ficam limpas.

As fixtures exercitam o caminho feliz **e** um ciclo reprova → delta → reparo →
aprova em cada gate:

| Estágio | Tentativa 1 | Gate | Tentativa 2 |
| --- | --- | --- | --- |
| mapeador | `CAT-05` não contabilizada em `POST /pedidos` | Gate A reprova com `QAAPI-021` | manifesto completo → aprova |
| executor | falta `seguranca.cy.js` e o teste `@campo situacao` de `CAT-03` | Gate B reprova com `QAAPI-002` e `QAAPI-025` | suíte completa → aprova |

Saída esperada ao final: as três tabelas de telemetria (marcadas **SIMULADO**) e
o resumo por recurso.

### Escrever novas fixtures

Um arquivo por (recurso, estágio, tentativa) em
`fixtures/roteiros/<recurso>/<estagio>/tentativa-NN.json`:

```json
{
  "descricao": "para que serve esta tentativa",
  "passos": [
    {"tipo": "tool", "nome": "listar_diretorio", "argumentos": {"caminho": "src"}},
    {"tipo": "final", "artefato": {"inventario": {}, "manifesto": {}}}
  ]
}
```

`final` aceita `artefato` (objeto, serializado em JSON) ou `conteudo` (texto cru —
útil para exercitar o mini-loop de reparo de schema com resposta malformada).
Tentativa sem roteiro próprio repete a última, então um `max_tentativas` maior que
o roteiro falha por esgotamento do gate, e não por arquivo faltando.

---

## Rodar de verdade

```bash
python -m orquestrador --recurso pedidos
```

| Flag | Efeito |
| --- | --- |
| `--config <arquivo>` | usa outra configuração |
| `--recurso <nome>` | repetível; um recurso por vez, na ordem dada |
| `--dry-run` | não chama modelo nenhum |
| `--max-tentativas <n>` | sobrescreve o limite de todos os gates |
| `--rodar-cypress` | executa o Cypress no Bloco 3 (por padrão é pulado) |
| `--auditor` | **indisponível** — recusa com código 2 |
| `--remover-reprovados` | apaga o que criamos nos recursos não aprovados |

Códigos de saída: `0` sucesso, `1` algum gate esgotou as tentativas, `2` erro de
configuração ou indisponibilidade de ferramenta, `3` algum recurso encerrou em
`REQUER_REVISAO`. O pior desfecho manda: reprovado > requer revisão > aprovado.

**Por que `--auditor` recusa em vez de sumir.** Continua reconhecida pelo argparse
para não quebrar script existente em silêncio, mas encerra com código 2 e uma
mensagem dizendo o que falta. Antes ela imprimia o veredito do stub e saía com
**0**; em CI isso é indistinguível de auditoria feita — e o auditor não existe.

**Por que `--remover-reprovados` voltou.** Ela apagava todo caminho da lista de
reprovados sem distinguir arquivo que criamos de arquivo que já era do cliente, e
por isso ficou desligada até existir o diário de propriedade. Com o diário, a
remoção é restrita à classificação `criado` **e** ao hash inalterado desde que o
gravamos: um spec que nasceu conosco e o desenvolvedor editou à mão deixa de ser
descartável no instante em que ele o salva.

**O terceiro estado do recurso.** `REQUER_REVISAO` é o desfecho de quem passou nos
gates mas encontrou, no backend, campo que o schema preexistente do consumidor não
declara. O schema **não** é atualizado — o contrato dele tem precedência, e mexer
nele para fazer teste passar seria trocar a régua independente pela régua de quem é
medido. O diff sai legível por máquina em
`.execucoes/<ts>/artefatos/<recurso>/divergencias-de-schema.json`.

### Executar o Cypress

O comando em `[execucao].cypress` **precisa** conter a marca `{relatorio}` no
argumento que diz ao repórter onde escrever o JSON:

```toml
cypress = ["npx", "--no-install", "cypress", "run",
           "--reporter", "json", "--reporter-options", "output={relatorio}"]
```

O orquestrador a substitui pelo caminho do relatório **desta** execução, garante que
ele não existe antes de rodar e exige que exista depois. Sem isso não há como
distinguir o relatório de agora daquele que uma execução anterior deixou no projeto
— e relatório velho aprovando suíte nova é o falso sucesso mais silencioso que
existe.

Código de saída diferente de zero reprova o recurso. Quando o Cypress não roda, o
resumo diz `NÃO EXECUTADOS` em vez de deixar a cobertura estática passar por prova
de runtime.

⚠️ **Nunca chame `graphify extract` na mão.** Sem a flag `--code-only` que o
`qa-reindex.mjs` passa, ele faz extração semântica paga por LLM sobre o backend
inteiro, sem avisar. O Bloco 0 sempre passa pelo reindex.

---

## O que fica registrado

Cada execução cria `.execucoes/<AAAAMMDD-HHMMSS>-<pid>/` com:

```
execucao.jsonl              log estruturado, uma linha por evento
manifesto-execucao.json     o que era verdade na máquina quando esta execução rodou
artefatos/<recurso>/inventario.json
cobertura/<recurso>/cobertura.html
sandbox/                    só no --dry-run
```

Dá para reconstruir o que aconteceu sem reexecutar — cada `chamada_llm` traz
estágio, recurso, tentativa, modelo e tokens de entrada e saída; cada `gate` traz o
veredito e os códigos de violação. A execução termina em `execucao_concluida` ou em
`execucao_abortada` (com o motivo), nunca nos dois.

Toda linha carrega `schema_version`, para que log de ontem continue legível depois
de uma mudança de formato.

### Tipos de evento no JSONL

O catálogo abaixo é **gerado** a partir de `TipoDeEvento`, em
[`observabilidade/eventos.py`](src/orquestrador/observabilidade/eventos.py), e
[`tests/test_observabilidade_eventos.py`](tests/test_observabilidade_eventos.py) reprova se ele divergir do enum.
Era uma lista mantida à mão, e ela divergiu duas vezes no mesmo dia — não edite a
tabela: edite o enum e regenere.

<!-- INICIO DO CATALOGO DE EVENTOS: gerado por observabilidade/eventos.py -->
| Evento | O que registra |
| --- | --- |
| `execucao_iniciada` | abertura: dry-run, recursos pedidos, arquivo de configuração e os dois repositórios |
| `manifesto_de_execucao` | onde o `manifesto-execucao.json` foi escrito e quais campos não puderam ser coletados |
| `bloco0` | preparação determinística: se o `graph.json` ficou utilizável, e por quê |
| `superficie` | módulos compartilhados do projeto de testes e os exports que o executor pode importar |
| `estagio_tentativa` | uma tentativa de um estágio: tamanho da instrução fixa, da entrada e uso de tools |
| `chamada_llm` | uma chamada ao modelo: estágio, recurso, tentativa, modelo e tokens de entrada e saída |
| `tool` | uma chamada de tool do mapeador: ordem, argumentos, tamanho do retorno e erro |
| `gate` | veredito de um gate numa tentativa, com violações e avisos |
| `delta` | o delta enviado ao reparo: códigos de violação e tamanho do artefato atual |
| `artefatos` | arquivos que um estágio escreveu na área de staging da execução |
| `schemas_preservados` | schemas que já eram do consumidor e o mapeador não sobrescreveu |
| `schemas_divergentes` | campos que o mapeador achou no backend e o schema preservado não declara |
| `publicacao` | o que a publicação fez no projeto do consumidor, arquivo a arquivo, com hash e classificação |
| `artefatos_reprovados` | o que ficou em disco em estado reprovado, e se chegou a ser publicado |
| `staging_mantido` | o staging do recurso sobreviveu ao fim porque tem artefato para inspecionar |
| `cypress` | execução da suíte: código de saída e relatório desta execução, ou o motivo de não rodar |
| `cobertura` | contadores do `qa-cobertura.mjs` e se houve execução de runtime |
| `recurso_falhou` | o recurso terminou reprovado, com o motivo |
| `recurso_concluido` | desfecho do recurso: estado, tentativas e execução de testes |
| `telemetria` | agregados de token e de caracteres por estágio, recurso e tentativa |
| `execucao_interrompida` | o laço de recursos parou no meio por ferramenta indisponível; lista quem não rodou |
| `execucao_abortada` | a execução terminou sem veredito, com o motivo |
| `execucao_concluida` | fechamento: sucesso, interrupção e o resumo por recurso |
<!-- FIM DO CATALOGO DE EVENTOS -->

### `manifesto-execucao.json`

Responde ao chamado de suporte que o JSONL não responde: *"ontem passou, hoje
falhou"*. Ele registra o `run_id`, a versão do orquestrador, do Python e do Node, o
commit e o **estado sujo** dos dois repositórios quando são checkouts Git, a
impressão da skill, a configuração **redigida**, os modelos configurados por
estágio e o hash de cada prompt e de cada artefato.

É escrito **duas vezes**: no início, para que uma execução que morra no meio ainda
deixe o cabeçalho do chamado; e no fim, com os hashes dos artefatos.

Três regras que o módulo não pode violar, e que
[`tests/test_observabilidade_manifesto_de_execucao.py`](tests/test_observabilidade_manifesto_de_execucao.py) fixa:

* **segredo nunca entra** — o *nome* da variável de ambiente da chave entra, o
  valor não, nem mascarado; além da redação por nome de campo, o texto final é
  varrido atrás do valor real da chave;
* **código-fonte nunca entra** — de arquivo sai hash, nunca conteúdo;
* **sonda que falha não derruba a execução** — backend que não é repositório Git
  ou `node` fora do PATH deixam o campo ausente **com o motivo** em
  `campos_ausentes`, e o manifesto sai assim mesmo. Um diagnóstico que impede o
  trabalho é pior que um diagnóstico incompleto.

`schemas_preservados` é o que separa denominador independente de denominador
gerado: ele lista os schemas que já existiam no projeto do consumidor e que o
mapeador **não** sobrescreveu. Schema preservado foi escrito para outra
finalidade, por outra pessoa — vale mais como régua do que o que o próprio
modelo emite. Quando o mapeador encontra no backend um campo que o schema
preservado não declara, sai um aviso no console: o campo fica fora da conta de
cobertura por campo, e isso precisa ser visível.

### Como conferir se o Graphify está pagando o que promete

O Graphify existe para **localizar** código sem gastar token varrendo o backend, e
a instrução do mapeador manda consultá-lo antes de procurar na fonte —
`buscar_no_backend` é último recurso, não primeiro passo. Mas instrução não é
garantia: o modelo pode ignorar o grafo e sair lendo arquivo, e o custo do estágio
dobra sem que nada acuse.

Cada chamada de tool vira um evento `tool` com **ordem**, nome, argumentos,
tamanho do retorno e se ela falhou. Três leituras saem daí:

| Pergunta | Onde |
| --- | --- |
| consultou o grafo antes de ler arquivo? | `primeira_tool` no `estagio_tentativa` |
| que fatia da entrada veio de resposta de tool? | coluna `% do total` na tabela *Tools do mapeador* |
| o grafo está quebrado e ninguém viu? | coluna `erros` — as tools devolvem `ERRO: ...` como texto normal ao modelo |

A coluna `devolvido` é a que importa no custo, e por um motivo que não é óbvio:
cada caractere que uma tool devolve entra no histórico do ReAct e é **reenviado em
toda volta seguinte**. Resposta de tool grande é multiplicador, não parcela — é
por isso que uma `buscar_no_backend` generosa custa muito mais do que o próprio
retorno dela sugere.

O `--dry-run` exercita esse caminho de verdade: as tools são chamadas sobre
arquivos reais, só a resposta do modelo vem de fixture.

### Como conferir que o custo não é quadrático

Cada `chamada_llm` traz `caracteres_instrucao` (a instrução fixa do estágio, que é
constante) e `caracteres_entrada` (o que variou naquela tentativa). O evento
`estagio_tentativa` agrega os dois por tentativa, e a terceira tabela do resumo
final — *Entrada enviada por tentativa* — mostra isso lado a lado.

O que se procura ali é **a coluna `entrada` não crescer com o número da
tentativa**. Essa é a formulação correta do princípio 2.

Cuidado com uma leitura tentadora e errada: *"o reparo custa menos que a primeira
tentativa"* não vale em geral. No mapeador a primeira entrada é minúscula (nome do
recurso e caminhos) e o reparo carrega o manifesto inteiro, então ele é maior — e
está certo. O que a arquitetura garante é outra coisa: a entrada do reparo é função
do **artefato atual + violações atuais**, e de mais nada. Dobre o artefato e ela
cresce o tamanho do artefato; repita a tentativa dez vezes e ela não cresce nada.

O que fica de fato mais barato no reparo é o *total* da tentativa: ela não repete a
exploração por tools que a primeira volta fez, então tem menos chamadas ao modelo.
Os testes em [`tests/test_invariante_principio_2.py`](tests/test_invariante_principio_2.py) fixam as duas
afirmações — e quebram se alguém concatenar histórico "para dar mais contexto".

---

## Estrutura

```
pyproject.toml       deps + configuração de pytest + empacotamento, num arquivo só
config.toml          configuração de execução — é do usuário; nasce de `orquestrador init`
prompts/             instrução fixa de cada estágio (editorial; mapeado para o wheel)
fixtures/            artefatos do --dry-run — desenvolvimento, fora do wheel
tests/
src/orquestrador/
  __init__.py        docstring do pacote
  __main__.py        ponto de entrada de `python -m orquestrador`
  cli.py             argumentos, montagem da execução e apresentação
  pipeline.py        a classe Pipeline e o _ciclo (o loop de reparo)
  config.py          carga e validação da configuração
  simulacao.py       modelo falso dirigido por fixture + sandbox do --dry-run
  excecoes.py        FalhaDeGate, FalhaDeEstagio, ErroDeFerramenta, ErroDeConfiguracao
  raiz.py            resolução da raiz do projeto — único uso de Path(__file__)
  dominio/
    __init__.py
    endpoint.py      o vocabulário HTTP que inventário e manifesto compartilham
    recurso.py       Recurso e NomeDeRecurso — a unidade de trabalho e o nome que vira diretório
    inventario.py    o que o backend expõe, segundo quem leu o código
    manifesto.py     o gabarito de cobertura — espelho de _support/cobertura.json
    veredito.py      Violacao, ResultadoGate, Delta, EstadoDoRecurso
    artefatos.py     SaidaMapeador, SaidaExecutor e o confinamento de forma de caminho
    propriedade.py   diário de propriedade, classificação e divergência de schema
    superficie.py    o que o projeto de testes do consumidor já oferece ao executor
    auditoria.py     o veredito do auditor semântico
  llm/
    __init__.py
    cliente.py       cliente OpenRouter, seleção por estágio
    mensagens.py     uso de token, texto e tamanho de entrada
    estruturado.py   saída estruturada + mini-loop de reparo de schema
    montagem.py      carga dos prompts e a regra do prompt de reparo
  observabilidade/
    __init__.py
    eventos.py       TipoDeEvento — o vocabulário fechado do JSONL e a versão do formato
    medidas.py       UsoDeTokens, RegistroDeChamada, RegistroDeTool — o que se mede
    manifesto_de_execucao.py  manifesto-execucao.json: ambiente, commits, hashes, config redigida
    registro.py      log estruturado (JSONL) + console
    telemetria.py    agregação de tokens e caracteres por estágio, recurso, tentativa
    tabelas.py       as tabelas Rich do resumo final
  agentes/
    __init__.py
    mapeador.py                 a unidade de trabalho do Bloco 1
    ferramentas_do_mapeador.py  as cinco tools e a medição de cada chamada
    grafo_react.py              todo o acoplamento com o LangGraph
    executor.py                 chamada estruturada, sem tools
    auditor.py                  STUB, interface definida
  analise_estatica/
    __init__.py
    exports_javascript.py    parser puro dos `export` de um módulo JS
    tags_cypress.py          parser puro das tags @endpoint/@cat de um spec
    extrator_de_superficie.py  acha os módulos compartilhados e calcula os imports
    rotas_java_spring.py       parser puro das anotações de rota do Spring MVC
    extrator_de_endpoints.py   matriz de suporte + grafo → endpoints do backend
  ferramentas/
    __init__.py
    processo.py      subprocess (lista de argumentos, utf-8, os dois fluxos)
    graphify.py      wrappers query/affected/reindex
    arquivos.py      ler/listar/buscar com confinamento de caminho
    privacidade.py   denylist, .llmignore e redação de segredo antes do envio
    publicacao.py    staging por recurso, diário de propriedade, publicação atômica
    scripts_qa.py    wrappers dos .mjs da skill
    json_externo.py  extrair_json tolerante de stdout de ferramenta (única impl.)
  gates/
    __init__.py
    codigos.py       catálogo dos códigos de violação QAORQ-
    gate_a.py        --so-manifesto + diff grafo × manifesto
    gate_b.py        prettier + eslint + validador + lacuna de cobertura
    lacunas.py       QAORQ-030: categoria planejada que não virou teste
    saidas.py        JSON dos .mjs → ResultadoGate/Violacao
```

`analise_estatica/` responde "o que existe neste JavaScript" sem executá-lo, e é a
razão de `exports_javascript.py` não morar em `ferramentas/`: entra texto, sai
`ExportJs`. `ferramentas/` fica reservado ao adaptador de disco, de subprocesso e
de CLI de terceiro.

[`tests/test_invariante_estrutura_do_codigo.py`](tests/test_invariante_estrutura_do_codigo.py) **verifica
esta árvore**: módulo de produção que não aparece aqui reprova, e linha aqui que
não corresponde a arquivo também. Foi a omissão de dois módulos que fez um revisor
externo procurar arquivo no lugar errado — a árvore é documentação executável, não
enfeite. O mesmo arquivo fixa a lista fechada da raiz do pacote, a direção de
dependência entre os subpacotes e a proibição de reexport em `__init__.py`.

**Onde o Bloco 1 escreve.** O manifesto vai para `_support/cobertura.json`, dentro do
diretório do recurso; os **schemas de entrada** vão para
`[caminhos].dir_schemas` (`cypress/fixtures/schemas/<recurso>/`), que fica **fora**
dele. Quem os emite é o mapeador, não o executor: o schema é o denominador da
cobertura por campo, e denominador pertence ao plano. Se o executor o escrevesse,
estaria escrevendo a própria régua — a circularidade que esta arquitetura existe para
eliminar. O confinamento do executor ao diretório do recurso continua intacto.

**Quando o projeto do consumidor é tocado.** Uma vez por recurso, depois que os
dois gates aprovaram. Até lá, cada tentativa do loop escreve numa **área de
staging** da execução, e é o staging que os gates validam — validar uma coisa e
publicar outra era o buraco por onde uma tentativa ruim sobrescrevia a suíte de
quem paga pela ferramenta.

O staging do recurso é um irmão do diretório real, no mesmo nível
(`cypress/e2e/apis/.qa-staging-<execucao>-<recurso>`). Precisa ser ali, e não em
`.execucoes/`, por duas resoluções de caminho da skill: os specs importam os
módulos compartilhados por caminho relativo (`../../../../support/api/...`), que o
validador resolve a partir do arquivo, e o `cobertura/handlers.mjs` **sobe** do
recurso procurando `.agents/config/qa-api/handlers.json`. Os schemas, esses, ficam
em `.execucoes/<ts>/staging/`, porque as duas ferramentas aceitam o diretório
pronto (`--schemas`). O ponto inicial do nome mantém o staging fora do
`specPattern` padrão do Cypress.

A publicação é atômica por recurso, com verificação de conflito antes e rollback
em caso de falha no meio: uma interrupção deixa o projeto byte a byte como estava.
Cada arquivo tocado vira uma linha no **diário de propriedade**
(`.execucoes/diario-de-propriedade.json`) com caminho, hash anterior, hash novo e
classificação `criado` / `modificado` / `preexistente`. É esse diário que devolve
o `--remover-reprovados`, restrito ao que **nós** criamos e que ninguém editou
desde então — e que autoriza remover spec obsoleto de execução anterior sob a
mesma regra. Sem diário, nada é removido.

**Por que `prompts/` fica fora de `src/` e mesmo assim vai no wheel.** Prompt é
conteúdo editorial, iterado por quem não necessariamente mexe em Python, e por isso
mora na raiz do repositório, longe do código. Só que sem ele não há estágio de LLM:
o wheel **precisa** levá-lo, e antes não levava — instalava com sucesso e falhava no
primeiro comando.

A conciliação é de empacotamento, não de cópia. O `pyproject.toml` mapeia o
diretório `prompts/` da raiz para o pacote `orquestrador.prompts`
(`[tool.setuptools].package-dir`), e o build grava ali dentro do wheel o conteúdo
que já mora na raiz. **Não existe segunda cópia versionada para divergir**: num
checkout só existe `prompts/`; num ambiente instalado só existe
`orquestrador/prompts/`. Quem resolve os dois casos é
[`raiz.py`](src/orquestrador/raiz.py), por `importlib.resources`, e
`[caminhos].prompts` continua sendo o override declarado.

O preço é uma lista explícita de `packages` no `pyproject.toml` — `packages.find`
varre `where` e nunca acharia um diretório fora de `src/`. Quem cobra que ela não
envelheça é `tests/test_invariante_empacotamento.py`: subpacote novo que não apareça lá reprova,
em vez de sumir do wheel em silêncio.

**Por que `fixtures/` e `config.toml` NÃO vão no wheel.** `fixtures/` é material de
desenvolvimento do `--dry-run` — backend e projeto Cypress de mentira, roteiros de
resposta —, e empacotá-lo faria todo usuário baixar o banco de testes deste
repositório. `config.toml` é do usuário, e nasce de `orquestrador init`. A
consequência declarada é que a instalação pelo wheel não tem `--dry-run`: quem
quiser conferir a instalação usa `orquestrador doctor`, que não depende de fixture
nenhuma.

**Onde mora `Path(__file__)`.** Em [`raiz.py`](src/orquestrador/raiz.py), e só lá.
Havia cinco módulos calculando a raiz por conta própria; depois que o código desceu
para `src/`, cada um passaria a apontar para dentro do pacote e uma saída
configurada como `.execucoes` iria parar em `src/orquestrador/.execucoes/` — sem
erro nenhum, só no lugar errado.

### Contratos de dados

`Recurso`, `Endpoint`, `Inventario`, `Manifesto`, `Violacao`, `Delta`,
`ResultadoGate`, `SaidaMapeador`, `ArquivoSchema`, `SaidaExecutor`,
`ResultadoAuditoria` — todos em [`dominio/`](src/orquestrador/dominio), um módulo
por vocabulário.

`SaidaMapeador.schemas` é uma lista de `ArquivoSchema`, com o caminho relativo à raiz
de schemas (`<recurso>/<nome>.schema.json`, sem `..` e sem raiz absoluta, como o
`ArquivoGerado` do executor). Um validador cruzado exige que todo `schemaEntrada`
declarado no manifesto tenha o arquivo correspondente na lista — o ponteiro JSON
opcional (`entidade#/properties/entity`) escolhe o nó dentro do arquivo e sai antes da
comparação. Rejeitar aqui vira um delta de schema, o reparo mais barato que existe,
sem tirar do Gate A a autoridade sobre o arquivo em disco: quem reprova o schema
ausente continua sendo o `validar-suite-gerada.mjs`, com `QAAPI-027`.

O `Manifesto` espelha `_support/cobertura.json`, cujo formato é definido **pela
skill** (SKILL.md passo 6 + `scripts/cobertura/manifesto.mjs` e `estrutura.mjs`).
Ele valida apenas o que é estrutural: tipos, ids de categoria bem formados,
endpoint na forma canônica, ausência de campo desconhecido. A **contabilidade das
12 categorias** fica deliberadamente de fora — quem reprova isso é o
`validar-suite-gerada.mjs` (princípio 4). Duplicar a regra no Pydantic apagaria o
Gate A do fluxo e criaria duas fontes de verdade para a mesma invariante.

### Saída estruturada é tratada como não confiável

Suporte a *structured output* e a *tool calling* varia muito entre modelos do
OpenRouter. O fluxo é sempre: pedir pelo mecanismo configurado
(`modo_estruturado`), validar com Pydantic aqui, e se falhar montar um `Delta` de
estágio `"schema"` e reenviar **só ele** — mini-loop praticamente grátis em
contexto, limitado por `max_tentativas_schema`. Ao estourar, o erro diz qual
recurso e qual estágio.

### Códigos de violação

Os `QAAPI-0xx` vêm dos scripts da skill. O orquestrador usa o prefixo `QAORQ-`
para nunca colidir:

| Código | Significado |
| --- | --- |
| `QAORQ-001` | trecho que o diff grafo × manifesto não conseguiu resolver (aviso) |
| `QAORQ-002` | endpoint existe no backend e não está no gabarito |
| `QAORQ-003` | endpoint declarado no gabarito sem correspondente no backend |
| `QAORQ-010` | saída do modelo não valida contra o contrato Pydantic |
| `QAORQ-011` | o modelo não devolveu JSON no formato pedido |
| `QAORQ-020` / `-021` | prettier / eslint reprovaram |
| `QAORQ-022` | formatador configurado mas ausente do PATH |
| `QAORQ-030` | categoria declarada em `cats` sem nenhum `it` que a cubra |
| `QAORQ-040` | schema preservado do consumidor não declara campo que o mapeador achou |

### A lacuna é um gate, não só um número no relatório

`QAORQ-030` fecha o buraco que sobrou entre os dois scripts da skill. O
`validar-suite-gerada.mjs` prova **forma** — manifesto contabilizado, specs-base
presentes, imports resolvidos, campo do schema com teste `@campo` ou exceção. Ele
não confere se cada categoria declarada em `cats` virou um `it`. Quem sabe disso é
o `qa-cobertura.mjs`, que classifica cada célula e conta as `lacunas` — só que era
relatório, rodava no Bloco 3 depois do loop, e saía com código 0 de qualquer jeito.

O número aparecia na tela e ninguém agia sobre ele. Foi assim que uma execução real
gerou 50 testes, deixou `CAT-07` (regras de negócio) sem um único teste nos cinco
endpoints, e **passou** no Gate B. É o defeito "planejei e não entreguei" — o
mesmo que motivou o projeto — uma camada acima de onde os gates olhavam.

Agora o `qa-cobertura.mjs` roda **dentro** do Gate B, e `lacunas > 0` reprova.
Desligável em `[gates.b].exigir_cobertura`, ligado por padrão.

**Autoridade e detalhe são coisas diferentes.** Quem decide se reprova é o contador
do script; o orquestrador nunca recalcula esse número. Mas "6 lacunas" não diz ao
executor o que escrever, então o detalhe é reconstruído cruzando o manifesto com as
tags dos specs — e **conferido contra o contador antes de ser usado**. Se as duas
contas divergirem, a lista é descartada e o delta sai só com o número. Um par
endpoint×categoria errado na lista faria o executor gastar tentativa consertando o
que não estava quebrado, e palpite com cara de precisão é pior que número honesto.
Pela mesma razão, spec com tag dinâmica (`@cat ${...}`, a forma data-driven que a
skill permite) desliga o detalhe: este parser não resolve template, e o que ele não
resolve pareceria lacuna.

---

## Testes

```bash
python -m pytest
```

Roda de qualquer diretório de trabalho (o pacote é instalado, não achado por
`sys.path`).

Cobrem: parsing dos gates (incluindo stdout×stderr e exit 2), montagem do delta,
confinamento de caminho, o loop de reparo, os contratos Pydantic, a etapa de
formatadores do Gate B e o dry-run ponta a ponta. Os testes de integração pulam
sozinhos se o Node ou a skill não estiverem disponíveis.

Dois módulos guardam invariantes de **comportamento**, não de estrutura — são os
que uma refatoração quebraria em silêncio:

| Arquivo | O que protege |
| --- | --- |
| [`test_invariante_principio_2.py`](tests/test_invariante_principio_2.py) | a entrada de um reparo não leva nada da tentativa anterior, nos dois estágios de LLM |
| [`test_invariante_falhas_isoladas.py`](tests/test_invariante_falhas_isoladas.py) | um recurso que falha não derruba os seguintes, e o artefato reprovado que fica em disco é anunciado |

---

## O que é stub

| Item | Estado | Onde |
| --- | --- | --- |
| Auditor semântico | stub; `auditar(..., permitir_stub=True)` devolve veredito vazio marcado `"revisar"` | `agentes/auditor.py` |
| Prettier / ESLint | implementados e testados, **desligados por padrão** | `[execucao]` no `config.toml` |

O auditor devolve `"revisar"`, nunca `"íntegro"`, exatamente para que o stub não
seja confundido com auditoria feita — e `--auditor` encerra com erro em vez de
sair com código 0. Prettier e ESLint vêm desligados porque o projeto de fixture
não tem toolchain Node instalado; ligue-os apontando para o do consumidor.

**O diff grafo × manifesto saiu daqui.** Ele existe e reprova, mas só onde há
adaptador de rota — hoje, Java/Spring. Isso não é estado de implementação, é
**alcance**: veja [Matriz de suporte](#matriz-de-suporte). Fora do Tier A o Gate A
responde erro de ferramenta, nunca aprovação.

Vale registrar por que ele demorou: a suposição de trabalho era que o `graph.json`
traria os endpoints. Não traz — para Java, o grafo tem arquivo, classe e método
com linha, e **nenhuma informação de HTTP**. Verbo e rota vivem na anotação, no
texto do fonte. O grafo entrega a lista de arquivos já filtrada pelos excludes da
indexação; quem lê a rota é um parser por framework. É por isso que "suportar
qualquer backend" é uma matriz de adaptadores, e não uma instrução de prompt.
