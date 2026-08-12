# Estrutura

```
pyproject.toml       deps + configuração de pytest + empacotamento, num arquivo só
config.toml          configuração de execução — é do usuário; nasce de `orquestrador init`
prompts/             instrução fixa de cada estágio (editorial; mapeado para o wheel)
fixtures/            artefatos do --dry-run — desenvolvimento, fora do wheel
tests/
src/orquestrador/
  __init__.py        docstring do pacote
  __main__.py        ponto de entrada de `python -m orquestrador`
  config.py          carga e validação da configuração
  excecoes.py        FalhaDeGate, FalhaDeEstagio, ErroDeFerramenta, ErroDeConfiguracao
  raiz.py            resolução da raiz do projeto — único uso de Path(__file__)
  cli/
    __init__.py
    codigos_de_saida.py  0..5 — o contrato com quem automatiza
    estimativa.py    `--estimar`: conta endpoints e devolve a faixa de token
    init.py          `orquestrador init`: o config.toml comentado
    doctor.py        `orquestrador doctor`: dez diagnósticos do ambiente
    execucoes.py     listar, mostrar, validar e comparar execuções locais
    principal.py     argumentos, montagem da execução e apresentação
  aplicacao/
    __init__.py
    pipeline.py      a ordem dos quatro blocos e o desfecho de cada recurso
    ciclo_de_reparo.py  gera → persiste → avalia → (delta → repete)
    reaproveitamento.py  gabarito, plano, dossiê e inventário lidos de uma execução anterior
    persistencia.py  o que sai para o disco, e de quem é cada arquivo que sai
    simulacao.py     modelo falso dirigido por fixture + sandbox do --dry-run
  dominio/
    __init__.py
    endpoint.py      o vocabulário HTTP que os artefatos compartilham: métodos e forma canônica
    orcamento.py     tetos, consumo e a decisão pura de parar
    recurso.py       Recurso e NomeDeRecurso — a unidade de trabalho e o nome que vira diretório
    inventario.py    o que o backend expõe, segundo quem leu o código
    notas.py         o contrato de forma das notas de descoberta, e os parsers que montam inventário e schemas delas
    manifesto.py     o gabarito de cobertura — espelho de _support/cobertura.json
    plano.py         o gabarito expandido em cenários concretos, e a conferência de completude
    dossie.py        o que o mapeador leu além do gabarito: regras, erros, consultas e incertezas
    limpeza.py       veredito determinístico de limpeza e ausências derivadas do inventário
    veredito.py      Violacao, ResultadoGate, Delta, EstadoDoRecurso
    edicao.py        a troca pontual que o reparo declara, e a aplicação que recusa o que não casa
    artefatos.py     SaidaMapeador, SaidaExecutor, o nome do spec de cada operação e o confinamento de forma de caminho
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
    artefatos.py     mede caminho, bytes e hash dos artefatos, sem copiar conteúdo
    eventos.py       TipoDeEvento — o vocabulário fechado do JSONL e a versão do formato
    medidas.py       UsoDeTokens, RegistroDeChamada, RegistroDeTool — o que se mede
    manifesto_de_execucao.py  manifesto-execucao.json: ambiente, commits, hashes, config redigida
    registro.py      log estruturado (JSONL) + console
    rastreamento.py  contexto local de trace/span, operações aninhadas e duração
    provedor.py       callback por requisição efetiva, inclusive voltas internas do ReAct
    leitura.py        leitor e validador somente leitura dos JSONL v0, v1 e v2
    relatorios.py     resumos, histórico real/dry-run e comparação de execuções
    exportacao_otlp.py  projeção minimizada e assíncrona de traces/métricas via OTLP/HTTP
    telemetria.py    agregação de tokens e caracteres por estágio, recurso, tentativa
    tabelas.py       as tabelas Rich do resumo final
  agentes/
    __init__.py
    mapeador.py                 a unidade de trabalho do Bloco 1
    ferramentas_do_mapeador.py  as cinco tools e a medição de cada chamada
    guarda_de_orcamento.py      a checagem que os dois agentes fazem antes de chamar
    grafo_react.py              todo o acoplamento com o LangGraph
    planejador.py               expande o gabarito em cenários, um endpoint por chamada
    executor.py                 chamadas estruturadas por fatia de arquivo, sem tools
    auditor.py                  STUB, interface definida
  analise_estatica/
    __init__.py
    exports_javascript.py    parser puro dos `export` de um módulo JS
    tags_cypress.py          parser puro das tags @endpoint/@cat de um spec
    estrutura_de_suite.py    parser puro da árvore describe/context/it, com literais e chamadas
    identificadores_javascript.py  o que um módulo JS importa, liga e chama
    extrator_de_superficie.py  acha os módulos compartilhados e calcula os imports
    rotas_java_spring.py       parser puro das anotações de rota do Spring MVC
    limpeza_javascript.py      parser puro: o que o `_support/` gerado faz ao apagar massa
    extrator_de_endpoints.py   matriz de suporte + grafo → endpoints do backend
    ci_do_projeto.py         que integração contínua o projeto já usa, e o que cabe gerar para ela
  ferramentas/
    __init__.py
    processo.py      subprocess (lista de argumentos, utf-8, os dois fluxos)
    retencao_de_execucoes.py  prévia, confinamento, revalidação e remoção explícita
    graphify.py      extract/query/affected — o extrator é dependência Python deste projeto
    arquivos.py      ler/listar/buscar com confinamento de caminho
    privacidade.py   denylist, .llmignore e redação de segredo antes do envio
    publicacao.py    staging por recurso, diário de propriedade, publicação atômica
    json_externo.py  extrair_json tolerante de stdout de ferramenta (única impl.)
    pipeline_ci.py   escreve o .yml da suíte quando o projeto já tem CI, e nunca sobrescreve
  gates/
    __init__.py
    codigos.py       catálogo dos códigos de violação QAORQ-
    evidencias.py    verificação determinística do dossiê: evidência, citações, checklist
    gate_a.py        diff grafo × manifesto + conferência do dossiê
    gate_b.py        prettier + eslint + limpeza gerada + padrão de código
    limpeza.py       QAORQ-031/032/033: a receita de limpeza virou código de verdade
    cobertura.py     QAORQ-030/082: o gabarito prometeu, os specs entregaram?
    padrao_cypress.py  QAORQ-070..081: o pedaço da norma de código que um script prova
    saidas.py        JSON de ferramenta externa → ResultadoGate/Violacao
```

