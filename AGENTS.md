# AGENTS.md

Regras canônicas para qualquer agente que trabalhe neste repositório.

O [`README.md`](README.md) ensina **o que o projeto é e como rodá-lo**; este arquivo
diz **o que não pode ser quebrado e onde as coisas moram**. Não duplique um no
outro: se divergirem, um dos dois está desatualizado, e regra duplicada só descobre
isso depois que já foi seguida errada.

## Propósito

Orquestrador em Python (LangGraph + OpenRouter) que coordena três estágios — dois
com LLM, um determinístico — para gerar suítes Cypress de API a partir de um
backend. É um produto instalável: `pip install` num ambiente vazio, e nada fora
do pacote precisa existir na máquina de quem o roda.

A promessa do projeto é cobertura **provada por verificador determinístico**, nunca
cobertura afirmada por um LLM. Quase toda regra abaixo existe para proteger isso.

---

## As seis invariantes

Estão explicadas no README, com a razão de cada uma. Aqui elas valem como limite:

1. **O que trafega entre estágios é artefato em disco**, nunca histórico de
   conversa. Qualquer agente pode morrer e ser reinstanciado do zero sem perda.
2. **O prompt de reparo é exatamente** `instrucao_fixa_do_estagio + artefato_atual +
   delta.violacoes`. Nada de mensagem anterior, resumo de tentativa, raciocínio ou
   "contexto extra" — é esse corte que troca custo quadrático por linear.
3. **Agentes são stateless entre unidades de trabalho.** Um recurso por vez,
   histórico zerado entre recursos.
4. **Quem reprova é script; LLM só cria.** Nenhum LLM decide se a cobertura está
   completa, e nenhum LLM valida a saída de outro LLM.
5. **O auditor semântico fica fora do loop quente.** Não vire gate de tentativa.
6. **Nenhum nome de modelo em código ou prompt.** Modelo, provedor e parâmetros vêm
   de configuração.

**Se uma tarefa exigir contrariar uma destas, pare e exponha o conflito.** Não
contorne em silêncio, não desligue o gate, não afrouxe o teste que a protege.

Corolário operacional: **gate falha fechado**. Ausência de evidência, script que não
rodou, relatório velho ou erro de ferramenta nunca produzem "aprovado".

---

## Onde colocar código novo

Escolha o diretório pelo **único motivo dominante de mudança**:

| Diretório | Motivo dominante | O que ele não faz |
| --- | --- | --- |
| `agentes/` | monta e invoca um criador LLM | não decide aprovação |
| `gates/` | reprova determinística | nunca chama LLM |
| `ferramentas/` | adaptador de I/O externo: disco, subprocesso, CLI de terceiro | não contém regra de domínio |
| `llm/` | cliente, saída estruturada e montagem de prompt | não conhece gate nem recurso |
| `observabilidade/` | eventos e métricas | nunca decide fluxo |
| `analise_estatica/` | lê código-fonte sem executar | não abre subprocesso nem fala com a rede |
| `dominio/` | contratos e regras puras | não abre, não lê, não escreve, não lista e não resolve caminho; não roda subprocesso; não importa outro subpacote. `Path` entra só como valor |
| `aplicacao/` | coordena estágios e persistência | não parseia saída de ferramenta, não decide aprovação |
| `cli/` | argumentos, apresentação e códigos de saída | não coordena estágio nem escreve artefato |
| raiz do pacote | **lista fechada**: `config.py`, `excecoes.py`, `raiz.py`, `__init__.py`, `__main__.py` | não recebe arquivo novo |

**Sobre o `Path` em `dominio/`.** A regra é sobre **acesso**, não sobre o tipo.
`Recurso.caminho_testes` é álgebra de caminho e `EntradaDoDiario.destino` é chave
de índice: nenhum dos dois toca o disco. Proibir a anotação esvaziaria o pacote —
`Recurso` é o símbolo mais importado do repositório e é literalmente a unidade de
trabalho do princípio 3. O que a regra proíbe tem lista e tem teste:
`test_dominio_nao_toca_no_disco` recusa import de `subprocess`, `os`, `io`,
`shutil` e rede, chamada de `open()`, e qualquer método de acesso a disco de
`Path`.

**Nunca crie `utils.py`, `helpers.py`, `common.py`, `models.py` ou
`constantes.py`.** Nome que não diz o motivo de mudança vira depósito.

**Se um código couber em dois donos, a fronteira não está clara**: extraia a parte
pura para o dono inferior e deixe só o I/O no de cima. O par de referência é
`analise_estatica/limpeza_javascript.py` — entra texto, sai `LimpezaEncontrada`,
sem dependência do projeto — e `ferramentas/graphify.py`, que invoca o extrator por
subprocesso e devolve a saída crua para outro módulo interpretar. Use os dois antes
de inventar um arranjo novo.

`analise_estatica/` lê arquivo do disco, e isso não a torna `ferramentas/`. A
fronteira entre as duas não é "toca em `Path`", é **o que muda o módulo**:
`ferramentas/` acompanha a CLI, o código de retorno e o formato de saída de uma
ferramenta de terceiro; `analise_estatica/` acompanha a sintaxe da linguagem e a
convenção de marcação dos specs (`@endpoint`, `@cat`, `@campo`).

