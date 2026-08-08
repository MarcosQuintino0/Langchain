# Plano de execução

Consolidação das duas revisões externas ([revisao-arquitetura.md](historico/revisao-arquitetura.md)
e [revisao-organizacao.md](historico/revisao-organizacao.md)), filtrada pelo que faz sentido
para este projeto e ordenada por etapas.

## Como ler este documento

**Critério de ordenação.** Da mais importante para a menos importante, nesta lógica:

1. O que pode **destruir dado do cliente** ou **declarar sucesso falso**. É irreversível e
   invalida a promessa central do produto.
2. O que torna a **prova determinística** real. É a razão de o projeto existir.
3. O **piso de engenharia** que impede regressão — e a organização que faz o código ser
   encontrável.
4. **Reprodutibilidade** e cobertura das fronteiras.
5. O que transforma um checkout em **produto instalável**.
6. **Refatorações caras** de estrutura interna.
7. **Expansão** de mercado.

Tamanho de arquivo e elegância não entram no critério. Um repositório bem organizado que
apaga o teste de um cliente continua sendo um produto quebrado.

**Marcação de evidência.** Toda citação marcada com ✅ foi verificada por mim abrindo o
arquivo. A primeira revisão publicou três caminhos inexistentes; nada aqui entrou sem
conferência.

**Esforço.** `[P]` pequeno (até meio dia), `[M]` médio (1–3 dias), `[G]` grande (mais que isso).

---

# Etapa 1 — Fechar falso sucesso e perda de dado

> **Por que primeiro.** Hoje o pipeline pode terminar com `OK` tendo aprovado sem evidência,
> e pode sobrescrever arquivo do cliente sem possibilidade de volta. Enquanto isso for
> verdade, todo o resto é otimização de um sistema que não pode ser usado por terceiros.

## 1.1 — O Bloco 0 falha aberto `[P]`

**Problema.** O resultado da preparação é calculado e **descartado**. Se o `graph.json`
estiver ausente, corrompido ou com drift, o pipeline segue e o mapeador explora um grafo
inválido.

**Evidência.** ✅ `pipeline.py:561` — `self.bloco0()`
sem atribuição. O método monta um `ResultadoPreparacao` com campo `ok` que ninguém lê.

**Correção.** `resultado = self.bloco0()`; se `not resultado.ok`, levantar erro operacional
tipado antes de qualquer chamada de modelo. Grafo inválido é pré-condição do ambiente, como
já é o projeto de testes não preparado.

**Validação.** Teste que injeta `ResultadoPreparacao(ok=False)` e exige que nenhum modelo
seja chamado.

## 1.2 — `ResultadoGate.combinar` aprova por ausência de violação `[P]`

**Problema.** Um filho com `aprovado=False` e lista de violações vazia vira resultado
aprovado. Basta uma checagem reprovar sem conseguir descrever o motivo para o gate passar.

**Evidência.** ✅ `contratos.py:125` —
`aprovado=not violacoes`.

**Correção.** Exigir que **todos** os filhos estejam aprovados **e** que não haja violações.
Adicionar validador no modelo proibindo `aprovado=True` com violações.

**Validação.** Teste com filho `aprovado=False` e `violacoes=[]` exigindo reprovação.

## 1.3 — Três estados de gate, não dois `[M]`

**Problema.** Falha de ferramenta é tratada como aprovação. Indisponibilidade não é sucesso
degradado, e também não é violação para o LLM reparar — mandar o modelo "consertar" um
script que não rodou queima tentativa sem chance de convergir.

**Evidência.** ✅ `gates/cobertura.py:64-82` —
aprova quando o `qa-cobertura.mjs` não devolve contadores. **Este código é meu, de hoje.** Eu
tratei o problema como binário (aprovar ou reprovar) quando existe um terceiro estado. O
revisor está certo.

Também em `gates/parser.py`: confia no campo `valid` do
JSON mesmo quando o código de saída é incompatível, exceto no caso particular de exit 2.

**Correção.** Introduzir `VereditoDeGate` com `APROVADO`, `REPROVADO` e `ERRO_DA_FERRAMENTA`.
Erro operacional interrompe o recurso com mensagem acionável e **nunca** entra em
`delta.violacoes`. Exigir as combinações documentadas — exit 0 com `valid=true`, exit 1 com
`valid=false`; qualquer outra é quebra de contrato.

**Validação.** Reescrever `test_gates_lacunas.py::test_sem_contadores_avisa_em_vez_de_reprovar`,
que hoje **fixa o comportamento errado**. Cobrir cada combinação inválida de exit/JSON.

