# AGENTS.md

Regras canônicas para qualquer agente que trabalhe neste repositório.

O [`README.md`](README.md) ensina **o que o projeto é e como rodá-lo**; este arquivo
diz **o que não pode ser quebrado e onde as coisas moram**. Não duplique um no
outro: se divergirem, um dos dois está desatualizado, e regra duplicada só descobre
isso depois que já foi seguida errada.

## Propósito

Orquestrador em Python (LangGraph + OpenRouter) que coordena três estágios — dois
com LLM, um determinístico — para gerar suítes Cypress de API a partir de um
backend, dirigido pela skill externa `qa-api`.

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
| ★ `dominio/` | contratos e regras puras | sem I/O, sem `Path`, sem subprocess |
| ★ `aplicacao/` | coordena estágios e persistência | não parseia saída de ferramenta |
| raiz do pacote | **lista fechada**: `cli.py`, `config.py`, `excecoes.py`, `raiz.py`, `__init__.py`, `__main__.py`, e — até a Etapa 6 — `contratos.py`, `pipeline.py`, `simulacao.py` | não recebe arquivo novo |

★ ainda não existem — são a Etapa 6 de
[`docs/plano-de-execucao.md`](docs/plano-de-execucao.md). Não as crie por conta
própria numa tarefa que não seja essa. Até lá, coordenação fica em `pipeline.py` e
contrato puro em `contratos.py`, na raiz.

**Nunca crie `utils.py`, `helpers.py`, `common.py`, `models.py` ou
`constantes.py`.** Nome que não diz o motivo de mudança vira depósito.

**Se um código couber em dois donos, a fronteira não está clara**: extraia a parte
pura para o dono inferior e deixe só o I/O no de cima. O par de referência é
`analise_estatica/exports_javascript.py` — entra texto, sai `ExportJs`, sem
dependência do projeto — e `ferramentas/scripts_qa.py`, que invoca `.mjs` por
subprocesso e devolve a saída crua para outro módulo interpretar. Use os dois antes
de inventar um arranjo novo.

`analise_estatica/` lê arquivo do disco, e isso não a torna `ferramentas/`. A
fronteira entre as duas não é "toca em `Path`", é **o que muda o módulo**:
`ferramentas/` acompanha a CLI, o código de retorno e o formato de saída de uma
ferramenta de terceiro; `analise_estatica/` acompanha a sintaxe da linguagem e a
convenção de marcação da skill.

**Estas regras têm teste.**
[`tests/test_estrutura_do_codigo.py`](tests/test_estrutura_do_codigo.py) verifica
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

## A skill `qa-api` é somente leitura

Ela mora em `C:\Agents\skills\qa-api` (configurável em `[caminhos].skill` do
[`config.toml`](config.toml)) e é **outro projeto**.

**Nunca modifique, formate, mova, versione, commite nem copie nada dela para dentro
deste repositório.** Nem para "corrigir um bug óbvio", nem para "só rodar o
prettier". Este repositório é consumidor: invoca os scripts `.mjs` por subprocesso,
através dos adaptadores em `ferramentas/`.

Se o comportamento dela precisar mudar, isso é trabalho no repositório dela. Se a
CLI, o JSON de saída ou o código de retorno dela mudarem, o ajuste aqui vem
acompanhado de teste de contrato.

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
- **Não propague a chave do provedor para subprocesso** (Node, Cypress, prettier,
  eslint). O ambiente do subprocesso é montado explicitamente.
- **Nenhuma chamada externa em teste unitário** — nem rede, nem provedor, nem Node.
  Integração tem marker próprio.
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

O dry-run substitui **apenas a resposta do modelo**; tools, scripts `.mjs`, gates e
deltas são reais. Ele não prova compatibilidade com provedor real.

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
