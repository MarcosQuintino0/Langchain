# Revisão externa de organização e padronização

## 1. Veredito

A estrutura não é um depósito indiscriminado: `agentes/`, `gates/`, `ferramentas/`, `llm/` e `observabilidade/` têm identidades úteis.
A raiz do pacote, porém, mistura entrada pública, domínio, aplicação, parsing e simulação; falta uma regra de pertencimento que um agente consiga verificar.
O problema mais urgente não é tamanho de arquivo: são donos canônicos ocultos por reexports em módulos-folha.
`contratos.py` deve ser dividido por vocabulário; `pipeline.py`, por ciclo de reparo e persistência — sem fragmentar a orquestração restante.
Os nomes internos em português são, em geral, consistentes; termos externos e aliases JSON são variação legítima, não dívida.
Os testes misturam arquivo por módulo, invariante e integração e repetem fábricas de objetos, mas ainda não justificam uma árvore profunda.
Os três erros da revisão anterior não provam a mesma coisa: dois caminhos constavam no README; a omissão de `ferramentas/superficie.py` expõe documentação estrutural incompleta.
A correção durável é pequena: teste AST de estrutura, Ruff/Pyright com regras precisas e migrações de um dono por vez, sempre preservando os 178 testes.

## 2. Mapa atual

As contagens abaixo são de linhas físicas, incluindo docstrings e comentários, verificadas no estado atual do disco. “Move” inclui renomeação dentro do mesmo pacote; “divide e move” indica que uma parte fica e outra ganha dono novo.

| Módulo | Responsabilidade atual, em uma linha | Linhas | Veredito |
|---|---|---:|---|
| `src/orquestrador/__init__.py:1` | Declara o pacote, sem API agregada. | 1 | fica |
| `src/orquestrador/__main__.py:1-8` | Encaminha `python -m orquestrador` para a CLI. | 8 | fica |
| `src/orquestrador/agentes/__init__.py:1` | Declara o pacote de criadores LLM. | 1 | fica |
| `src/orquestrador/agentes/auditor.py:1-69` | Mantém o contrato do auditor semântico ainda fora do loop quente. | 69 | fica |
| `src/orquestrador/agentes/executor.py:1-143` | Monta/invoca o executor e também persiste e relê seus arquivos. | 143 | divide |
| `src/orquestrador/agentes/mapeador.py:1-469` | Define ferramentas, cria o agente, monta prompts, executa e interpreta sua saída. | 469 | divide |
| `src/orquestrador/cli.py:1-243` | Faz parsing da linha de comando, composição da execução e apresentação final. | 243 | fica |
| `src/orquestrador/config.py:1-258` | Modela, carrega e valida a configuração TOML. | 258 | fica |
| `src/orquestrador/contratos.py:1-702` | Reúne seis vocabulários independentes: gates, cobertura, artefatos, superfície, auditoria e telemetria. | 702 | divide |
| `src/orquestrador/excecoes.py:1-78` | Define a taxonomia de falhas do orquestrador. | 78 | fica |
| `src/orquestrador/ferramentas/__init__.py:1` | Declara o pacote de adaptadores determinísticos. | 1 | fica |
| `src/orquestrador/ferramentas/arquivos.py:1-251` | Centraliza confinamento, leitura, escrita e listagem de arquivos. | 251 | fica |
| `src/orquestrador/ferramentas/graphify.py:1-168` | Adapta o CLI Graphify da skill externa. | 168 | fica |
| `src/orquestrador/ferramentas/processo.py:1-127` | Executa subprocessos de forma uniforme no Windows. | 127 | fica |
| `src/orquestrador/ferramentas/scripts_qa.py:1-73` | Adapta os scripts `.mjs` da skill `qa-api`. | 73 | fica |
| `src/orquestrador/ferramentas/superficie.py:1-117` | Descobre módulos JS, extrai exports e calcula imports; é análise estática com I/O, não ferramenta externa. | 117 | move |
| `src/orquestrador/gates/__init__.py:1-16` | Declara o pacote e contém um catálogo incompleto de códigos. | 16 | divide |
| `src/orquestrador/gates/cobertura.py:1-169` | Compara tags Cypress com o manifesto e produz lacunas de cobertura. | 169 | move |
| `src/orquestrador/gates/gate_a.py:1-89` | Compõe as verificações determinísticas do manifesto. | 89 | fica |
| `src/orquestrador/gates/gate_b.py:1-100` | Compõe validação da suíte, formatação e lint. | 100 | fica |
| `src/orquestrador/gates/parser.py:1-99` | Converte saídas JSON dos scripts em contratos internos. | 99 | move |
| `src/orquestrador/javascript.py:1-294` | Contém dois parsers puros diferentes: exports/declarações e tags de specs. | 294 | divide e move |
| `src/orquestrador/llm/__init__.py:1` | Declara o pacote de integração com modelos. | 1 | fica |
| `src/orquestrador/llm/cliente.py:1-41` | Instancia o cliente OpenRouter por estágio. | 41 | fica |
| `src/orquestrador/llm/estruturado.py:1-197` | Invoca modelos com saída estruturada e normaliza falhas. | 197 | fica |
| `src/orquestrador/llm/mensagens.py:1-67` | Extrai texto, chamadas de ferramenta e uso de mensagens LangChain. | 67 | fica |
| `src/orquestrador/montagem.py:1-83` | Monta os prompts dos estágios e o prompt mínimo de reparo. | 83 | move |
| `src/orquestrador/observabilidade/__init__.py:1` | Declara o pacote de observabilidade. | 1 | fica |
| `src/orquestrador/observabilidade/registro.py:1-98` | Grava eventos JSONL e converte seus dados serializáveis. | 98 | fica |
| `src/orquestrador/observabilidade/telemetria.py:1-267` | Agrega métricas e também renderiza quatro tabelas Rich. | 267 | divide |
| `src/orquestrador/pipeline.py:1-606` | Orquestra blocos e recursos, mas também implementa o ciclo de reparo e persistência. | 606 | divide e move |
| `src/orquestrador/raiz.py:1-40` | Resolve a raiz do projeto e seus caminhos padrão. | 40 | fica |
| `src/orquestrador/simulacao.py:1-188` | Implementa o modelo roteirizado e a sandbox do `--dry-run`. | 188 | move |
| `src/orquestrador/textos.py:1-53` | Extrai JSON de texto de ferramentas externas. | 53 | move |