## 1.4 — Falha do Cypress não reprova, e relatório velho é aceito `[M]`

**Problema.** O código de saída é registrado e ignorado. Um `report.json` de uma execução
anterior é aceito como se fosse desta.

**Evidência.** ✅ `pipeline.py:394-397` — evento com
`codigo=saida.codigo`, seguido de `candidato.is_file()` sem verificar procedência.

**Correção.** Caminho de relatório único por execução, garantir inexistência antes de rodar,
validar após. Exit não-zero vira falha tipada. Quando o Cypress não roda, o resultado diz
`NAO_EXECUTADO` — nunca algo equivalente a "validado em runtime".

## 1.5 — Comparação textual de caminho `[P]`

**Problema.** `startswith` aceita diretório irmão com prefixo comum. `.../products-old` passa
na verificação de `.../products`.

**Evidência.** ✅ `executor.py:117` —
`str(destino).startswith(str(raiz))`.

**Correção.** `Path.resolve()` nos dois lados e `Path.is_relative_to()`. Centralizar em
`ferramentas/arquivos.py`, que já é o dono do confinamento.

**Validação.** Teste com irmão de prefixo comum, diferença de caixa e junction do Windows.

## 1.6 — `NomeDeRecurso` como tipo, não string livre `[P]`

**Problema.** O nome do recurso vem cru da CLI e é concatenado em caminhos. `..`,
separadores, caminho absoluto e nomes reservados do Windows (`CON`, `NUL`, `COM1`) escapam.

**Evidência.** ✅ `contratos.py` — `Recurso.nome: str` sem
validador. ✅ `cli.py:32-40` — nomes crus.

**Correção.** Tipo restrito a slug: `^[a-z0-9][a-z0-9._-]*$`, sem `.`/`..`, sem ponto ou
espaço final, com denylist dos dispositivos reservados.

## 1.7 — A chave do LLM vaza para todo subprocesso `[P]`

**Problema.** `subprocess.run` é chamado sem `env=`, então **todo** o ambiente do processo pai
é herdado. `OPENROUTER_API_KEY` chega ao Node, ao Cypress, ao prettier e ao eslint — que
rodam código do projeto do cliente.

**Evidência.** ✅ `processo.py:86-96` — sem
parâmetro `env`.

**Correção.** Ambiente mínimo explícito: `PATH`, `SystemRoot`, `TEMP` e uma allowlist
declarada. A chave do provedor e variáveis de tracing são removidas. Variáveis do Cypress
entram por allowlist separada.

**Validação.** Teste que executa um processo que imprime o ambiente e verifica ausência da
chave.

## 1.8 — `--auditor` mente `[P]`

**Problema.** O comando encerra com código 0 anunciando um veredito, mas o auditor é stub.
Em CI, isso é indistinguível de auditoria feita.

**Evidência.** ✅ `cli.py:169-178` — `return 0` depois de
imprimir a descrição do stub.

**Correção.** Enquanto não existir implementação, retornar código diferente de zero com
mensagem de indisponibilidade. O auditor continua **fora do loop quente**.

## 1.9 — Escrita destrutiva: desligar antes de consertar `[P]`

**Problema.** `--remover-reprovados` chama `unlink()` em toda a lista de reprovados. A
proteção que construímos hoje cobre schemas preexistentes, mas os specs continuam sendo
sobrescritos direto no destino final, sem staging e sem rollback.

**Evidência.** ✅ `cli.py:89-103`. ✅
`executor.py:110-122` — grava direto no
diretório do recurso.

**Correção nesta etapa.** Desabilitar `--remover-reprovados` com mensagem explicando que
volta quando houver diário de propriedade. O staging completo é a Etapa 2.

## 1.10 — Donos canônicos ocultos `[P]`

**Problema.** Módulos-folha reexportam símbolos de outros módulos, criando mais de uma
resposta válida para "de onde importo isso?". Num projeto escrito por IA, o agente importa de
onde encontrou primeiro, e a estrutura derrete em silêncio.

**Evidência.** ✅ `processo.py:23-32`
reexporta `ErroDeFerramenta` e `ExecutavelAusente`. ✅
`gates/parser.py:12-23` reexporta `ErroDeInvocacao` e
`extrair_json`. ✅ `superficie.py:31-33`
reexporta `extrair_exports`. ✅ `ErroDeConfiguracao` mora em
`config.py:25`, embora `excecoes.py` declare concentrar a
taxonomia.

**Correção.** Remover os reexports; consumidores importam do dono. Mover
`ErroDeConfiguracao` para `excecoes.py`. Criar `gates/codigos.py` com o catálogo — que hoje
vive no `__init__.py` e ✅ **não contém `QAORQ-030`**, o código que eu adicionei hoje.

