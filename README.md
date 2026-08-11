# Orquestrador multi-agente de testes de API

Orquestrador em Python que coordena três estágios (dois com LLM, um determinístico)
para gerar suítes de teste Cypress de API a partir de um backend.

Nada fora do pacote precisa existir na máquina de quem o roda: `pip install` e
pronto. O extrator de grafo (Graphify) é dependência declarada em
[`pyproject.toml`](pyproject.toml) e é instalado junto.

**Até 2026-08-10 não era assim.** O pipeline consumia a skill `qa-api`, um
repositório externo: invocava os `.mjs` dela por subprocess, exigia Node 24 e
recusava rodar quando o hash daqueles scripts mudava. Nada disso existe hoje — não
há `[caminhos].skill`, nem seção `[skill]`, nem `[execucao].node`, e nenhum
JavaScript é invocado pelo orquestrador. Os prompts de `prompts/` continuam sendo
condensação **manual** das `references/` dela, e é de lá que vem o vocabulário das
12 categorias. O que se perdeu junto com os `.mjs` — a reconciliação de cobertura
por categoria e por campo, principalmente — está registrado em
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md).

> **O que ainda é stub:** o auditor semântico. Veja
> [O que é stub](#o-que-é-stub). O resto do fluxo roda ponta a ponta, com escrita
> transacional e verificação determinística em cada gate — inclusive o diff
> grafo × manifesto, que só existe hoje para backend Java/Spring (veja
> [Matriz de suporte](#matriz-de-suporte)).

---

## Documentação

Este README ensina **o que o projeto é e como rodá-lo**. O resto — glossário,
diagramas, referência da CLI e da configuração, o porquê de cada ferramenta e as
decisões de arquitetura — está em [`docs/`](docs/index.md), como um site MkDocs:

```bash
pip install -e ".[docs]" && mkdocs serve
```

O site **não é publicado**. A CI o constrói com `--strict`, para reprovar link
quebrado e página fora da navegação, e descarta o resultado
([ADR 0011](docs/adr/0011-documentacao-sem-publicacao.md)).

E o [`AGENTS.md`](AGENTS.md) diz **o que não pode ser quebrado e onde as coisas
moram**. Os três não se repetem de propósito: regra duplicada só descobre que
divergiu depois que já foi seguida errada.

---

## Por que esta arquitetura existe

A skill `qa-api` executada por um único modelo, numa sessão só, apresentava dois
defeitos medidos. Eles não sumiram quando o projeto se desacoplou dela: são
propriedade de instrução densa executada de uma vez, e é contra os dois que este
desenho existe.

**Completude.** O modelo não implementa todos os testes que a própria skill exige.
A causa não é falta de capacidade: é horizonte longo somado a instrução densa. O
modelo amostra em vez de enumerar, e isso passa despercebido porque o gabarito de
cobertura é escrito pelo mesmo modelo que depois vai satisfazê-lo.

**Custo quadrático de token.** Tentativas anteriores de dividir o trabalho
reenviavam o contexto inteiro a cada etapa e a cada correção.

O desenho ataca os dois: **decomposição por estágio** (cada agente recebe só a
fatia da instrução do seu estágio) e **verificação determinística** (contar 12
categorias × N endpoints × M campos é trabalho de script, não de LLM) contra o
primeiro; **handoff por artefato em disco** e **loops de reparo que enviam só o
delta** contra o segundo.

É o padrão *Assured LLM-based Software Engineering*: o LLM gera, filtros
determinísticos descartam o que não presta. O LLM nunca é o verificador primário.

### Os seis princípios

1. O que trafega entre estágios é **artefato em disco**, nunca histórico de conversa.
2. O loop de reparo envia **apenas o delta**.
3. Agentes são **stateless** entre unidades de trabalho.
4. **Quem reprova é script**; LLM só cria.
5. O auditor semântico fica **fora do loop quente**.
6. **Nenhum nome de modelo** em código ou prompt.

A razão de cada um está em
[`docs/arquitetura/os-seis-principios.md`](docs/arquitetura/os-seis-principios.md).

---

## Os quatro blocos

```
BLOCO 0  Graphify (AST, --code-only)             determinístico, zero token
         → .agents/state/qa-api/graphify-out/graph.json
BLOCO 1  MAPEADOR (LLM + tools, ReAct)           um recurso por vez
         → inventario.json + _support/cobertura.json + dossiê
         GATE A: diff grafo × manifesto  +  conferência do dossiê
         reprova → delta → volta ao mapeador
BLOCO 2  EXECUTOR (LLM, sem tools)               um recurso por vez
         → specs *.cy.js com tags @endpoint @cat @campo
         GATE B: prettier + eslint (opcionais) + limpeza gerada (QAORQ-031/032/033)
         reprova → delta (QAORQ-0xx) → volta ao executor
BLOCO 3  Cypress                                 determinístico
         AUDITOR SEMÂNTICO [STUB] — sob demanda, fora do loop
```

**Por que o Gate A tem duas checagens.** O diff grafo × manifesto responde
*"planejei tudo que existe"*, e é a única checagem do pipeline cujo denominador
não passa por LLM nenhum: ele sai do fonte do backend. A conferência do dossiê
responde outra coisa, mais barata e igualmente necessária — que a evidência que o
mapeador citou aponta arquivo e linha que existem de verdade.

Quem provava *"entreguei o que planejei"* era o validador da skill, que enxergava
apenas o projeto de testes e nunca o backend. Com o desacoplamento essa metade
ficou descoberta, e é a maior perda registrada em
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md).

---

## Matriz de suporte

Backend **Java/Spring** e projeto de testes **Cypress em JavaScript** são o Tier A —
a combinação avaliada, em que os quatro blocos funcionam inteiros. Fora dela a
ferramenta falha com diagnóstico: nunca aprova, nunca promete cobertura que não
sabe medir.

Os três tiers, os limites que valem **dentro** do Tier A e o que exatamente falta
em cada um estão em [`docs/referencia/matriz-de-suporte.md`](docs/referencia/matriz-de-suporte.md).

## Instalação

Requer Python 3.13 e Git já instalados. Sem Docker, sem container. Node deixou de
ser pré-requisito no desacoplamento: ele só entra se você ligar o Cypress, o
prettier ou o eslint do seu projeto, que são comandos opcionais de `[execucao]`.

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
modelo de cada estágio, o Graphify, o projeto de testes e seus módulos
compartilhados, o backend e a presença da chave do provedor — **a presença, nunca o
valor**. Sai com código 2 se algum item reprovar, para servir de porta de CI.

Skill e Node saíram da lista, e o Graphify deixou de ser conferido contra o
`manifest.json` de um repositório de terceiro: sendo dependência do pacote, ele
chega na versão certa junto com a instalação, e não há segunda fonte para
divergir.

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

Confira `backend` e `projeto_testes` no `config.toml`: depois do desacoplamento
são os dois únicos caminhos que apontam para fora deste repositório, e sem eles só
o `--dry-run` funciona.

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
chamar qualquer modelo**, com a mensagem dizendo o que fazer. Ele não põe uma
arquitetura-base no lugar: isso produziria imports que não existem no seu projeto, e
o loop de reparo não converge sobre nome de símbolo que o executor nunca teve — o
delta do gate diz "import não resolve", não diz qual era o nome certo.

**Preparar o projeto continua sendo passo manual.** Era um fluxo da skill
(`references/preparar-projeto.md`) e nenhum comando daqui o substituiu; a pendência
está em
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md).

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
| `[caminhos]` | backend, projeto de testes, `graph.json`, `prompts/`, onde vão os logs |
| `[openrouter]` | `base_url`, nome da variável de ambiente da chave, timeout |
| `[estagios.*]` | **modelo por estágio**, temperatura, modo de saída estruturada, tentativas de schema |
| `[gates.a]` / `[gates.b]` | `max_tentativas` de reparo de cada gate |
| `[execucao]` | executáveis (graphify, prettier, eslint, cypress) e limites |

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
gates leem os arquivos escritos em disco, os deltas são montados e reenviados. Ele
trabalha numa sandbox por execução (`.execucoes/<timestamp>/sandbox/`), então as
fixtures ficam limpas.

As fixtures exercitam o caminho feliz **e** um ciclo reprova → delta → reparo →
aprova no Gate A:

| Estágio | Tentativa 1 | Gate | Tentativa 2 |
| --- | --- | --- | --- |
| mapeador | sai sem o dossiê do recurso | Gate A reprova com `QAORQ-063` | dossiê emitido → aprova |
| executor | suíte completa | Gate B aprova de primeira | não é alcançada |

O ciclo do Gate B ficou sem gatilho no desacoplamento: quem reprovava a primeira
tentativa do executor era a reconciliação de cobertura dos `.mjs` (`QAAPI-002` e
`QAAPI-025`), e ela saiu. O roteiro
`fixtures/roteiros/pedidos/executor/tentativa-02.json` continua no lugar, e volta a
ser exercitado quando a checagem for reconstruída em Python.

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

⚠️ **Nunca chame `graphify extract` na mão.** Sem a flag `--code-only` que o Bloco 0
passa, ele faz extração semântica paga por LLM sobre o backend inteiro, sem avisar.
Chame sempre pelo Bloco 0.

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

O vocabulário é fechado e a tabela é **gerada** a partir de
`observabilidade/eventos.py`: veja
[`docs/referencia/eventos.md`](docs/referencia/eventos.md).

### `manifesto-execucao.json`

Responde ao chamado de suporte que o JSONL não responde: *"ontem passou, hoje
falhou"*. Ele registra o `run_id`, a versão do orquestrador e do Python, o commit e
o **estado sujo** dos repositórios do orquestrador e do backend quando são
checkouts Git, a versão do Graphify, a configuração **redigida**, os modelos
configurados por estágio e o hash de cada prompt e de cada artefato.

A versão do Graphify ocupa o lugar que era da impressão digital da skill: o que
precisa ser reproduzível é a ferramenta que leu o backend, e ela agora é
dependência declarada daqui — não um repositório de terceiro congelado por hash.

É escrito **duas vezes**: no início, para que uma execução que morra no meio ainda
deixe o cabeçalho do chamado; e no fim, com os hashes dos artefatos.

Três regras que o módulo não pode violar, e que
[`tests/test_observabilidade_manifesto_de_execucao.py`](tests/test_observabilidade_manifesto_de_execucao.py) fixa:

* **segredo nunca entra** — o *nome* da variável de ambiente da chave entra, o
  valor não, nem mascarado; além da redação por nome de campo, o texto final é
  varrido atrás do valor real da chave;
* **código-fonte nunca entra** — de arquivo sai hash, nunca conteúdo;
* **sonda que falha não derruba a execução** — backend que não é repositório Git
  ou `graphify` fora do PATH deixam o campo ausente **com o motivo** em
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

Cinco arquivos na raiz do pacote e nove subpacotes, cada um com um único motivo
dominante de mudança. A árvore anotada — que é **documentação executável**, e
reprova se divergir dos módulos reais — está em
[`docs/arquitetura/estrutura.md`](docs/arquitetura/estrutura.md).

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
comparação. Rejeitar aqui vira um delta de schema, o reparo mais barato que existe.

Essa rejeição era a primeira linha, não a única: o schema **ausente em disco** era
reprovado pelo `validar-suite-gerada.mjs`, com `QAAPI-027`, e essa segunda linha
saiu com a skill. O validador cruzado enxerga o que o mapeador declarou, não o que
sobreviveu à escrita.

O `Manifesto` espelha `_support/cobertura.json`, num formato que foi definido pela
skill e continua valendo porque é o que os prompts ensinam ao mapeador. Ele valida
apenas o que é estrutural: tipos, ids de categoria bem formados, endpoint na forma
canônica, ausência de campo desconhecido. A **contabilidade das 12 categorias**
ficava de fora porque quem a reprovava era o `validar-suite-gerada.mjs`
(princípio 4). Sem ele ninguém a reprova, e a resposta continua não sendo movê-la
para o Pydantic: contrato de dados não é gate, e um gate que mora no construtor do
artefato não tem como emitir violação reparável. Ver
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md).