### Leitura do mapa

A raiz deve ter uma lista fechada, não uma heurística: `__init__.py`, `__main__.py`, `cli.py`, `config.py`, `excecoes.py` e `raiz.py`. São entrada pública ou infraestrutura transversal. Os demais módulos da raiz têm um motivo de mudança mais específico e, por isso, um dono melhor.

O erro de localização na revisão anterior precisa ser interpretado com rigor. `README.md:316-346` lista `montagem.py` em `README.md:321` e `llm/cliente.py` em `README.md:327`; nesses dois casos o revisor não verificou o disco. Já `javascript.py` e `ferramentas/superficie.py` existem, mas não aparecem nessa árvore manual. A omissão de `superficie.py` torna plausível procurá-lo no lugar errado e demonstra que uma árvore escrita à mão não pode ser a fonte de verdade. Isso é uma continuação organizacional de A18, não um novo problema arquitetural.

Os arquivos de 250 linhas não são automaticamente grandes demais. `config.py:29-258` muda quando o esquema de configuração muda, e `ferramentas/arquivos.py:27-251` muda quando a política de arquivos muda; ambos são coesos. Em contraste, `javascript.py:59-249` e `javascript.py:265-294` têm consumidores e motivos de mudança diferentes, mesmo tendo menos de 300 linhas.

## 3. Plano de movimentação

O plano abaixo é incremental. Cada item deve ser um commit próprio; o próximo só começa com `python -m pytest` verde na venv do projeto. A linha de base verificada nesta revisão foi `178 passed`.

### Passo 0 — remover donos ambíguos antes de mover arquivos

1. Criar `gates/codigos.py` e transferir `CODIGOS_DO_ORQUESTRADOR` de `gates/__init__.py:7-16`; incluir nele `QAORQ-030`, hoje emitido separadamente em `gates/cobertura.py:42`. O `__init__.py` volta a ser apenas declaração do pacote.
2. Fazer consumidores importarem símbolos do dono real. Hoje `ferramentas/processo.py:23-32` reexporta exceções, `gates/parser.py:12-23` reexporta `ErroDeInvocacao` e `extrair_json`, e `ferramentas/superficie.py:31-33` reexporta `extrair_exports`. Esses atalhos criam mais de uma resposta válida para “onde mora?”.
3. Mover `ErroDeConfiguracao` de `config.py:25-26` para `excecoes.py`, que afirma concentrar as exceções em `excecoes.py:1-15`; `cli.py` e `llm/cliente.py` passam a importar a exceção do dono canônico.

**Validação:** `python -m pytest`; busca sem ocorrências dos imports antigos; teste AST novo garantindo que módulos-folha não reexportem nomes importados. O custo é baixo, mas pode quebrar imports de testes que hoje usam o atalho (`tests/test_parser.py:15-20` e `tests/test_superficie.py:197-236`).

### Passo 1 — movimentos mecânicos de um único dono

Fazer um movimento por commit, usando `git mv`, atualizando imports e sem mudar comportamento:

1. `montagem.py` → `llm/montagem.py` (baixo): todo o módulo monta entradas de modelo; `agentes/mapeador.py`, `agentes/executor.py` e `pipeline.py` são os consumidores.
2. `textos.py` → `ferramentas/json_externo.py` (baixo): seu contrato é recuperar JSON de stdout/stderr ou texto de modelo, não tratar texto genérico (`textos.py:19-53`).
3. `gates/parser.py` → `gates/saidas.py` (baixo): “parser” não diz de quê; o módulo interpreta saídas de validadores (`gates/parser.py:1-4`).
4. `gates/cobertura.py` → `gates/lacunas.py` (baixo): evita colisão conceitual com a classe-relatório `Cobertura` de `ferramentas/scripts_qa.py:40-73` e nomeia o resultado que o gate produz.