**Estas regras têm teste.**
[`tests/test_invariante_estrutura_do_codigo.py`](tests/test_invariante_estrutura_do_codigo.py) verifica
por AST a lista fechada da raiz, a proibição de reexport em `__init__.py`, a
honestidade de cada `__all__`, a direção de dependência da tabela acima, o catálogo
dos códigos `QAORQ-`, a correspondência entre a árvore do `README.md` e os módulos
reais, e a proibição de `from conftest import`. Falha ali é para ser resolvida
movendo o código — ou, se a decisão de arquitetura mudou mesmo, alterando **esta
tabela e o teste na mesma mudança**, nunca só o teste.

Funções puras recebem dados e devolvem dados; o I/O fica explícito na borda. Falha
de ferramenta ou de provedor é **erro operacional**, não violação para o LLM
reparar.

---

## Nada fora do pacote

O orquestrador é instalado com `pip` e roda com o que veio no wheel. Não há
script de outro repositório, não há Node, não há caminho de máquina configurado.
**Não reintroduza dependência externa sem decisão explícita do dono**: cada uma é
uma máquina de cliente onde a instalação falha, e o cliente não tem como
consertá-la. Quem cobra isso é `test_wheel_limpo`, que instala o wheel num
ambiente vazio e roda `--help`, `init` e `doctor`.

Ferramenta de terceiro entra como **dependência declarada no `pyproject.toml`** —
foi assim que o `graphify` entrou — e nunca como caminho que alguém preenche.

Isto tem história recente. Até 2026-08-10 o projeto era dirigido pela skill
externa `qa-api`, invocando os `.mjs` dela por subprocesso. Ela ainda existe em
`C:\Agents\skills\qa-api` e é **outro projeto**: **nunca modifique, formate,
mova, versione, commite nem copie nada dela para dentro deste repositório** — nem
para consultar como algo era feito lá. O que ela fazia ou virou Python aqui
dentro, ou está registrado como perda em
[`docs/arquitetura/pendencias.md`](docs/arquitetura/pendencias.md); a
reconciliação de cobertura por categoria e por campo é a principal. Se precisar
daquele comportamento, escreva-o aqui, com gate e teste próprios.

---

## `prompts/` é conteúdo editorial

Versionado, iterado por quem não necessariamente mexe em Python. **Não reformule,
reescreva nem "melhore" um prompt como efeito colateral de uma mudança de código.**
Mudança de prompt é tarefa própria, com diff próprio.

---

## Segurança

- **Nunca leia nem envie ao LLM** `.env`, chave, token, credencial, PEM ou qualquer
  segredo. Redija segredo em log, erro, evento e fixture.
  Isto **tem dono e tem teste**: `ferramentas/privacidade.py` aplica denylist, o
  `.llmignore` do consumidor e a redação de conteúdo, e as tools do mapeador
  consultam a política pelo próprio `Confinamento`. Ferramenta nova que leia o
  backend passa por lá; não reimplemente a regra ao lado.
- **Não propague a chave do provedor para subprocesso** (Cypress, prettier, eslint,
  graphify). O ambiente do subprocesso é montado explicitamente.
- **Nenhuma chamada externa em teste unitário** — nem rede, nem provedor, nem
  subprocesso. Integração tem marker próprio.
- Confine caminho à raiz permitida depois de `Path.resolve()`; nada de comparação
  textual de prefixo.

---

## Docstring e comentário

O padrão real do projeto, visível em `raiz.py`, `llm/montagem.py` e
`analise_estatica/exports_javascript.py`:

- **Docstring de módulo explica a fronteira e a invariante** — o que este módulo é
  dono, o que ele deliberadamente não faz, e por quê. Não é um índice das funções.
- **Comentário explica por que a alternativa óbvia está errada.** Se o leitor
  provavelmente pensaria "por que não fazer do jeito X?", responda. Caso contrário,
  não comente.
- **Nunca narre a linha seguinte.** `# incrementa o contador` é ruído.
- **Nunca deixe marcador de fase como fonte de verdade** (`Fase 1`, `TODO`,
  `futuro`, `placeholder`). Estado temporário pertence ao plano de execução, não ao
  código; decisão durável pertence à docstring, escrita no presente.

---

## Como verificar

```powershell
python -m pytest
python -m orquestrador --dry-run --recurso pedidos
```

O dry-run substitui **apenas a resposta do modelo**; tools, gates e deltas são
reais. Ele não prova compatibilidade com provedor real.

**Todo teste declara exatamente um marker**: `unit` (roda com o venv e nada mais),
`integration` (precisa do `uv` ou de outro executável) ou `e2e` (o pipeline
inteiro). A coleta reprova sem ele — ver `pytest_collection_modifyitems` em
[`tests/conftest.py`](tests/conftest.py).

Rode também os dois marcadores pesados, sempre com `-rs`:

```powershell
python -m pytest -m "integration or e2e" -rs
```