### Saída estruturada é tratada como não confiável

Suporte a *structured output* e a *tool calling* varia muito entre modelos do
OpenRouter. O fluxo é sempre: pedir pelo mecanismo configurado
(`modo_estruturado`), validar com Pydantic aqui, e se falhar montar um `Delta` de
estágio `"schema"` e reenviar **só ele** — mini-loop praticamente grátis em
contexto, limitado por `max_tentativas_schema`. Ao estourar, o erro diz qual
recurso e qual estágio.

### Códigos de violação

Todo código que o fluxo emite hoje é `QAORQ-0xx`, do orquestrador, e a tabela é
gerada a partir de `gates/codigos.py`. Os `QAAPI-0xx` eram da skill e saíram com
ela; continuam listados porque delta e log de execução antiga ainda os nomeiam. As
duas famílias estão em
[`docs/referencia/codigos-de-violacao.md`](docs/referencia/codigos-de-violacao.md).

### A lacuna foi um gate, e voltou a ser buraco

`QAORQ-030` fechava o vão entre os dois scripts da skill. O
`validar-suite-gerada.mjs` provava **forma** — manifesto contabilizado, specs-base
presentes, imports resolvidos, campo do schema com teste `@campo` ou exceção. Ele
não conferia se cada categoria declarada em `cats` virou um `it`. Quem sabia disso
era o `qa-cobertura.mjs`, que classificava cada célula e contava as `lacunas` — só
que era relatório, rodava no Bloco 3 depois do loop, e saía com código 0 de
qualquer jeito.