`analise_estatica/` responde "o que existe neste JavaScript" sem executá-lo, e é a
razão de `exports_javascript.py` não morar em `ferramentas/`: entra texto, sai
`ExportJs`. `ferramentas/` fica reservado ao adaptador de disco, de subprocesso e
de CLI de terceiro.

`tests/test_invariante_estrutura_do_codigo.py` **verifica
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
`.execucoes/`, por causa da resolução de caminho: os specs importam os módulos
compartilhados por caminho relativo (`../../../../support/api/...`), resolvido a
partir do arquivo. Numa árvore de outra profundidade todo import falharia no
staging e resolveria no destino — que é justamente o contrário do motivo de validar
o staging. (Havia uma segunda razão, a busca que o `cobertura/handlers.mjs` da
skill fazia subindo do recurso; ela saiu com o desacoplamento, e a primeira sozinha
já decide.) Os schemas, esses, ficam em `.execucoes/<ts>/staging/`, porque quem os
lê recebe o diretório pronto. O ponto inicial do nome mantém o staging fora do
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
`src/orquestrador/raiz.py`, por `importlib.resources`, e
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

**Onde mora `Path(__file__)`.** Em `src/orquestrador/raiz.py`, e só lá.
Havia cinco módulos calculando a raiz por conta própria; depois que o código desceu
para `src/`, cada um passaria a apontar para dentro do pacote e uma saída
configurada como `.execucoes` iria parar em `src/orquestrador/.execucoes/` — sem
erro nenhum, só no lugar errado.
