Você é um arquiteto de software sênior fazendo uma segunda revisão externa deste repositório, focada **exclusivamente em organização de arquivos e padronização de código**.

## Leia antes de começar

1. `docs/revisao-arquitetura.md` — a revisão anterior, feita por outro revisor. **Não repita o que já está lá.** Ela cobriu arquitetura, gates, privacidade, custo, ferramentas e regras para agentes. Se precisar referenciar um achado dela, cite pelo código (A1, A15…) e siga em frente.
2. `README.md` da raiz, por inteiro.
3. Todos os módulos de `src/orquestrador/` e todos os arquivos de `tests/`.
4. `pyproject.toml`, `config.toml`, e a árvore de `prompts/`, `fixtures/`, `docs/`, `.claude/`.

## Aviso de qualidade — leia com atenção

A revisão anterior citou três caminhos que **não existem**: `src/orquestrador/llm/montagem.py` (o real é `src/orquestrador/montagem.py`), `src/orquestrador/superficie.py` (o real é `src/orquestrador/ferramentas/superficie.py`) e `src/orquestrador/llm/client.py` (o real é `src/orquestrador/llm/cliente.py`).

Isso custou credibilidade ao relatório. **Toda citação sua precisa ser verificada no disco antes de entrar no texto.** Abra o arquivo, confirme a linha. Se não conseguir confirmar, escreva "não verificado" em vez de citar.

Aliás: o fato de um revisor competente ter *chutado errado* onde esses três arquivos estão é, em si, um dado sobre a descobribilidade da estrutura atual. Considere isso na sua análise.

## O que o projeto é

Orquestrador multi-agente em Python (LangGraph + OpenRouter) que gera suítes Cypress de teste de API lendo um backend. Consome uma skill externa (`qa-api`) por subprocess. Tudo é escrito em português — nomes de identificador, docstrings, comentários, testes. Isso é deliberado e deve continuar.

O código é majoritariamente escrito por IA e vai continuar sendo. Uma convenção que não pode ser verificada automaticamente vai apodrecer — leve isso em conta em toda recomendação.

## O que analisar

**1. Localização.** Para cada módulo de `src/orquestrador/`: ele está no lugar certo? Se não, para onde deveria ir e por quê. Preste atenção especial aos que ficaram na raiz do pacote (`montagem.py`, `javascript.py`, `textos.py`, `simulacao.py`, `raiz.py`, `contratos.py`, `excecoes.py`) — a raiz é o lugar certo para todos eles, ou virou o depósito do que não coube em nenhuma pasta? Entregue um plano de movimentação concreto, arquivo por arquivo, com o custo de cada movimento.

**2. A regra de decisão.** Escreva a regra que um agente futuro usa para responder "onde eu ponho este código novo?". Precisa ser curta, sem ambiguidade e verificável por quem lê o diff. Se a estrutura atual não permite uma regra assim, isso é um achado.

**3. Convenção de nome.** Módulos, classes, funções, variáveis, testes, fixtures, eventos de log, códigos de violação. Onde há inconsistência real? Distinga inconsistência de variação legítima. Cite exemplos concretos dos dois lados.

**4. Forma interna dos módulos.** Os módulos seguem um formato consistente — docstring de topo, imports, constantes, tipos, funções públicas, privadas? Onde diverge e isso importa? Há módulos grandes demais ou pequenos demais para justificar existir?

**5. Imports.** Convenção de import absoluto/relativo, ordem, imports dentro de função (há pelo menos um caso), ciclos ou risco de ciclo, `__init__.py` que exporta ou não exporta.

**6. Organização dos testes.** Um arquivo por módulo, por comportamento, ou misto? Os nomes de teste comunicam o comportamento protegido? Há duplicação de fixture entre arquivos? O `conftest.py` está bem dimensionado? Existe teste que deveria estar em outro arquivo?

**7. Padrão de docstring e comentário.** O projeto tem um estilo distinto: docstrings explicam responsabilidade e invariante, comentários explicam o *porquê* de decisões não óbvias, e há comentários longos justificando trade-offs. Descreva o padrão que você observa, avalie se ele se sustenta, e escreva-o como regra explícita e citável. Aponte onde o próprio código já viola o próprio padrão.

**8. Os dois arquivos grandes.** `pipeline.py` e `contratos.py`. A revisão anterior disse que fazem demais. Vá além: entregue o **plano de divisão concreto** — que bloco de linhas vai para qual arquivo novo, qual a ordem segura dos passos, o que quebra em cada passo, e como validar entre eles. Se você achar que dividir agora é pior que manter, diga isso e defenda.

**9. Raiz do repositório.** `prompts/`, `fixtures/`, `docs/`, `config.toml`, `.claude/`, `.execucoes/`. A disposição está certa? Falta alguma pasta? Sobra alguma?

**10. O que dá para automatizar.** Este é o ponto mais importante. Para cada convenção que você propor, diga **como ela é verificada**: regra do Ruff (qual), configuração do Pyright, um teste em `tests/`, um hook, ou "só revisão humana". Convenção sem verificação, num projeto escrito por IA, é decoração. Prefira menos regras verificáveis a muitas regras aspiracionais.

## Restrições

- Tudo continua em português. Não proponha renomear para inglês.
- Não proponha refatoração "big bang". Todo movimento precisa de um caminho incremental com a suíte verde em cada passo.
- Não reabra decisões de arquitetura da revisão anterior. O foco aqui é onde as coisas moram e como são escritas.
- Não repita a tabela de ferramentas da revisão anterior. Referencie e acrescente só o que for específico de organização e padronização.
- A suíte hoje tem 178 testes e passa com `python -m pytest`. Qualquer plano precisa preservar isso.

## Formato da resposta

Escreva em **`docs/revisao-organizacao.md`**. Não altere nenhum outro arquivo — nada de código, configuração ou movimentação real.

Estrutura:

1. **Veredito em até 8 linhas.**
2. **Mapa atual** — tabela com módulo, responsabilidade em uma linha, linhas de código, veredito (fica / move / divide / funde).
3. **Plano de movimentação** — passo a passo incremental, com o que valida cada passo.
4. **Regra de decisão para código novo** — pronta para colar no `AGENTS.md`.
5. **Convenções** — tabela com: convenção, situação atual, regra proposta, **como verificar automaticamente**, esforço.
6. **Plano de divisão de `pipeline.py` e `contratos.py`** — ou a defesa de não dividir agora.
7. **O que não vale mexer** — e por quê.

Escreva em português. Cite arquivo e linha, sempre verificados. Prefira uma recomendação forte e justificada a uma lista de opções neutras.
