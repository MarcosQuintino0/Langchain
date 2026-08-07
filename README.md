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
   [`montagem.py`](src/orquestrador/montagem.py), num lugar só, para não escapar por descuido.
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
| `--auditor` | mostra o veredito do auditor semântico (stub) e sai |
| `--remover-reprovados` | apaga, ao final, os artefatos deixados em estado reprovado |

Códigos de saída: `0` sucesso, `1` algum gate esgotou as tentativas, `2` erro de
configuração ou de invocação de ferramenta.

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

Tipos de evento no JSONL: `execucao_iniciada`, `bloco0`, `estagio_tentativa`,
`chamada_llm`, `gate`, `delta`, `artefatos`, `artefatos_reprovados`,
`artefatos_removidos`, `cobertura`, `recurso_falhou`, `recurso_concluido`,
`telemetria`, `execucao_concluida`. Dá para reconstruir o que aconteceu sem
reexecutar — cada `chamada_llm` traz estágio, recurso, tentativa, modelo e tokens
de entrada e saída; cada `gate` traz o veredito e os códigos de violação.

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
  cli.py             argumentos, montagem da execução e apresentação
  pipeline.py        a classe Pipeline e o _ciclo (o loop de reparo)
  config.py          carga e validação da configuração
  contratos.py       todos os modelos Pydantic
  montagem.py        carga dos prompts e a regra do prompt de reparo
  simulacao.py       modelo falso dirigido por fixture + sandbox do --dry-run
  excecoes.py        FalhaDeGate, FalhaDeEstagio, ErroDeFerramenta, ErroDeInvocacao
  raiz.py            resolução da raiz do projeto — único uso de Path(__file__)
  textos.py          extrair_json tolerante (implementação única)
  llm/
    cliente.py       cliente OpenRouter, seleção por estágio
    mensagens.py     uso de token, texto e tamanho de entrada
    estruturado.py   saída estruturada + mini-loop de reparo de schema
  observabilidade/
    registro.py      log estruturado (JSONL) + console
    telemetria.py    tokens e caracteres por estágio, recurso e tentativa
  agentes/
    mapeador.py      agente ReAct + registro das tools
    executor.py      chamada estruturada, sem tools
    auditor.py       STUB, interface definida
  ferramentas/
    processo.py      subprocess (lista de argumentos, utf-8, os dois fluxos)
    graphify.py      wrappers query/affected/reindex
    arquivos.py      ler/listar/buscar com confinamento de caminho
    scripts_qa.py    wrappers dos .mjs da skill
  gates/
    gate_a.py        --so-manifesto + STUB do diff grafo×manifesto
    gate_b.py        prettier + eslint + validador completo
    parser.py        JSON dos .mjs → ResultadoGate/Violacao
```

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
`ResultadoGate`, `SaidaMapeador`, `SaidaExecutor`, `ResultadoAuditoria` — todos em
[`contratos.py`](src/orquestrador/contratos.py).

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