**Por que já nesta etapa.** É pré-requisito de tudo na Etapa 3 e custa pouco.

## 1.11 — Correções pontuais de baixo custo `[P]`

- ✅ `mapeador.py:298` manda o usuário rodar
  `pip install -r requirements.txt`. **Esse arquivo não existe.** Trocar por
  `pip install -e ".[dev]"`, que é o que o README documenta.
- ✅ `CATS_SET` e `ESTADOS_DE_EXCECAO` em `contratos.py` não têm consumidor. Remover.
- ✅ `.coverage` não está no `.gitignore` (arquivo que eu gerei hoje). Acrescentar
  `.coverage*` e `htmlcov/`.
- ✅ O catálogo de eventos do README omite `execucao_abortada`
  (`cli.py:199`) e `cypress`.

**Critério de saída da Etapa 1.** Nenhum erro, evidência ausente ou arquivo residual produz
"aprovado". Uma interrupção no meio preserva os arquivos preexistentes byte a byte. A chave
do provedor não sai do processo Python. Existe um teste de regressão por item.

---

# Etapa 2 — Persistência transacional e reparo honesto

> **Por que segundo.** Resolvida a mentira, resta a fragilidade: o que o gate valida não é
> exatamente o que o próximo estágio recebe, e o reparo trabalha com informação incompleta.

## 2.1 — Staging, diário de propriedade e publicação atômica `[G]`

**Problema.** Artefatos são escritos no destino final antes da aprovação. Não há como
distinguir arquivo criado por nós de arquivo preexistente, nem desfazer uma tentativa ruim.

**Correção.**

1. Cada recurso é gerado numa área de staging da execução.
2. Validação roda no staging.
3. Publicação por substituição atômica, só depois da aprovação.
4. Diário por arquivo: caminho, hash anterior, hash novo, classificação
   `criado` / `modificado` / `preexistente`.
5. Rollback em conflito ou falha.
6. Spec obsoleto de execução anterior só é removido se constar no diário **e** o hash não
   tiver mudado desde então.
7. Com o diário no lugar, `--remover-reprovados` volta a existir — restrito a `criado`.

**Por que é a mais importante da etapa.** É o único item de todo o plano que pode destruir
trabalho de quem pagou pela ferramenta.

## 2.2 — O reparo do mapeador manda o artefato incompleto `[M]`

**Problema.** No reparo, o mapeador recebe **só o manifesto**. Inventário e schemas ficam de
fora. Uma violação sobre schema não pode ser corrigida sem o schema à vista.

**Evidência.** ✅ `pipeline.py:264` — o
`texto_do_artefato` do Bloco 1 usa apenas `saida.manifesto.para_json()`, embora
`SaidaMapeador` tenha inventário e schemas.

**Correção.** Bundle canônico com os três, lido do disco.

**Isto não viola o princípio 2.** A fórmula continua
`instrução_fixa + artefato_atual + delta.violacoes`. O que muda é que "artefato atual" passa a
significar o artefato inteiro, não uma fatia arbitrária dele. Nada de histórico entra.

## 2.3 — O truncamento do executor é posicional `[M]`

**Problema.** O artefato atual do executor é a concatenação dos arquivos, cortada em 60 mil
caracteres. Em suíte grande, o trecho que o gate reclamou pode estar depois do corte — e o
loop repete a tentativa sem chance de corrigir.

**Evidência.** ✅ `executor.py:125-143`.

**Correção.** Projetar deterministicamente **apenas** os arquivos e trechos apontados por
`delta.violacoes`, com contexto de linhas calculado por script. Fica menor e mais relevante ao
mesmo tempo.

## 2.4 — Schema divergente precisa de um terceiro estado `[P]`

**Problema.** Quando preservamos um schema do cliente e o mapeador encontrou campos que ele
não declara, sai um aviso — e o recurso pode terminar como sucesso completo.

**Evidência.** ✅ `pipeline.py:288-328` — o
`_avisar_divergencia` que escrevi hoje.

**Correção.** Manter a preservação e produzir um diff legível por máquina. O recurso encerra
como `REQUER_REVISAO`, distinto de aprovado e de reprovado. **Nunca** atualizar schema
existente para fazer teste passar.

## 2.5 — Fixar a versão da skill `[P]`

**Problema.** A validação confere se três arquivos `.mjs` existem. Não há versão suportada,
nem negociação de capacidade.

E há um problema que **nenhuma das duas revisões viu**: os `prompts/*.md` são condensação
**manual** dos `references/*.md` da skill. Se a skill mudar uma regra, nada avisa o
orquestrador. São duas fontes de verdade, uma copiando a outra, sem detecção de divergência.

