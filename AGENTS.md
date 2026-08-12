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

### O comando — sempre pelo venv de execução

A suíte leva dezenas de minutos, e o desenvolvimento não para enquanto ela anda.
Dois arquivos que a execução lê **ao vivo** tornariam qualquer edição
concorrente um risco: os `prompts/*.md` são relidos do disco a cada chamada, e o
código Python de um subprocesso futuro viria do checkout. A resposta é congelar:
instalar o pacote num venv próprio e rodar a suíte **dele** — o pacote instalado
carrega a própria cópia de código e prompts em `site-packages`, e o checkout
fica livre para ser editado.

```powershell
python -m venv .venv-execucao
.venv-execucao\Scripts\pip install --quiet .
.venv-execucao\Scripts\python -m orquestrador --recurso customers
```

O `pip install .` (sem `-e`!) é o congelamento: `-e` apontaria de volta para o
checkout e desfaria tudo. **Reinstale a cada suíte** — o venv não se atualiza
sozinho, e rodar código velho achando que é novo é o único jeito de esta
proteção falhar; a reinstalação custa ~30 s contra os minutos da suíte.

O `config.toml`, o backend e o projeto Cypress continuam sendo os do disco: o
congelamento cobre o que muda quando se edita ESTE repositório. A saída continua
em `.execucoes/` (vem da configuração), então `execucoes listar/comparar`
enxergam tudo no mesmo lugar.

Rode **em segundo plano** e acompanhe: o JSONL vai sendo escrito durante a
execução, então dá para seguir com `Get-Content -Wait`.

Acrescente `--rodar-cypress` **só se o usuário pedir**: ele executa a suíte
gerada de verdade e exige Node e um `[execucao].cypress` configurado. Sem essa
flag, a cobertura relatada é estática — e o resumo final diz isso, em vermelho.

### Iterar no executor sem pagar o pipeline inteiro

**Use isto sempre que a mudança for do Bloco 2 para frente**: norma de código,
prompt do executor, fatiamento, reparo ou qualquer gate do Gate B. Rodar a suíte
completa nesses casos é pagar duas vezes pelo mesmo trabalho e ainda medir errado.

```powershell
.venv-execucao\Scripts\python -m orquestrador --recurso customers --reaproveitar <run_id>
```

**Escolhendo o `run_id`.** Precisa ser uma execução que tenha chegado ao plano
daquele recurso — o mapeador e o planejador precisam ter rodado nela. Execução que
morreu no Bloco 1 não serve.

```powershell
python -m orquestrador execucoes listar
dir .execucoes\<run_id>\artefatos\<recurso>   # tem plano.json? então serve
```

**Fixe um só e não troque.** Todas as voltas de uma investigação têm de citar o
MESMO `run_id`, ou a comparação perde o sentido: plano diferente é entrada
diferente. Foi medido — o planejador emitiu 200 cenários numa execução e 253 na
seguinte, com o mesmo backend.

**O que se reaproveita e o que não.** Voltam do disco o gabarito, o plano, o
dossiê e o inventário. **Os dois gates continuam rodando** e o executor roda
inteiro, do zero: reaproveita-se decisão, nunca veredito nem código. A execução
nova grava a própria cópia dos artefatos, então `execucoes comparar` continua
funcionando mesmo depois de a origem ser apagada pela retenção.

**Por que é a medição honesta.** A entrada do executor fica literalmente idêntica
entre as voltas — o mesmo plano, os mesmos cenários, na mesma ordem. O que diferir
no resultado é a sua mudança, e nada mais. Rodar o pipeline completo duas vezes
compara duas coisas que já diferem antes de o executor começar.

**O que custa**, medido em `customers` (5 endpoints, 253 cenários) em 2026-08-12:

| | Suíte completa | Só o executor |
| --- | --- | --- |
| Tempo | ~25 min | **4,5 min** |
| Custo | ~US$ 0,08 | **~US$ 0,05** |
| Chamadas | 38 | 14 |

O ciclo prático é: muda, `pip install .` no venv congelado, roda, lê o Gate B,
muda de novo. Cinco voltas custam menos que uma suíte completa.

**Antes de rodar, dispare o gate em seco** contra a suíte publicada — custo zero,
e é o que separa régua que pega defeito de régua que inventa trabalho:

```powershell
python -c "from pathlib import Path; from orquestrador.gates.padrao_cypress import conferir_padrao; r=Path('../projeto-de-testes/cypress/e2e/apis/customers'); print(conferir_padrao({p.relative_to(r).as_posix(): p.read_text('utf-8') for p in r.rglob('*.js')}).violacoes)"
```

Foi assim que três falsos positivos morreram antes de custar uma volta paga, e
que os tetos de `QAORQ-079` e `QAORQ-084` foram calibrados com número em vez de
gosto.

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