**Validação de cada commit:** `python -m pytest`, `python -m orquestrador --help` e busca pelo caminho antigo. Não manter fachadas nesses quatro movimentos: são imports internos, o custo de compatibilidade supera o benefício.

### Passo 2 — criar `analise_estatica/`

1. Mover `javascript.py:59-249` para `analise_estatica/exports_javascript.py`.
2. Mover `javascript.py:265-294` para `analise_estatica/tags_cypress.py`.
3. Mover `ferramentas/superficie.py` inteiro para `analise_estatica/superficie.py`.
4. Atualizar primeiro os consumidores de produção, depois os testes. Usar módulos-ponte nos caminhos antigos apenas durante os commits intermediários; removê-los ao terminar o passo.

**Por quê:** os três módulos respondem à mesma pergunta — “o que existe no código JS sem executá-lo?” — enquanto `ferramentas/` deve ficar reservado a adaptadores de filesystem, subprocesso e CLIs externas. **Custo:** médio; há imports cruzados em gates, pipeline e testes. **Validação:** `tests/test_javascript.py`, `tests/test_superficie.py`, `tests/test_gate_cobertura.py` e depois a suíte completa. O teste estrutural deve proibir `analise_estatica` de importar `agentes`, `gates` ou `pipeline`.

### Passo 3 — reduzir os agentes ao que é próprio de agente

1. Extrair `agentes/mapeador.py:61-274` para `agentes/ferramentas_do_mapeador.py`: modelos de argumentos, wrappers observados e construção das cinco tools. Manter em `mapeador.py` a criação do agente e a unidade de trabalho (`mapeador.py:282-469`).
2. Extrair `agentes/executor.py:110-143` para o serviço de persistência criado no Passo 5. Até lá, não movê-lo isoladamente: criar um terceiro local temporário seria pior.

**Custo:** médio no mapeador e baixo no executor quando o destino já existir. **Validação:** testes de instrumentação das tools, falhas do mapeador, dry-run e suíte completa. A assinatura pública de `mapeador.executar` não muda.

### Passo 4 — dividir `contratos.py`

Executar a migração detalhada na seção 6, mantendo uma fachada temporária que reexporte as **mesmas classes**, nunca cópias. **Custo:** alto por quantidade de imports e pelo schema do Pydantic, não por complexidade algorítmica.

### Passo 5 — criar `aplicacao/` e reduzir o pipeline

1. Extrair ciclo de reparo e persistência conforme a seção 6.
2. Levar `pipeline.py` já reduzido para `aplicacao/pipeline.py`.
3. Levar `simulacao.py` inteiro para `aplicacao/simulacao.py`: ele é uma implementação coesa da execução local, consumida na composição da aplicação (`simulacao.py:9-24`), não um cliente LLM genérico.
4. Atualizar a CLI primeiro; manter `orquestrador.pipeline` como fachada por um único passo para os testes e removê-la quando a busca não encontrar consumidores.

**Custo:** alto no pipeline, baixo na simulação. **Validação:** testes focados descritos na seção 6, `tests/test_dry_run.py`, `python -m orquestrador --dry-run` e suíte completa. Uma fachada deve reexportar a mesma classe; não pode subclassificar nem duplicar tipos, pois isso altera identidade e alvos de monkeypatch.

### Passo 6 — separar coleta de apresentação

Mover os métodos de tabelas Rich de `observabilidade/telemetria.py:131-198` e `observabilidade/telemetria.py:206-246` para funções em `observabilidade/tabelas.py`. `Telemetria` conserva agregação e `resumo_para_log` (`observabilidade/telemetria.py:70-128`, `observabilidade/telemetria.py:200-204` e `observabilidade/telemetria.py:248-267`).

**Custo:** baixo. **Validação:** testes de telemetria/CLI existentes e suíte completa. Não criar uma interface de renderer; duas funções de apresentação são suficientes.

### Passo 7 — alinhar os testes aos donos finais