**Correção.** Declarar versão/tag suportada da skill em `config.toml` e recusar rodar fora
dela — o mesmo padrão que o `qa-reindex.mjs` já usa para o Graphify, e que nos custou um erro
hoje. Registrar no cabeçalho de cada prompt a versão da skill de que foi destilado.

**Critério de saída da Etapa 2.** Uma falha no meio de qualquer estágio deixa o projeto do
cliente exatamente como estava. O reparo recebe o artefato completo e relevante. Divergência
de contrato não termina como sucesso.

---

# Etapa 3 — Piso de engenharia e organização

> **Por que terceiro.** É o que impede as etapas anteriores de regredirem. Com IA escrevendo o
> código, convenção sem verificação automática é decoração.

## 3.1 — Ferramental `[M]`

Cadeia pequena e ortogonal. Uma ferramenta por responsabilidade.

| Ferramenta | Configuração | Observação |
| --- | --- | --- |
| **uv + `uv.lock`** | `[dependency-groups] dev`; CI com `uv lock --check` e `uv sync --frozen` | Lock versionado |
| **Ruff lint** | `select = ["E4","E7","E9","F","I","UP","B","C4","PIE","RUF","S","PTH","ARG","TID252","PLC0415"]` | `tests/**` ignora só `S101` |
| **Ruff format** | `line-ending = "lf"` | Substitui Black e isort |
| **Pyright** | `typeCheckingMode="standard"`, `strict=["src"]`, `reportUnnecessaryTypeIgnoreComment="error"` | Sem baseline de ignores |
| **pytest-cov** | `branch=true`, `fail_under=85` | ✅ medi: **86% hoje**. O piso passa |
| **Markers** | `unit`, `integration`, `e2e` + `--strict-markers` | Integração pulada em silêncio é o risco |
| **pre-commit** | Só whitespace/EOF/TOML + `ruff check --fix` e `ruff format` | Nunca pytest nem Pyright no hook |
| **`uv audit`** | `--frozen` em PR e semanal | Dispensa `pip-audit` |
| **Scanner de segredo** | Gitleaks ou secret scanning do host | Lê repositório de cliente |

**Regras que eu explicitamente não habilito:**

- `N818` — exige que exceção termine em `Error`. Quebraria a taxonomia `ErroDe...` /
  `FalhaDe...`, que é deliberada e em português.
- `D101` / `D103` — geram docstring burocrática vazia quando quem escreve é IA.
- `N815` sobre modelos que espelham JSON externo — os aliases `naoAplica` e `schemaEntrada`
  são o contrato da skill, não desleixo.
- Limite automático de complexidade ou de linhas — dividiria `config.py` e
  `ferramentas/arquivos.py`, que são coesos.

**Divergência da revisão:** ela sugere CI em Python 3.12 **e** 3.13. Eu ficaria só em
**3.13**. Não há usuário em 3.12; suportar dois dobra o tempo de CI sem retorno. Reavaliar se
aparecer demanda real.

## 3.2 — Teste de estrutura: o item que faz tudo o resto grudar `[M]` — **CONCLUÍDO**

Sem isto, todas as convenções abaixo são texto que um agente futuro lê pela metade.

`tests/test_estrutura_do_codigo.py` existe e cobre as sete checagens. Duas ressalvas sobre o
que foi entregue, ambas deliberadas: a lista fechada da raiz aceita os nove arquivos de hoje
(item 1 pedia seis — os outros três dependem da Etapa 6), e a árvore do README é **conferida**
contra o código, não gerada a partir dele (item 6 admitia as duas formas; a descrição de uma
linha por módulo é editorial e nenhum gerador a produz).

Criar `tests/test_estrutura_do_codigo.py` com verificação por AST:

1. A raiz de `src/orquestrador/` contém **exatamente** `__init__.py`, `__main__.py`,
   `cli.py`, `config.py`, `excecoes.py`, `raiz.py`. Lista fechada.
2. Nenhum `__init__.py` reexporta nome importado.
3. Nenhum módulo declara em `__all__` um símbolo que não define.
4. Direção de dependência: `dominio` não importa aplicação, agentes ou gates;
   `analise_estatica` não importa orquestração; `ferramentas` não importa agentes.
5. Todo literal `QAORQ-\d{3}` em `src/` está registrado no catálogo.
6. Todo módulo de produção aparece na árvore do README — ou, melhor, a árvore é **gerada** a
   partir do código.
7. Nenhum teste faz `from conftest import ...`.