O número aparecia na tela e ninguém agia sobre ele. Foi assim que uma execução real
gerou 50 testes, deixou `CAT-07` (regras de negócio) sem um único teste nos cinco
endpoints, e **passou** no Gate B. É o defeito "planejei e não entreguei" — o
mesmo que motivou o projeto — uma camada acima de onde os gates olhavam.

A correção foi trazer o `qa-cobertura.mjs` para **dentro** do Gate B, com
`lacunas > 0` reprovando. **Ela saiu junto com a skill em 2026-08-10**, e é a maior
perda do desacoplamento: nenhum gate responde hoje "planejei e não entreguei" sobre
o código gerado. Quem responde por cobertura é o `QAORQ-050/051/052`, no
planejador, e ele julga o **plano**, não os specs. A reconstrução em Python está
desenhada em
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md) — as tags
`@endpoint`/`@cat`/`@campo` já são lidas por `analise_estatica/tags_cypress.py`,
que hoje serve só para nomear o que faltou num delta, sem autoridade para reprovar.

**Autoridade e detalhe eram coisas diferentes**, e a distinção fica registrada
porque quem reconstruir a checagem vai reencontrá-la. Quem decidia se reprovava era
o contador do script, e o orquestrador nunca recalculava aquele número — mas "6
lacunas" não diz ao executor o que escrever, então o detalhe era reconstruído
cruzando o manifesto com as tags dos specs e **conferido contra o contador antes de
ser usado**. Contas divergentes descartavam a lista, e o delta saía só com o
número: um par endpoint×categoria errado faria o executor gastar tentativa
consertando o que não estava quebrado, e palpite com cara de precisão é pior que
número honesto. Pela mesma razão, spec com tag dinâmica (`@cat ${...}`, a forma
data-driven que a skill permitia) desligava o detalhe — o parser não resolve
template, e o que ele não resolve pareceria lacuna.

---

## Testes

```bash
python -m pytest
```

Roda de qualquer diretório de trabalho (o pacote é instalado, não achado por
`sys.path`).

Cobrem: parsing dos gates (incluindo stdout×stderr e exit 2), montagem do delta,
confinamento de caminho, o loop de reparo, os contratos Pydantic, a etapa de
formatadores do Gate B e o dry-run ponta a ponta. Os `integration` e os `e2e` rodam
na CI desde o desacoplamento: o único pré-requisito que sobrou é o `uv`, e um caso
que pule ali reprova o job.

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