1. Adotar um arquivo unitário por módulo canônico. Manter arquivos de invariantes apenas quando cruzarem pelo menos dois módulos.
2. Criar no `conftest.py` fábricas-fixture `fabrica_recurso`, `fabrica_pipeline` e `fabrica_saida_de_processo`. A função comum atual é importada diretamente de `conftest.py` (`tests/test_parser.py:13`), o que transforma um arquivo especial do pytest em módulo de biblioteca.
3. Recolocar testes puros no dono correto: casos de export JS em `tests/test_superficie.py:197-236`, tags em `tests/test_gate_cobertura.py:158-176`, renderização Graphify em `tests/test_tools_instrumentadas.py:135-217` e `nomes_de_campos` em `tests/test_schemas_de_entrada.py:267-291`.
4. Dividir `tests/test_falhas_isoladas.py`: mapeador (`tests/test_falhas_isoladas.py:59-106`), isolamento por recurso (`tests/test_falhas_isoladas.py:109-195`) e CLI (`tests/test_falhas_isoladas.py:198-238`) protegem donos diferentes.
5. Renomear `test_dry_run.py` para `test_integracao_dry_run.py`; o teste unitário de roteiro em `tests/test_dry_run.py:169-173` vai para `test_simulacao.py`. Renomear `test_principio_2.py` para `test_invariante_principio_2.py`.

**Custo:** médio e puramente organizacional. **Validação:** o número de testes coletados permanece 178 antes de qualquer teste novo; `pytest --collect-only -q` não contém nomes duplicados; depois, suíte completa. Não criar `tests/unitarios/` e `tests/integracao/` agora: 15 arquivos continuam navegáveis e a separação pode ser expressa por nome e marker.

### Passo 8 — tornar o mapa impossível de apodrecer silenciosamente

Substituir no README a árvore manual de módulos (`README.md:316-346`) por uma árvore curta, gerada em teste a partir de uma lista de donos e responsabilidades, ou testar que todos os `.py` de produção aparecem nela. A primeira opção é preferível: estrutura não deve ser duplicada em prosa. Acrescentar o teste de caminhos em Markdown apenas a arquivos alterados no commit; aplicar retroativamente a todo o histórico faria revisões antigas falharem depois de movimentos legítimos.

## 4. Regra de decisão para código novo

Texto pronto para colar no `AGENTS.md`:

```markdown
### Onde colocar código novo

Escolha o diretório pelo único motivo dominante de mudança:

- `dominio/`: contratos e regras puras; não faz I/O nem importa aplicação.
- `aplicacao/`: coordena estágios, tentativas, handoffs e persistência da execução.
- `agentes/`: uma unidade stateless que monta/invoca um criador LLM; tools exclusivas do agente ficam ao lado dele.
- `gates/`: reprovação determinística e interpretação da saída dos validadores.
- `analise_estatica/`: lê ou interpreta código-fonte sem executá-lo.
- `ferramentas/`: adaptador de filesystem, subprocesso ou CLI externa.
- `llm/`: cliente, mensagens, saída estruturada e montagem de prompt comum.
- `observabilidade/`: eventos, métricas e apresentação operacional; nunca decide o fluxo.
- raiz de `orquestrador/`: somente `cli.py`, `config.py`, `excecoes.py`, `raiz.py`, `__init__.py` e `__main__.py`.

Não crie `utils.py`, `helpers.py`, `common.py` ou `models.py`. Se um código couber em dois donos, a fronteira ainda não está clara: extraia a parte pura para o dono inferior e deixe a coordenação no superior. Imports sempre apontam para o módulo canônico; `__init__.py` não reexporta API.
```

Essa regra é curta, mas só é confiável se o teste AST verificar a lista fechada da raiz e as direções de dependência. A decisão sem essa verificação voltaria a ser opinativa.

## 5. Convenções