O item 6 tem causa concreta: ✅ a árvore do README lista `montagem.py` e `llm/cliente.py` mas
**omite** `superficie.py` e `javascript.py`. Foi essa omissão que fez o primeiro revisor
procurar os arquivos no lugar errado.

## 3.3 — Organização de pastas

### Estrutura-alvo

```
src/orquestrador/
├── __init__.py
├── __main__.py
├── cli.py                          composição da execução e apresentação
├── config.py                       carga e validação da configuração
├── excecoes.py                     + ErroDeConfiguracao        ⬅ de config.py
├── raiz.py                         único uso de Path(__file__)
│                                   ── lista FECHADA: 6 arquivos (hoje 12)
├── agentes/                        cria artefato; não decide aprovação
│   ├── auditor.py
│   ├── executor.py
│   ├── mapeador.py
│   └── ferramentas_do_mapeador.py  ★ Etapa 6 ⬅ mapeador.py:61-274
│
├── gates/                          reprova determinística; sem LLM
│   ├── __init__.py                 só docstring
│   ├── codigos.py                  ★ Etapa 1 — catálogo + QAORQ-030
│   ├── gate_a.py
│   ├── gate_b.py
│   ├── lacunas.py                  ⬅ cobertura.py  (evita colidir com a classe Cobertura)
│   └── saidas.py                   ⬅ parser.py     ("parser" não diz de quê)
│
├── analise_estatica/               ★ lê código-fonte sem executar
│   ├── exports_javascript.py       ⬅ javascript.py:59-249
│   ├── tags_cypress.py             ⬅ javascript.py:265-294
│   └── extrator_de_superficie.py   ⬅ ferramentas/superficie.py
│
├── ferramentas/                    adaptador de filesystem, subprocesso e CLI externa
│   ├── arquivos.py
│   ├── graphify.py
│   ├── json_externo.py             ⬅ textos.py
│   ├── processo.py
│   └── scripts_qa.py
│
├── llm/                            cliente, mensagens, saída estruturada, prompt
│   ├── cliente.py
│   ├── estruturado.py
│   ├── mensagens.py
│   └── montagem.py                 ⬅ montagem.py (raiz)
│
├── observabilidade/                eventos e métricas; nunca decide fluxo
│   ├── eventos.py                  ★ Etapa 4 — TipoDeEvento (StrEnum)
│   ├── modelos.py                  ★ Etapa 6 ⬅ contratos.py:621-689
│   ├── registro.py                 + dados_para_log
│   ├── tabelas.py                  ★ tabelas Rich saem da telemetria
│   └── telemetria.py               só agregação
│
├── dominio/                        ★ Etapa 6 — contratos puros, sem I/O
│   ├── gates.py                    Violacao, ResultadoGate, Delta
│   ├── cobertura.py                categorias, Recurso, Inventario, Manifesto
│   ├── artefatos.py                caminhos de schema, saídas dos estágios
│   ├── superficie.py               exports, módulos, SuperficieDoProjeto
│   └── auditoria.py                achados e resultado do auditor
│
└── aplicacao/                      ★ Etapa 6 — coordena estágios e persistência
    ├── pipeline.py                 ⬅ pipeline.py, reduzido a ~320 linhas
    ├── ciclo_de_reparo.py          ⬅ pipeline.py:420-556
    ├── persistencia.py             ⬅ pipeline.py:267-328 + executor.py:110-143
    └── simulacao.py                ⬅ simulacao.py
```

**Minha correção ao plano do revisor.** Ele propôs `dominio/superficie.py` **e**
`analise_estatica/superficie.py` — dois arquivos com o mesmo nome no mesmo pacote, que é
exatamente o problema de descobribilidade que o plano quer resolver. Renomeei o segundo para
`extrator_de_superficie.py`: um é o contrato, o outro é quem o produz.

**Custo que a revisão não menciona.** A árvore completa leva o pacote de 34 para ~49 arquivos,
crescimento de ~45%. Cada arquivo ganha um dono, o que é bom, mas é custo real de navegação.
É parte da razão de eu empurrar `dominio/` e `aplicacao/` para a Etapa 6.

### O que fazer nesta etapa `[M]` — **CONCLUÍDO**

Só a parte barata — `git mv` mais atualização de import, um commit por movimento, suíte verde
entre eles:

| # | Movimento | Custo | Estado |
| --- | --- | --- | --- |
| 1 | `montagem.py` → `llm/montagem.py` | P | ✅ feito |
| 2 | `textos.py` → `ferramentas/json_externo.py` | P | ✅ feito |
| 3 | `gates/parser.py` → `gates/saidas.py` | P | ✅ feito |
| 4 | `gates/cobertura.py` → `gates/lacunas.py` | P | ✅ feito |
| 5 | Criar `analise_estatica/` com os três módulos | M | ✅ feito |
| 6 | Tabelas Rich → `observabilidade/tabelas.py` | P | ✅ feito |