**Se algum deles pular, isso é um defeito, não um resultado.** Os `e2e` pularam
por meses porque procuravam um checkout da skill que a configuração já não
declarava — passaram a pular em toda máquina, e ninguém percebeu, porque suíte
verde com casos pulados parece suíte verde. O `-rs` diz o motivo de cada pulo:
leia-o.

Nada acima chama o provedor. Para isso existe a seção seguinte.

---

## A suíte completa com o modelo real

Esta é a verificação que **gasta dinheiro e leva dezenas de minutos**: o pipeline
inteiro, contra o backend de referência, com o DeepSeek respondendo de verdade. É
o único jeito de saber se uma mudança melhorou ou piorou a qualidade dos testes
gerados — o `--dry-run` prova o encanamento, não o julgamento do modelo.

**Só rode quando o usuário pedir.** Ele custa, e a decisão de gastar é dele.

### Antes de rodar

```powershell
python -m orquestrador doctor
```

Os dez itens precisam estar OK. Depois, a régua de tamanho, que não chama modelo
nenhum e diz a ordem de grandeza do que você está prestes a gastar:

```powershell
python -m orquestrador --estimar
```

### O alvo

Tudo já está no [`config.toml`](config.toml) versionado; não invente caminho nem
troque de backend sem o usuário pedir.

| O quê | Onde |
| --- | --- |
| Backend de referência | `C:\LangChainTestes-01\Backend-` (Java/Spring, 27 endpoints em 5 controllers) |
| Projeto Cypress que recebe os testes | `C:\LangChainTestes-01\projeto-de-testes` |
| Recursos | `customers` e `products` — são os nomes reais, e `--recurso` é obrigatório numa execução real |
| Onde os specs aparecem | `<projeto>/cypress/e2e/apis/<recurso>/` |
| Artefatos e log da execução | `.execucoes/<run_id>/` |

### O comando

```powershell
python -m orquestrador --recurso customers --recurso products
```

Rode-o **em segundo plano** e acompanhe: são dezenas de minutos, e prender o
terminal impede o usuário de ver o log enquanto anda. O JSONL vai sendo escrito
durante a execução, então dá para seguir com `Get-Content -Wait`.

Acrescente `--rodar-cypress` **só se o usuário pedir**: ele executa a suíte
gerada de verdade e exige Node e um `[execucao].cypress` configurado. Sem essa
flag, a cobertura relatada é estática — e o resumo final diz isso, em vermelho.

### O relatório é obrigatório

Uma execução sem números não serviu para nada: o motivo de rodá-la é comparar
com a anterior. **Sempre entregue, sem o usuário precisar pedir:**

| Dado | De onde tirar |
| --- | --- |
| Dólares gastos | `orquestrador execucoes listar` → coluna `US$`. É o valor que o **provedor** reportou, não estimativa nossa |
| Tokens por estágio, com cache e raciocínio | tabelas impressas no fim da execução |
| Tempo por estágio | coluna `tempo (s)` da tabela por estágio |
| Tempo total | `orquestrador execucoes mostrar <run_id>` → `duracao_s` |
| Chamadas, falhas de requisição e tools | mesma saída do `mostrar` |
| Tentativas de cada gate | o resumo final: `N tentativa(s)` por estágio |
| Testes gerados | quantos `it` em cada spec de `cypress/e2e/apis/<recurso>/` |
| Ambiente e modelo de cada estágio | `.execucoes/<run_id>/manifesto-execucao.json` |

```powershell
python -m orquestrador execucoes listar
python -m orquestrador execucoes listar --historico
python -m orquestrador execucoes mostrar <run_id>
python -m orquestrador execucoes comparar <run_id_antes> <run_id_depois>
```

O `comparar` é o que responde "melhorou?", e o `--historico` mostra a tendência
ao longo das execuções. Os dois existem para não se comparar execução com
lembrança — e lembrança de custo é sempre otimista.

O `--json` de qualquer um deles serve para colar num relatório sem redigitar
número, que é como se introduz erro de transcrição num dado que custou dinheiro
para obter.

**Relate o que deu errado com o mesmo destaque do que deu certo**: gate que
esgotou tentativas, recurso reprovado, requisição que falhou, campo ausente no
manifesto. Uma execução que terminou com `sucesso: false` e um relatório que só
mostra tokens é pior que nenhum relatório.

**Toda correção de bug inclui um teste que falha antes e passa depois.** Escreva o
teste primeiro e veja-o falhar — teste escrito depois costuma provar o código, não o
comportamento.

---

## Conclusão da tarefa

1. Revise `git diff` e `git status --short`. **Confirme que só os arquivos da sua
   tarefa mudaram** — o working tree é compartilhado com outros agentes, e mudança
   alheia não é sua para tocar, nem para commitar, nem para descartar. Nada de
   `git stash`, `git checkout --`, `git clean` ou `git reset` sobre trabalho que não
   é seu.
2. Rode as verificações proporcionais ao risco e **relate os comandos e os
   resultados**, inclusive o que você não conseguiu rodar.
3. Confirme que nenhuma invariante, segredo ou arquivo externo foi afetado.
4. **Nunca faça commit, push ou release sem pedido explícito do usuário.**