| Convenção | Situação atual | Regra proposta | Como verificar automaticamente | Esforço |
|---|---|---|---|---:|
| Dono canônico | Exceções e parsers são reexportados por módulos-folha (`ferramentas/processo.py:23-32`, `gates/parser.py:12-23`, `ferramentas/superficie.py:31-33`). | Cada símbolo público tem um único módulo definidor; consumidores importam dele. `__init__.py` só documenta o pacote. | Teste AST em `tests/test_estrutura_do_codigo.py`: rejeitar `__all__` com nomes importados e assignments/reexports em `__init__.py`. | baixo |
| Direção de dependência | Imports absolutos são consistentes e não há ciclo de runtime detectável; `excecoes.py:21-24` usa `TYPE_CHECKING` deliberadamente. | Imports internos absolutos; domínio não importa aplicação/agentes/gates, análise estática não importa orquestração, ferramenta não importa agente. | Ruff `TID252` mais `[tool.ruff.lint.flake8-tidy-imports] ban-relative-imports = "all"`; teste AST para a matriz entre pacotes; Pyright `reportImportCycles = "error"`. | médio |
| Imports dentro de função | Dois são legítimos: LangGraph com migração explícita (`agentes/mapeador.py:282-306`) e cliente OpenRouter evitado no dry-run (`llm/cliente.py:16-18`). Os imports em `cli.py:134`, `pipeline.py:386`, `tests/test_loop_reparo.py:32` e `tests/test_tools_instrumentadas.py:156` não precisam ser tardios. | Import no topo por padrão; import local só com comentário que nomeie optionalidade ou ciclo real. | Ruff `PLC0415`; `# noqa: PLC0415` somente nos dois casos documentados, com justificativa na mesma linha ou acima. | baixo |
| Ordem e formato de imports | Há desvios reais em `agentes/executor.py:25-32`, `agentes/mapeador.py:35-48` e `tests/test_falhas_isoladas.py:19-26`. | `__future__`, biblioteca padrão, terceiros, imports absolutos do projeto; um grupo em branco entre eles. | Ruff `I`; `ruff format --check`. | baixo |
| Nomes internos | Predominam módulos/funções/variáveis em `snake_case` e classes em `CapWords`. `montagem`, `textos` e `parser` são genéricos demais para descobrir o conteúdo. | Português, PEP 8 e nome baseado no objeto concreto: `json_externo`, `saidas`, `lacunas`. | Ruff `N801,N802,N803,N806,N999`; teste da lista fechada de módulos raiz. Não habilitar `N818`: `ErroDe...`/`FalhaDe...` é a taxonomia portuguesa do projeto, não deve terminar em `Error`. | baixo |
| Vocabulário externo | `query`/`affected` em `ferramentas/graphify.py:102-130`, métodos LangChain em `simulacao.py:60-75`, `dry_run`, Cypress e OpenRouter não seguem tradução literal. | Preservar nomes exigidos por protocolo, biblioteca, CLI ou formato externo; traduzir apenas o código sob nosso controle. | Pyright confirma overrides; testes de integração/serialização confirmam as bordas. Revisão humana decide se o termo é realmente externo. | baixo |
| Aliases JSON | O domínio interno usa português, enquanto campos externos usam aliases como `naoAplica`, `schemaEntrada` e `handler...` (`contratos.py:289-296` e `contratos.py:339-346`). | Campo Python em português; alias mantém exatamente o contrato externo. Nunca “corrigir” camelCase externo. | Testes Pydantic de dump/load por alias; Ruff `N815` não deve ser habilitado sobre modelos que espelham JSON externo. | baixo |
| Eventos de log | Eventos são strings `snake_case`, mas o catálogo de `README.md:242-246` omite ao menos `execucao_abortada` (`cli.py:199`) e `cypress` (`pipeline.py:393`). | Definir `TipoDeEvento(StrEnum)` em `observabilidade/eventos.py`; toda emissão usa membro do enum. | Pyright no parâmetro de `Registro.evento`; teste exige unicidade e regex `^[a-z][a-z0-9_]*$`; documentação é gerada/testada a partir do enum. | médio |
| Códigos de violação | O catálogo `gates/__init__.py:7-16` está separado das emissões e não contém `QAORQ-030` (`gates/cobertura.py:42`). | Todo código `QAORQ-nnn` é membro de um catálogo tipado em `gates/codigos.py`; mensagem contextual continua junto da emissão. | Teste AST/regex procura `QAORQ-\d{3}` em `src/` e falha se um literal fora do catálogo não estiver registrado; teste de unicidade. | baixo |
| Forma de módulo | Módulos substantivos começam com docstring, `from __future__ import annotations`, imports agrupados e depois tipos/funções. Módulos grandes usam seções; os pequenos não, legitimamente. | Docstring de módulo obrigatória. Constantes antes dos tipos; API pública antes de helpers privados quando isso não quebra a leitura; seções só para separar responsabilidades reais. | Ruff `D100`, `I` e formatter. Ordem semântica dentro do corpo fica em revisão humana; não criar teste de posição frágil. | baixo |
| Docstring e comentário | O padrão bom explica responsabilidade, invariante e modo de falha (`javascript.py:1-31`, `gates/cobertura.py:1-30`, `ferramentas/processo.py:1-12`); comentários locais explicam razão (`observabilidade/telemetria.py:32-34`, `pipeline.py:449-450`). Há referências opacas a tarefas em `pipeline.py:61` e `pipeline.py:444-450`, além de documentação de fase já derivada, tratada em A18. | Docstring de módulo responde “qual fronteira e qual invariante?”. Docstring pública registra contrato não evidente. Comentário registra por que uma alternativa óbvia é errada; nunca repete a linha nem depende de código de tarefa sem explicar a invariante. | Ruff `D100` verifica presença. Conteúdo semântico é revisão humana; não habilitar `D101/D103` indiscriminadamente, pois produzirá texto vazio escrito por IA. Hook pode rejeitar `Tarefa [A-Z]\d+` em comentário sem frase explicativa, mas não deve tentar medir qualidade. | baixo |
| Referências externas em comentários | `references/preparar-projeto.md` aparece como se fosse local em `excecoes.py:42-46`; há referências equivalentes em `ferramentas/graphify.py:1-3` e `agentes/auditor.py:12`. | Prefixar documento externo com o dono: `qa-api: references/...`; caminho local deve existir no repositório. | Hook de Markdown/comentários valida caminhos locais reconhecíveis; lista curta de prefixos externos (`qa-api:`). | baixo |
| Caminhos em documentação | A revisão anterior publicou três caminhos incorretos; a árvore do README é manual e incompleta (`README.md:316-346`). | Citação nova de arquivo local deve existir no commit em que é escrita; linha citada deve estar dentro do arquivo naquele momento. | Hook sobre Markdown adicionado/alterado extrai caminhos entre crases e `arquivo:linha`, verifica existência e limite de linha. Não rodar retroativamente em documentos históricos após refatorações. | médio |
| Tamanho de módulo | Dois arquivos são grandes por mistura de responsabilidades; outros de 250 linhas são coesos. | Dividir por motivo de mudança e direção de dependência, nunca por um teto numérico. Acima de 350 linhas, exigir justificativa na revisão, não falha automática. | Só revisão humana; uma métrica informativa pode listar arquivos >350 sem reprovar CI. | baixo |
| Acesso a privados nos testes | Testes chamam `_ciclo` (`tests/test_loop_reparo.py:47-55`), `_formatador` (`tests/test_gate_b.py:21-84`) e `_rodar_recurso` (`tests/test_falhas_isoladas.py:191`). | Depois das extrações, testar a API pública da unidade extraída; privado é detalhe, salvo helper puro explicitamente mantido local. | Pyright `reportPrivateUsage = "error"` somente depois da migração; antes disso, modo warning para inventário. | médio |
| Organização dos testes | É mista: alguns arquivos seguem módulo, outros cenário transversal. Há casos puros sob arquivos de integração e várias construções repetidas de `Recurso`/`Pipeline`. | Um arquivo unitário por módulo canônico; arquivo `test_invariante_*` quando cruza ≥2 módulos; `test_integracao_*` para subprocesso/composição real. Shared setup só como fixture/fábrica do pytest. | Teste AST proíbe `from tests.conftest`/`from conftest`; marker pytest para integração; `--strict-markers`; revisão humana da regra “≥2 módulos”. | médio |
| Fixtures roteirizadas | Os arquivos seguem `tentativa-01.json`, `tentativa-02.json` em `fixtures/roteiros/...`, e são dados de runtime do dry-run, não fixtures exclusivas do pytest (`simulacao.py:9-24`). | Caminho `<recurso>/<estagio>/tentativa-NN.json`; JSON deve validar contra o contrato do estágio e a sequência deve ser contígua. | Teste parametrizado percorre `fixtures/roteiros`, aplica regex, detecta lacunas e valida cada JSON pelo Pydantic apropriado. | baixo |
| Artefatos gerados na raiz | `.execucoes/`, cache do pytest, venv e builds são ignorados (`.gitignore:1-9`), mas `.coverage` existe na raiz e não está coberto. | Todo artefato de execução/cobertura fica ignorado; nenhum arquivo gerado entra no diff. | Acrescentar `.coverage*` e `htmlcov/` ao ignore; hook executa `git check-ignore` para caminhos gerados conhecidos. | baixo |
| Codificação e finais de linha | O código declara UTF-8 nas bordas de subprocesso e arquivo; o formatter ainda não está configurado. | UTF-8, LF no repositório; conversão de newline só na borda que exigir formato externo. | `.editorconfig` e `.gitattributes` (`*.py text eol=lf`, Markdown/TOML idem); Ruff formatter. Um teste não deve comparar newline do SO sem normalizar. | baixo |