Isso já entrega: raiz com lista fechada e verificável, `javascript.py` e `superficie.py` num
lugar que se acha pelo nome, e o fim dos nomes genéricos `parser` e `textos`.

**Sem fachada de compatibilidade** nesses movimentos: são imports internos, e manter duas
rotas de import anula a padronização.

**O que ficou fora, e continua nesta seção como pendência.** Tudo marcado ★ na
estrutura-alvo acima: `agentes/ferramentas_do_mapeador.py`, `observabilidade/eventos.py`
(Etapa 4.3), `observabilidade/modelos.py`, e os pacotes `dominio/` e `aplicacao/` inteiros —
Etapa 6. A raiz do pacote, portanto, ainda tem nove arquivos e não seis: `contratos.py`,
`pipeline.py` e `simulacao.py` só saem quando aqueles dois pacotes existirem. A lista fechada
de `tests/test_estrutura_do_codigo.py` reflete os nove de hoje e é para **encolher** conforme
a Etapa 6 avança, nunca crescer.

## 3.4 — Configuração que não pode voltar a ficar inválida `[P]`

**Problema.** Limites aceitam zero e negativo. A CLI muta um modelo já validado usando
truthiness, então `--max-tentativas 0` é silenciosamente ignorado e um valor negativo passa.

**Evidência.** ✅ `cli.py:138-140` —
`if args.max_tentativas`.

**Correção.** `PositiveInt` e `NonNegativeFloat` com máximos sensatos; URL do provedor com
tipo de URL; `validate_assignment=True` e override por `model_copy(update=...)` revalidado.

## 3.5 — Tipagem nas fronteiras `[M]`

`Any` é legítimo na borda de JSON de terceiro, desde que convertido logo para DTO. O problema
é onde ele atravessa o domínio: modelo, registrador, callbacks e estado do LangGraph.

Usar `BaseChatModel`, um `Protocol` para o registrador, `TypeVar` em `_ciclo`, `TypedDict`
para o estado, e modelos Pydantic discriminados para os passos de simulação.

## 3.6 — `AGENTS.md` e `.claude/` `[P]`

**Divisão.** `AGENTS.md` na raiz é a fonte canônica. `.claude/CLAUDE.md` importa com
`@../AGENTS.md` e contém apenas o que é específico do Claude Code. Nada de
`.claude/rules/` repetindo invariante — duas fontes de verdade divergem.

O conteúdo proposto pela revisão é bom. Eu cortaria o que já está no README e acrescentaria a
**regra de decisão de onde colocar código novo**, que é o que falta hoje:

> Escolha o diretório pelo único motivo dominante de mudança:
> `dominio/` contratos e regras puras, sem I/O · `aplicacao/` coordena estágios e persistência ·
> `agentes/` monta e invoca um criador LLM · `gates/` reprova determinística ·
> `analise_estatica/` lê código-fonte sem executar · `ferramentas/` adaptador de I/O externo ·
> `llm/` cliente e prompt · `observabilidade/` eventos e métricas ·
> raiz apenas `cli.py`, `config.py`, `excecoes.py`, `raiz.py`, `__init__.py`, `__main__.py`.
>
> Não crie `utils.py`, `helpers.py`, `common.py` nem `models.py`. Se um código couber em dois
> donos, a fronteira não está clara: extraia a parte pura para o dono inferior.

**Corrigir a skill de push antes de qualquer outra coisa aqui.** ✅
`.claude/skills/push/SKILL.md:33` usa `git add -A`. Trabalhamos no mesmo working tree — isso
faz um agente commitar o trabalho do outro. Trocar por stage explícito de caminhos.

**Critério de saída da Etapa 3.** PR não integra com lock divergente, lint ou tipo falho,
cobertura abaixo do piso, integração pulada, ou violação da regra de estrutura.

---

# Etapa 4 — Reprodutibilidade e fronteiras

## 4.1 — Manifesto de execução `[M]`

Hoje não é possível reproduzir um chamado de suporte. Criar `manifesto-execucao.json` com:
`schema_version`, `run_id`, versão do orquestrador, Python e Node, commit e estado sujo dos
dois repositórios, versão da skill, configuração redigida, hash dos prompts e dos artefatos,
modelos configurados, provedor e rota efetivos, request IDs, tokens e custo.

