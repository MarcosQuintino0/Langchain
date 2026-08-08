# Orquestrador multi-agente da skill `qa-api` — Fase 1

Orquestrador em Python que coordena três estágios (dois com LLM, um determinístico)
para gerar suítes de teste Cypress de API, dirigido pela skill `qa-api`.

Este projeto (`C:\LangChainTestes`) é **independente** do repositório da skill
(`C:\agentesQualidade\qa-agent-skills`). Ele apenas **consome** a skill: invoca os
scripts `.mjs` dela por subprocess e nunca modifica nada dentro de
`skills/qa-api/`. O caminho da skill é configuração — veja `[caminhos].skill` em
[`config.toml`](config.toml).

> **Esta é a Fase 1: arquitetura e esqueleto executável.** Toda a fiação existe e
> roda ponta a ponta; o **conteúdo dos prompts dos agentes é Fase 2**. Onde eles
> entrariam há arquivos-placeholder com a interface já definida. Veja
> [O que é stub](#o-que-é-stub-nesta-fase).

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
         GATE A: validar-suite-gerada --so-manifesto  +  diff grafo×manifesto [STUB]
         reprova → delta → volta ao mapeador
BLOCO 2  EXECUTOR (LLM, sem tools)               um recurso por vez
         → specs *.cy.js com tags @endpoint @cat @campo
         GATE B: prettier + eslint + validar-suite-gerada
         reprova → delta (QAAPI-0xx) → volta ao executor
BLOCO 3  Cypress + qa-cobertura.mjs --json       determinístico
         AUDITOR SEMÂNTICO [STUB] — sob demanda, fora do loop
```

**Por que o Gate A tem duas checagens.** O validador da skill enxerga apenas o
projeto de testes, nunca o backend — limite deliberado, documentado em
`skills/qa-api/scripts/cobertura/handlers.mjs:15`. Ele prova *"entreguei o que
planejei"*, nunca *"planejei tudo que existe"*. O diff grafo × manifesto é o que
fecharia esse elo, e é **stub nesta fase**.

---

## Instalação

Requer Python 3.12+, Node 24+ e Git já instalados. Sem Docker, sem container.

```bash
cd C:\LangChainTestes
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

O projeto é instalável (`src/orquestrador`), então `orquestrador` fica importável
de qualquer diretório de trabalho — é o que permite rodar `pytest` de onde for.
Sem o extra `[dev]` você fica sem o `pytest`. Dependências e configuração de teste
vivem num arquivo só: [`pyproject.toml`](pyproject.toml).

Confira também `[caminhos].skill` no `config.toml`: ele aponta para o repositório
da skill `qa-api`, que é outro projeto. Sem ele, os gates não têm o que invocar.

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
| `[gates.a]` / `[gates.b]` | flags do validador e `max_tentativas` de cada gate |
| `[execucao]` | executáveis (node, graphify, prettier, eslint, cypress) e limites |

Antes do primeiro uso real, ajuste `backend`, `projeto_testes` e os três
`estagios.*.modelo` (vêm com placeholder `<defina: ...>`; o pipeline recusa rodar
com ele e diz qual estágio corrigir).

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
| `--remover-reprovados` | **indisponível** — recusa com código 2 |

Códigos de saída: `0` sucesso, `1` algum gate esgotou as tentativas, `2` erro de
configuração, indisponibilidade de ferramenta ou flag recusada.

**Por que as duas flags recusam em vez de sumir.** Continuam reconhecidas pelo
argparse para não quebrar script existente em silêncio, mas encerram com código 2 e
uma mensagem dizendo o que falta.

O `--auditor` imprimia o veredito do stub e saía com **0**. Em CI, isso é
indistinguível de auditoria feita — e o auditor não existe. O
`--remover-reprovados` apagava todo caminho da lista de reprovados sem distinguir
arquivo que criamos de arquivo que já era do cliente. Ele volta quando existir o
diário de propriedade da Etapa 2, que registra por arquivo se ele foi criado,
modificado ou preexistente.

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
artefatos/<recurso>/inventario.json
cobertura/<recurso>/cobertura.html
sandbox/                    só no --dry-run
```

Tipos de evento no JSONL: `execucao_iniciada`, `bloco0`, `superficie`,
`estagio_tentativa`, `chamada_llm`, `tool`, `gate`, `delta`, `artefatos`,
`schemas_preservados`, `artefatos_reprovados`, `cypress`, `cobertura`,
`recurso_falhou`, `recurso_concluido`, `telemetria`, `execucao_abortada`,
`execucao_interrompida`, `execucao_concluida`. Dá para reconstruir o que aconteceu sem
reexecutar — cada `chamada_llm` traz estágio, recurso, tentativa, modelo e tokens de
entrada e saída; cada `gate` traz o veredito e os códigos de violação. A execução
termina em `execucao_concluida` ou em `execucao_abortada` (com o motivo), nunca nos
dois.

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
Os testes em [`tests/test_principio_2.py`](tests/test_principio_2.py) fixam as duas
afirmações — e quebram se alguém concatenar histórico "para dar mais contexto".

---

## Estrutura

```
pyproject.toml       deps + configuração de pytest, num arquivo só
config.toml          configuração de execução — é do usuário, fica na raiz
prompts/             PLACEHOLDERS — conteúdo é Fase 2
fixtures/            artefatos do --dry-run
tests/
src/orquestrador/
  __init__.py        docstring do pacote
  __main__.py        ponto de entrada de `python -m orquestrador`
  cli.py             argumentos, montagem da execução e apresentação
  pipeline.py        a classe Pipeline e o _ciclo (o loop de reparo)
  config.py          carga e validação da configuração
  contratos.py       todos os modelos Pydantic
  simulacao.py       modelo falso dirigido por fixture + sandbox do --dry-run
  excecoes.py        FalhaDeGate, FalhaDeEstagio, ErroDeFerramenta, ErroDeConfiguracao
  raiz.py            resolução da raiz do projeto — único uso de Path(__file__)
  llm/
    __init__.py
    cliente.py       cliente OpenRouter, seleção por estágio
    mensagens.py     uso de token, texto e tamanho de entrada
    estruturado.py   saída estruturada + mini-loop de reparo de schema
    montagem.py      carga dos prompts e a regra do prompt de reparo
  observabilidade/
    __init__.py
    registro.py      log estruturado (JSONL) + console
    telemetria.py    agregação de tokens e caracteres por estágio, recurso, tentativa
    tabelas.py       as tabelas Rich do resumo final
  agentes/
    __init__.py
    mapeador.py      agente ReAct + registro das tools
    executor.py      chamada estruturada, sem tools
    auditor.py       STUB, interface definida
  analise_estatica/
    __init__.py
    exports_javascript.py    parser puro dos `export` de um módulo JS
    tags_cypress.py          parser puro das tags @endpoint/@cat de um spec
    extrator_de_superficie.py  acha os módulos compartilhados e calcula os imports
  ferramentas/
    __init__.py
    processo.py      subprocess (lista de argumentos, utf-8, os dois fluxos)
    graphify.py      wrappers query/affected/reindex
    arquivos.py      ler/listar/buscar com confinamento de caminho
    scripts_qa.py    wrappers dos .mjs da skill
    json_externo.py  extrair_json tolerante de stdout de ferramenta (única impl.)
  gates/
    __init__.py
    codigos.py       catálogo dos códigos de violação QAORQ-
    gate_a.py        --so-manifesto + STUB do diff grafo×manifesto
    gate_b.py        prettier + eslint + validador + lacuna de cobertura
    lacunas.py       QAORQ-030: categoria planejada que não virou teste
    saidas.py        JSON dos .mjs → ResultadoGate/Violacao
```

`analise_estatica/` responde "o que existe neste JavaScript" sem executá-lo, e é a
razão de `exports_javascript.py` não morar em `ferramentas/`: entra texto, sai
`ExportJs`. `ferramentas/` fica reservado ao adaptador de disco, de subprocesso e
de CLI de terceiro.

[`tests/test_estrutura_do_codigo.py`](tests/test_estrutura_do_codigo.py) **verifica
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

**Por que `prompts/`, `fixtures/` e `config.toml` ficam fora do pacote.** Prompt é
conteúdo, não código: a Fase 2 é trabalho editorial, iterado por quem não
necessariamente mexe em Python. O caminho vem da configuração
(`[caminhos].prompts`), com padrão na raiz.

**Onde mora `Path(__file__)`.** Em [`raiz.py`](src/orquestrador/raiz.py), e só lá.
Havia cinco módulos calculando a raiz por conta própria; depois que o código desceu
para `src/`, cada um passaria a apontar para dentro do pacote e uma saída
configurada como `.execucoes` iria parar em `src/orquestrador/.execucoes/` — sem
erro nenhum, só no lugar errado.

### Contratos de dados

`Recurso`, `Endpoint`, `Inventario`, `Manifesto`, `Violacao`, `Delta`,
`ResultadoGate`, `SaidaMapeador`, `ArquivoSchema`, `SaidaExecutor`,
`ResultadoAuditoria` — todos em [`contratos.py`](src/orquestrador/contratos.py).

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
| `QAORQ-001` | diff grafo × manifesto ainda não implementado (aviso do stub) |
| `QAORQ-002` / `-003` | reservados para o diff quando implementado |
| `QAORQ-010` | saída do modelo não valida contra o contrato Pydantic |
| `QAORQ-011` | o modelo não devolveu JSON no formato pedido |
| `QAORQ-020` / `-021` | prettier / eslint reprovaram |
| `QAORQ-022` | formatador configurado mas ausente do PATH |
| `QAORQ-030` | categoria declarada em `cats` sem nenhum `it` que a cubra |

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
| [`test_principio_2.py`](tests/test_principio_2.py) | a entrada de um reparo não leva nada da tentativa anterior, nos dois estágios de LLM |
| [`test_falhas_isoladas.py`](tests/test_falhas_isoladas.py) | um recurso que falha não derruba os seguintes, e o artefato reprovado que fica em disco é anunciado |

---

## O que é stub nesta fase

| Item | Estado | Onde |
| --- | --- | --- |
| Conteúdo dos prompts dos agentes | placeholder com a interface definida | `prompts/*.md` |
| Diff grafo × manifesto (Gate A) | stub documentado; emite aviso `QAORQ-001` e **não** reprova | `gates/gate_a.py::diff_grafo_manifesto` |
| Auditor semântico | stub; `auditar(..., permitir_stub=True)` devolve veredito vazio marcado `"revisar"` | `agentes/auditor.py` |
| Prettier / ESLint | implementados e testados, **desligados por padrão** | `[execucao]` no `config.toml` |

O diff está stub porque a implementação exige sondar o formato real do
`graph.json`, que varia por extrator de linguagem. O auditor devolve `"revisar"`,
nunca `"íntegro"`, exatamente para que o stub não seja confundido com auditoria
feita. Prettier e ESLint vêm desligados porque o projeto de fixture não tem
toolchain Node instalado; ligue-os apontando para o do projeto consumidor.