### Padrão explícito de documentação

Regra citável: **“A docstring do módulo explica a fronteira, a invariante e, quando relevante, o modo de falha; a docstring da API pública explica apenas contratos não dedutíveis da assinatura; comentários locais explicam por que uma escolha menos óbvia é necessária. Não narrar o código, não registrar histórico de tarefa e não citar caminho externo sem nomear o repositório.”**

Esse padrão é sustentável porque restringe o texto ao que o código não expressa. O exemplo de `ferramentas/processo.py:3-11` é bom: registra regras de subprocesso específicas do Windows e suas causas. O oposto são marcadores como “Tarefa A3” em `pipeline.py:444-450`: sem o documento original, eles não preservam a decisão. A referência desatualizada a `requirements.txt` no erro de `agentes/mapeador.py:294-300`, enquanto a instalação documentada usa `pip install -e ".[dev]"` em `README.md:89-99`, também viola o padrão por dar uma ação operacional que o próprio repositório não suporta.

## 6. Plano de divisão de `pipeline.py` e `contratos.py`

### `pipeline.py`: dividir, mas parar na orquestração

A15 acertou que o arquivo faz demais; a divisão útil é por estado e efeito, não pelos “Blocos 0–3”. O destino final é `aplicacao/pipeline.py` com aproximadamente 300–340 linhas de orquestração legível, apoiado por dois serviços pequenos.