## 4.2 — Taxonomia de erro de provedor `[M]`

`ErroDeProvedor` com categorias: autenticação, rate limit, timeout, transitório, contrato
inválido. Erro operacional nunca vira `delta.violacoes`. Retries centralizados no orquestrador,
com tentativa, request ID, espera e custo registrados — hoje o cliente faz retry invisível para
a telemetria. Códigos de saída distintos para configuração, ferramenta, gate e provedor.

**A cobertura corrobora.** ✅ Medi: `llm/cliente.py` **23%**, `llm/estruturado.py` **61%**,
`llm/mensagens.py` **71%**. A fronteira com o provedor é a menos testada do projeto — e é
justamente onde não há taxonomia.

## 4.3 — Enum de eventos `[P]`

`TipoDeEvento(StrEnum)` em `observabilidade/eventos.py`. Toda emissão usa membro do enum, e a
documentação é gerada a partir dele. ✅ Hoje o catálogo do README já omite `execucao_abortada`
e `cypress`.

## 4.4 — Testes de fronteira `[G]`

O que a suíte atual não cobre: quebra de contrato da skill, wheel incompleto, travessia de
caminho, relatório Cypress velho, perda de arquivo, 401/429/5xx/timeout do provedor,
vazamento de segredo.

Separar `unit`, `integration` e `e2e`; job Windows obrigatório com Node 24 e referência exata
da skill, falhando se houver skip inesperado.

## 4.5 — Reorganizar os testes `[M]`

Um arquivo unitário por módulo canônico; `test_invariante_*` quando cruzar dois ou mais
módulos; `test_integracao_*` para subprocesso real. Fábricas no `conftest.py` como fixture, não
como import. Renomear `test_dry_run.py` → `test_integracao_dry_run.py` e `test_principio_2.py`
→ `test_invariante_principio_2.py`.

**Não** criar `tests/unitarios/` e `tests/integracao/`: com 15 arquivos, nome e marker
resolvem com menos navegação.

---

# Etapa 5 — Produto

## 5.1 — O wheel não contém o que a execução procura `[M]`

✅ `pyproject.toml` exclui `prompts/`, `fixtures/` e `config.toml` do pacote, e
`raiz.py` os procura a partir da árvore-fonte. Um `pip install` do wheel instala e falha no
primeiro comando — o que inviabiliza "instala, fornece a chave e aponta os repositórios".

Empacotar prompts como package-data e acessar por `importlib.resources`. Criar
`orquestrador init` e `orquestrador doctor`. Toda release constrói o wheel e o testa em
ambiente limpo.

## 5.2 — Fronteira de privacidade `[G]`

Requisito de venda, não polimento. Nenhuma empresa aprova mandar o código-fonte dela para um
provedor sem política clara.

`PoliticaDePrivacidade` com allowlist de raízes e extensões, `.llmignore`, denylist forte
(`.env*`, PEM, credenciais) e scanner de segredo que bloqueia ou redige **antes** do envio.
Zero Data Retention e allowlist de provedor como configuração conservadora, sem fallback para
fora da política. Logs sem código-fonte por padrão.

## 5.3 — Orçamento antes, não depois `[M]`

Tetos duros por execução e por recurso: chamadas, tokens, bytes de ferramenta, tempo e moeda.
Estimativa em faixa antes de aplicar, interrompendo antes da chamada que estouraria o teto.
Registrar custo e rota efetiva.

**Barato de começar:** o Bloco 0 é determinístico e conta endpoints. Um `--estimar` que roda
só ele e devolve uma faixa custa pouco e é diferencial de venda.

## 5.4 — Matriz de suporte honesta `[P]`

Declarar Tier A (avaliado e suportado), Tier B (linguagem suportada com descoberta genérica) e
Tier C (experimental ou bloqueado). Hoje o suporte real é Cypress em JavaScript e backend
Spring/Java. ✅ O parser de superfície aceita só `.js/.mjs/.cjs` e é heurístico, não AST.

Fora da matriz, falhar com diagnóstico — nunca "universalizar" por prompt.

---

# Etapa 6 — Refatorações caras

> **Por que tarde.** São as de maior risco técnico — identidade de classe Pydantic, alvos de
> monkeypatch, `model_json_schema()` — e o ganho é organizacional, não funcional. Só depois
> que o produto não puder mais mentir nem destruir dado.

## 6.1 — Dividir `contratos.py` `[G]`

✅ 702 linhas com seis vocabulários independentes. Dividir por vocabulário conforme a árvore
da seção 3.3, com fachada temporária reexportando **as mesmas classes**, nunca cópias.