| Bloco atual | Destino | Contrato do destino |
|---|---|---|
| `pipeline.py:51-63` (`ResultadoDoRecurso`) | `aplicacao/pipeline.py` | Resultado público da orquestração por recurso. |
| `pipeline.py:66-72` (`_unir_caminhos`) | `aplicacao/ciclo_de_reparo.py` | União estável, sem duplicatas, dos artefatos produzidos entre tentativas. |
| `pipeline.py:75-98` (`nomes_de_campos`) | `aplicacao/persistencia.py` | Derivação pura dos nomes que orientam preservação de schema. |
| `pipeline.py:106-205` | `aplicacao/pipeline.py` | Composição, seleção de modelo, Bloco 0 e extração de superfície. |
| `pipeline.py:209-265` | `aplicacao/pipeline.py` | Orquestra o Bloco 1; o closure de persistência delega ao serviço. |
| `pipeline.py:267-328` | `aplicacao/persistencia.py` | `PersistenciaDeArtefatos.persistir_schemas` e aviso de divergência; o estado de schemas preservados pertence aqui. |
| `pipeline.py:332-377` | `aplicacao/pipeline.py` | Orquestra o Bloco 2; persistência delegada. |
| `pipeline.py:381-416` | `aplicacao/pipeline.py` | Orquestra o Gate B e a medição Cypress. |
| `pipeline.py:420-556` | `aplicacao/ciclo_de_reparo.py` | `CicloDeReparo.executar` e registro de tentativa, parametrizados por criador, gate e persistência. |
| `pipeline.py:560-604` | `aplicacao/pipeline.py` | Iteração, isolamento e resultado final por recurso. |
| `agentes/executor.py:110-143` | `aplicacao/persistencia.py` | Escrita e leitura canônica dos artefatos do executor. |

`PersistenciaDeArtefatos` recebe `Config`, `Registro` e diretório da execução; concentra escrita, leitura, confinamento e preservação de schemas. `CicloDeReparo` recebe configuração de tentativas, registro e telemetria e não conhece blocos numerados. O pipeline fornece as funções de criar, validar e persistir. Isso mantém o loop genérico sem torná-lo um framework.

Ordem segura:

1. **Extrair o ciclo sem mudar imports públicos.** Criar `CicloDeReparo`; manter `Pipeline._ciclo` como delegador temporário porque `tests/test_loop_reparo.py:47-55` o chama diretamente. O que pode quebrar: ordem dos eventos, acumulação dos caminhos e anexação dos artefatos à exceção. Validar `test_loop_reparo.py`, `test_falhas_isoladas.py`, `test_principio_2.py` e a suíte completa.
2. **Extrair persistência.** Mover primeiro `nomes_de_campos`, depois schemas, depois os helpers do executor. Manter aliases finos durante um commit para atualizar os testes. O que pode quebrar: confinamento, encoding, ordem dos arquivos, dono do schema preservado e conteúdo usado no reparo. Validar `test_schemas_de_entrada.py`, `test_confinamento.py`, `test_dry_run.py` e a suíte completa.
3. **Mover o pipeline reduzido.** `git mv src/orquestrador/pipeline.py src/orquestrador/aplicacao/pipeline.py`; criar uma fachada temporária no caminho antigo, atualizar CLI e produção, depois testes, e removê-la quando `rg "orquestrador\.pipeline"` retornar vazio. O que pode quebrar: identidade de classe e monkeypatches. A fachada só reexporta o objeto original.
4. **Endurecer a fronteira.** Ativar a regra AST: `aplicacao/pipeline.py` pode importar domínio, gates, agentes, ferramentas, LLM e observabilidade; nenhum deles pode importá-lo. Ativar `reportPrivateUsage` depois de os testes usarem `CicloDeReparo` diretamente.

Não dividir `bloco0`, `bloco1`, `bloco2`, `bloco3`, `rodar` e `_rodar_recurso` em módulos separados. Eles formam a narrativa de orquestração; separá-los por número criaria navegação sem independência.

### `contratos.py`: dividir por vocabulário, preservando identidade Pydantic

O arquivo não deve virar vários arquivos chamados `modelos.py`. Cada destino abaixo nomeia o subdomínio que possui os tipos.

| Bloco atual | Destino | Conteúdo |
|---|---|---|
| `contratos.py:71-154` | `dominio/gates.py` | `Violacao`, `ResultadoGate`, `EstagioDelta`, `Delta`. |
| `contratos.py:37-68` e `contratos.py:157-365` | `dominio/cobertura.py` | categorias, normalização de endpoint, `Recurso`, inventário e manifesto. |
| `contratos.py:366-510` | `dominio/artefatos.py` | caminhos de schema e saídas de mapeador/executor. |
| `contratos.py:512-591` | `dominio/superficie.py` | exports, módulos e `SuperficieDoProjeto`. |
| `contratos.py:594-618` | `dominio/auditoria.py` | achados e resultado do auditor semântico. |
| `contratos.py:621-689` | `observabilidade/modelos.py` | uso de tokens, registros de chamada e de tool. |
| `contratos.py:692-702` | `observabilidade/registro.py` | Fundir `dados_para_log` com seu único domínio de uso, não criar outro módulo. |

Ordem segura:

1. Criar `dominio/` e mover gates. `contratos.py` vira provisoriamente uma fachada que importa e reexporta **a mesma** `Violacao`, `ResultadoGate` e `Delta`. Atualizar gates e testes. Validar `test_delta.py`, `test_parser.py`, `test_gate_b.py` e testes de gates.
2. Mover cobertura, depois artefatos. Atualizar consumidores em lotes pequenos, produção antes de testes. Validar `test_contratos.py`, `test_confinamento.py`, `test_schemas_de_entrada.py`, `test_principio_2.py` e dry-run.
3. Mover superfície e auditoria. Validar `test_superficie.py`, `test_javascript.py` e chamadas do auditor/CLI.
4. Mover os contratos de observabilidade e fundir `dados_para_log`. Validar `test_tools_instrumentadas.py`, `test_principio_2.py`, logs do dry-run e suíte completa.
5. Executar uma busca por `from orquestrador.contratos`; quando zerar, remover a fachada e ativar a regra estrutural que proíbe recriá-la.

O que pode quebrar em todos os passos: referências futuras, `$defs` e nomes qualificados nos schemas JSON do Pydantic, identidade de classe em `isinstance`, imports de `TYPE_CHECKING` e alvos de monkeypatch. Por isso não se copiam classes e não se move tudo de uma vez. Além dos testes focados, comparar snapshots estruturais de `model_json_schema()` antes/depois para os seis contratos públicos principais; normalizar apenas ordenação, nunca nomes ou aliases.

Constantes sem uso como `CATS_SET` e `ESTADOS_DE_EXCECAO` em `contratos.py:44` e `contratos.py:50` devem ser removidas durante a extração se `rg` confirmar ausência de consumidores. Não criar um arquivo `constantes.py` para preservá-las.

## 7. O que não vale mexer

- **Não renomear o código para inglês.** A inconsistência real está em dono e precisão, não no idioma. Termos de protocolo — Cypress, OpenRouter, Graphify, `query`, `affected`, `dry_run` — permanecem como a borda os define.
- **Não mover `prompts/` para dentro de `src/` nesta revisão.** É conteúdo editorial deliberadamente separado (`pyproject.toml:79-80`). A questão de empacotamento já é A8; reorganizá-lo aqui reabriria arquitetura sem melhorar descobribilidade do código.
- **Não mover `fixtures/` para `tests/fixtures/`.** O dry-run consome esse diretório em runtime (`simulacao.py:9-24` e `cli.py:149-151`); tratá-lo como dado exclusivo de teste tornaria sua responsabilidade menos clara.
- **Não mover `config.toml`, `docs/`, `.claude/` ou `.execucoes/`.** `config.toml` é a configuração padrão resolvida em `raiz.py:37`; `docs/` tem poucos documentos; `.claude/skills/push/SKILL.md:1` é específico da ferramenta; `.execucoes/` é saída local já ignorada em `.gitignore:2`. Criar subpastas de documentação agora seria taxonomia sem volume.
- **Não criar `utils/`, `helpers/`, `common/`, `shared/`, `models.py` ou `constantes.py`.** Esses nomes escondem o motivo de mudança e recriam exatamente o depósito que se quer eliminar.
- **Não aplicar limite rígido de linhas.** Ele dividiria `config.py` e `ferramentas/arquivos.py`, que são coesos, e incentivaria arquivos artificiais. Use o limiar de 350 apenas como pergunta em revisão.
- **Não dividir módulos pequenos só para simetria.** `llm/cliente.py`, `raiz.py` e `ferramentas/scripts_qa.py` são pequenos porque têm contratos estreitos; isso é qualidade, não desperdício.
- **Não reexportar toda API nos `__init__.py`.** Isso esconde o dono, aumenta risco de ciclo e torna busca/revisão menos confiável. Os módulos canônicos já são uma API suficientemente clara.
- **Não manter fachadas de compatibilidade indefinidamente.** Elas são andaimes de migração e devem ter condição explícita de remoção no mesmo plano. Duas rotas permanentes de import anulam a padronização.
- **Não habilitar todas as regras de docstring, complexidade e naming do Ruff.** `D101/D103` indiscriminados geram docstrings burocráticas; `N818` conflita com a taxonomia portuguesa; limites automáticos de complexidade/linhas incentivam fragmentação. Adotar apenas as regras da tabela.
- **Não criar uma árvore profunda de testes agora.** Com 15 arquivos, nomes `test_integracao_*` e `test_invariante_*`, markers estritos e fábricas no `conftest.py` resolvem o problema com menos navegação.
- **Não transformar o ciclo de reparo em framework genérico nem criar interfaces para cada função.** Dois serviços concretos extraídos do pipeline bastam. Abstração anterior a um segundo consumidor seria custo sem evidência.
- **Não corrigir a deriva do README copiando uma árvore ainda maior.** Gere/teste o inventário ou reduza-o; duplicar mais caminhos aumenta a superfície que apodrece.