Risco: referências futuras, `$defs` e nomes qualificados no schema Pydantic, `isinstance`,
alvos de monkeypatch. Comparar snapshot de `model_json_schema()` antes e depois dos seis
contratos públicos, normalizando só ordenação.

## 6.2 — Dividir `pipeline.py` e criar `aplicacao/` `[G]`

✅ 606 linhas: orquestração, persistência, heurística de schema, Cypress e telemetria.
Extrair `CicloDeReparo` e `PersistenciaDeArtefatos`; o pipeline reduzido vai para
`aplicacao/pipeline.py` com ~320 linhas.

**Não** dividir `bloco0` a `bloco3` em módulos separados: eles formam a narrativa da
orquestração, e separá-los por número cria navegação sem independência.

## 6.3 — Reduzir os agentes `[M]`

✅ `mapeador.py` tem 469 linhas. Extrair modelos de argumento, wrappers observados e
construção das cinco tools para `agentes/ferramentas_do_mapeador.py`. A assinatura pública de
`executar` não muda.

## 6.4 — Diff grafo × manifesto `[G]`

O stub que fecha a promessa central. Depende de adaptadores por linguagem e de normalização
determinística de rota. Está aqui, e não na Etapa 1, porque exige a matriz de suporte da
Etapa 5 para ser honesto — mas o `QAORQ-001` deve **reprovar** em vez de avisar assim que a
primeira linguagem estiver implementada.

---

# Etapa 7 — Expansão

Um adaptador de framework por vez, com projeto-fixture real, golden de grafo e falsos
negativos conhecidos. Medir precisão do extrator e custo por porte de projeto. Só então
avaliar auditor semântico, paralelismo entre recursos e throughput.

O denominador determinístico vem preferencialmente do que a aplicação declara sobre si
(OpenAPI, actuator, `rails routes`), com AST como segunda opção e enumeração por LLM como
terceira — esta última **sempre** marcando o run como "inventário não verificado".

---

# Apêndice A — O que foi descartado

| Sugestão | Por que não |
| --- | --- |
| **Reidratar artefato do disco entre estágios** (A3) | A intenção do princípio 1 é "sem histórico de conversa", não serializar como cerimônia. O manifesto já passa pelo mesmo `para_json()` que o gate lê. O ganho real é retomada de execução, que não é requisito. Custo alto, benefício especulativo. Reavaliar quando retomada virar requisito. |
| **CI em Python 3.12 e 3.13** | Não há usuário em 3.12. Dobra o tempo de CI sem retorno. |
| **Hypothesis nas etapas iniciais** | Bom em espaço combinatório, mas os defeitos de hoje são categóricos, não combinatórios. Entra junto com o confinamento de caminho, se entrar. |
| **Infra de golden/snapshot cedo** | JSON canônico e pytest bastam no volume atual. |
| **`tests/unitarios/` e `tests/integracao/`** | 15 arquivos. Nome e marker resolvem com menos navegação. |
| **Black, isort, Flake8, Bandit, mypy, Poetry, PDM, tox, nox** | Duplicam Ruff, Pyright e uv. Uma ferramenta por responsabilidade. |
| **SonarQube, mutation testing, cobertura 100%** | Otimizar métrica antes de corrigir risco conhecido. Mutation testing é um experimento futuro interessante nos gates puros. |
| **`utils/`, `helpers/`, `common/`, `models.py`, `constantes.py`** | Escondem o motivo de mudança e recriam o depósito que se quer eliminar. |
| **Limite rígido de linhas por arquivo** | Dividiria `config.py` e `ferramentas/arquivos.py`, que são coesos. Usar 350 como pergunta em revisão, não como falha. |
| **Docker, banco, fila, Kubernetes** | Artefato local versionado atende. Não resolve o gargalo. |
| **Paralelizar recursos agora** | Antes é preciso isolamento de diretório e publicação transacional. Paralelismo precoce torna corrupção intermitente. |

# Apêndice B — Invariantes que nenhuma etapa pode quebrar

1. Handoff entre estágios é artefato em disco, nunca histórico de conversa.
2. Prompt de reparo é exatamente `instrução_fixa + artefato_atual + delta.violacoes`.
3. Agentes são stateless entre unidades de trabalho.
4. Quem reprova é script. Nenhum LLM decide se a cobertura está completa, e nenhum LLM valida
   a saída de outro LLM.
5. O auditor semântico fica fora do loop quente.
6. Nenhum nome de modelo em código ou prompt.
7. A skill `qa-api` é somente leitura. Nunca modificar, copiar para dentro nem versionar.

Se uma tarefa exigir contrariar uma destas, pare e exponha o conflito.
