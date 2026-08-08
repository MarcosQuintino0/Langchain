# Revisão externa de arquitetura

> Escopo desta revisão: estado atual do repositório em 7 de agosto de 2026. As referências de linha apontam para esse estado do working tree. A suíte foi executada com Python 3.13.2 e Node 24.11.1: `178 passed`. A primeira execução fora do workspace falhou por restrição de acesso ao diretório temporário global do Windows; com `--basetemp` dentro de `.execucoes/`, passou integralmente.

## 1. Veredito

1. A decomposição mapeador → gates determinísticos → executor → relatório é a direção correta para atacar amostragem e autoavaliação do LLM.
2. Os seis princípios são coerentes entre si; eu os manteria, com uma interpretação mais rigorosa de “artefato em disco” e “script reprova”.
3. Hoje, porém, o produto ainda pode declarar sucesso sem provar a cobertura: o diff grafo × manifesto é um stub aprovado, o resultado do bloco 0 é ignorado e falhas do Cypress não reprovam a execução.
4. O maior risco comercial não é qualidade de prompt; é falso positivo silencioso, seguido de sobrescrita/perda de arquivos do cliente sem transação ou rollback.
5. A separação por pastas é razoável, mas `pipeline.py` concentra orquestração, persistência, heurísticas de schema, Cypress e telemetria; faltam uma camada de artefatos transacionais e políticas explícitas de privacidade/custo.
6. Os 178 testes protegem bem o caminho conhecido, mas também cristalizam comportamentos perigosos, como gate “aprovado” quando falta relatório e remoção de arquivos reprovados.
7. O `--dry-run` é uma boa simulação do fluxo e não é, ainda, uma segunda implementação; ele não substitui testes de contrato com a skill, o pacote instalado e um provedor real.
8. Empacotamento, compatibilidade versionada com `qa-api`, proteção do código-fonte enviado ao LLM, orçamento e diagnóstico de ambiente são bloqueadores de produto.
9. A prioridade deve ser fechar caminhos de sucesso indevido e de perda de dados antes de ampliar frameworks, agentes ou auditoria semântica.

## 2. Achados de arquitetura

### A1 — O núcleo determinístico ainda falha aberto

**Problema.** A promessa central — enumerar a cobertura a partir do backend e só avançar quando um script provar completude — ainda não é verdadeira. O Gate A aprova sem comparar grafo e manifesto; o pipeline calcula a preparação, mas ignora sua reprovação; e o comando do auditor retorna sucesso embora seja um stub.

**Evidência.** `src/orquestrador/gates/gate_a.py:51-89` devolve `aprovado=True` e apenas um aviso para o diff grafo × manifesto. `src/orquestrador/pipeline.py:146-173` produz `ResultadoPreparacao`, mas `src/orquestrador/pipeline.py:560-565` chama o bloco 0 e não verifica `ok`. `src/orquestrador/cli.py:169-178` encerra o modo auditor com código 0 sem auditoria real. O README reconhece stubs em `README.md:469-476`, mas isso não impede um resultado de sucesso.

**Impacto.** O sistema pode gerar uma suíte internamente consistente com um manifesto incompleto e vender isso como cobertura completa. Esse é exatamente o defeito que a arquitetura se propõe a eliminar. Em produto, um falso “sucesso” aqui é mais grave que uma falha explícita.

**Correção sugerida.** Implementar o diff determinístico por adaptadores de linguagem/framework, normalizando rotas, métodos, autenticação, schemas e operações antes da comparação. Linguagem não suportada deve falhar fechada com diagnóstico de capacidade, não emitir aviso e aprovar. Se o bloco 0 não estiver `ok`, o pipeline deve parar com erro operacional tipado. Enquanto o auditor não existir, o comando deve ser marcado como indisponível e retornar código diferente de zero; ele continua fora do loop quente, preservando o princípio 5.

### A2 — O contrato dos gates permite combinações contraditórias

**Problema.** Falta de artefato e inconsistências entre código de saída e JSON são tratadas como aprovação ou podem ser apagadas na combinação dos gates.

**Evidência.** `src/orquestrador/gates/cobertura.py:64-82` aprova quando o JSON de cobertura não existe, comportamento exigido por `tests/test_gate_cobertura.py:139-150`. `src/orquestrador/gates/parser.py:32-63` confia no campo `valid` do JSON mesmo quando o processo encerra com uma combinação incompatível, exceto pelo caso particular de código 2. `ResultadoGate.combinar`, em `src/orquestrador/contratos.py:113-130`, calcula a aprovação apenas pela ausência de violações e pode transformar um filho `aprovado=False` sem violações em resultado aprovado.

**Impacto.** Falha da ferramenta, relatório ausente, versão incompatível ou bug no parser pode ser confundido com cobertura aprovada. Isso torna a autoridade determinística nominal, não efetiva.

**Correção sugerida.** Separar três estados: `APROVADO`, `REPROVADO` e `ERRO_DA_FERRAMENTA`. Relatório ausente e JSON inválido são erro operacional; não entram no prompt de reparo. Exigir as combinações documentadas código 0/`valid=true` e código 1/`valid=false`; qualquer outra é quebra de contrato. `combinar` deve exigir que todos os filhos estejam aprovados e preservar erros. Adicionar invariantes no modelo para impedir `aprovado=True` com violações ou erro.

### A3 — O handoff em disco existe como cópia, não como fronteira

**Problema.** Os artefatos são gravados, mas o próximo estágio recebe os objetos Python que já estavam em memória. Portanto, o disco ainda não é a fonte efetiva do handoff.

**Evidência.** `src/orquestrador/pipeline.py:224-264` persiste inventário, schemas e manifesto, porém a avaliação usa o objeto corrente. Em `src/orquestrador/pipeline.py:570-576`, o bloco 2 recebe diretamente `saida_mapeador.manifesto`; a assinatura em `src/orquestrador/pipeline.py:332-334` confirma o acoplamento. `tests/test_loop_reparo.py:108-122` verifica ordem de eventos, não reidratação a partir do artefato persistido.

**Impacto.** Uma serialização incompleta, arquivo corrompido, alteração concorrente ou incompatibilidade de schema só aparece numa retomada futura, não no fluxo testado. Retomada, auditoria e reprodutibilidade ficam frágeis.

**Correção sugerida.** Criar um `RepositorioDeArtefatos` com escrita atômica, leitura validada por Pydantic, versão de schema e hash. O pipeline deve passar identificadores/caminhos entre blocos e reabrir o artefato antes do estágio seguinte. Isso não exige histórico de conversa e reforça, em vez de alterar, o princípio 1.

### A4 — O “artefato atual” do reparo não é completo nem canônico

**Problema.** No reparo do mapeador, só o manifesto volta ao modelo; inventário e schemas atuais ficam de fora. No executor, o artefato atual é reconstruído pela concatenação dos arquivos retornados na tentativa anterior e cortado arbitrariamente em 60 mil caracteres.

**Evidência.** `src/orquestrador/pipeline.py:264` usa apenas `saida.manifesto.para_json()` apesar de `SaidaMapeador` conter também inventário e schemas. `src/orquestrador/agentes/executor.py:125-143` concatena a saída anterior e aplica truncamento posicional.

**Impacto.** Violações de schema ou inventário não podem ser reparadas com o estado completo. Em suítes grandes, a parte relevante pode ficar depois do corte; o loop repete chamadas sem chance real de correção. Também não há garantia de que o conteúdo enviado seja o que foi efetivamente gravado em disco.

**Correção sugerida.** Definir um bundle canônico de artefato atual, reaberto do disco. Para o mapeador, incluir inventário, schemas e manifesto atuais. Para o executor, projetar deterministicamente apenas arquivos e trechos apontados por `delta.violacoes`, com contexto de linhas calculado por script e limite explícito. A composição continua exatamente `instrução_fixa + artefato_atual + delta.violacoes`: não adicionar mensagens, resumos de tentativas, raciocínio anterior ou histórico.

### A5 — Escritas não transacionais podem perder trabalho do cliente e deixar lixo aprovado

**Problema.** Manifestos, schemas e specs são sobrescritos no destino final antes da aprovação. A opção de remoção apaga arquivos reprovados sem distinguir arquivo recém-criado de arquivo preexistente. Ao mesmo tempo, arquivos obsoletos de tentativas anteriores podem permanecer e participar do gate.

**Evidência.** `src/orquestrador/pipeline.py:224-247` grava os artefatos diretamente; `src/orquestrador/agentes/executor.py:110-122` sobrescreve specs. Há proteção especial apenas para schemas existentes em `src/orquestrador/pipeline.py:267-305`. `src/orquestrador/cli.py:89-103` chama `unlink()` para todo caminho reprovado; `tests/test_falhas_isoladas.py:226-238` fixa essa remoção como comportamento esperado. Não existe diário de propriedade que permita identificar e remover specs obsoletos gerados pelo orquestrador.

**Impacto.** Uma tentativa ruim pode destruir um teste mantido pelo cliente, deixar o repositório parcialmente modificado ou produzir falso positivo com arquivos antigos. Uma interrupção no meio da gravação também pode deixar JSON ou spec truncado.

**Correção sugerida.** Gerar cada recurso numa área de staging por execução, validar ali e só publicar depois da aprovação. Manter um diário com caminho, hash anterior, hash novo e classificação `criado/modificado/preexistente`; publicar por substituição atômica e fazer rollback em conflito/falha. Specs antigos só podem ser removidos se constarem como propriedade de uma execução anterior e o hash não tiver sido alterado pelo usuário. Desabilitar `--remover-reprovados` até existir essa proteção; nunca apagar silenciosamente um arquivo preexistente.

### A6 — Nomes e caminhos controlados pela entrada podem escapar do projeto

**Problema.** `recurso` e vários caminhos de configuração são strings livres usadas para construir destinos. Há uma verificação por prefixo textual no executor, inadequada no Windows.

**Evidência.** `Recurso` não valida `nome` em `src/orquestrador/contratos.py:162-173`; a CLI recebe nomes crus em `src/orquestrador/cli.py:32-40`. `src/orquestrador/config.py:83-84`, `src/orquestrador/pipeline.py:240` e `src/orquestrador/pipeline.py:288-295` derivam caminhos por concatenação. `src/orquestrador/agentes/executor.py:113-120` usa `str(caminho).startswith(str(raiz))`, que aceita irmãos com prefixo comum e ignora diferenças de normalização/case. Os diretórios configuráveis em `src/orquestrador/config.py:38-48` também não são confinados ao projeto.

**Impacto.** Entradas como `..`, separadores, caminhos absolutos, nomes reservados do Windows (`CON`, `NUL`, `COM1`) ou junctions podem causar escrita/leitura fora do repositório esperado.

**Correção sugerida.** Criar um tipo `NomeDeRecurso` restrito a slug, proibindo separadores, `.`/`..`, ponto/espaço finais e dispositivos reservados. Após `resolve()`, usar `Path.is_relative_to(raiz_resolvida)` em toda leitura e escrita, centralizado na ferramenta de confinamento. Validar os caminhos derivados da configuração com a mesma política e testar symlinks/junctions, case-insensitivity e prefixos irmãos no Windows.

### A7 — Falha do Cypress não muda o resultado do pipeline

**Problema.** O retorno do Cypress é registrado, mas uma execução não zero não reprova o recurso. Um `report.json` antigo pode ser reutilizado.

**Evidência.** `src/orquestrador/pipeline.py:385-416` continua para a cobertura após código não zero e aceita qualquer relatório já existente em `:396-397`. `src/orquestrador/pipeline.py:580-581` conclui o recurso como sucesso.

**Impacto.** Testes que não compilam, não iniciam ou falham em runtime podem ser apresentados como suíte gerada com sucesso. Um relatório residual torna o resultado não reprodutível.

**Correção sugerida.** Usar caminho de relatório único por `run_id`, garantir inexistência antes da chamada e validar timestamp/hash depois. Executar somente os specs do recurso em questão. Código não zero deve virar falha tipada do estágio ou gate, com stdout/stderr limitado e higienizado. Se Cypress for opcional em algum modo, o resultado deve dizer explicitamente `NAO_EXECUTADO`, nunca “sucesso” equivalente a validação runtime.

### A8 — A distribuição por wheel não contém o que a execução padrão procura

**Problema.** Prompts, configuração e fixtures são deliberadamente excluídos do pacote, mas a descoberta da raiz depende da disposição do checkout. A instalação documentada é editável, não valida o produto instalado.

**Evidência.** `pyproject.toml:79-80` exclui esses diretórios. `src/orquestrador/raiz.py:27-40` procura `config.toml`, `prompts/` e `fixtures/` a partir da árvore-fonte. `README.md:93` orienta `pip install -e .`; `README.md:357` explica que esses arquivos ficam fora do pacote.

**Impacto.** O wheel pode ser publicado e instalar corretamente, mas o primeiro comando falhar por não encontrar os recursos. Isso inviabiliza a ambição “instala, fornece chave e aponta os repositórios”.

**Correção sugerida.** Empacotar prompts versionados como `package-data` e acessá-los com `importlib.resources`. O conteúdo editorial pode continuar editável fora de `src`, mas o build precisa copiá-lo ou ele precisa residir no pacote; deve haver uma única versão efetiva. Criar `orquestrador init` para escrever um `config.toml` de projeto e `orquestrador doctor` para diagnosticar dependências. Fixtures são dados de desenvolvimento, não precisam ir ao wheel. Toda release deve construir o wheel e testá-lo numa instalação limpa. A própria documentação do setuptools recomenda declarar explicitamente os dados de pacote ([setuptools: data files](https://setuptools.pypa.io/en/stable/userguide/datafiles.html)).

### A9 — A skill externa é uma dependência sem contrato de compatibilidade

**Problema.** A integração verifica apenas a presença de alguns arquivos e assume formatos de argumento, códigos de saída e JSON. Não há versão/commit suportado nem negociação de capacidades.

**Evidência.** `src/orquestrador/config.py:228-245` valida diretórios e três scripts. Os adaptadores em `src/orquestrador/ferramentas/scripts_qa.py` interpretam contratos implícitos, enquanto `pyproject.toml` não registra essa compatibilidade por ser uma dependência externa.

**Impacto.** Uma alteração compatível do ponto de vista do repositório `qa-api`, mas incompatível com este consumidor, quebra o pipeline ou, pior, muda silenciosamente a semântica de aprovação.

**Correção sugerida.** Declarar uma matriz de compatibilidade com versão/tag ou commit da skill, versão do schema JSON, scripts/capacidades e Node suportado. `doctor` deve verificar Node 24, Graphify, versão da skill e contratos de saída. A CI de integração deve obter uma referência exata da skill e rodar fixtures de contrato. O orquestrador nunca deve modificar, copiar internamente ou fazer commit na skill externa.

### A10 — Não existe uma fronteira de privacidade antes de enviar código ao provedor

**Problema.** A leitura de backend é ampla, sem política explícita de exclusão de segredos; a chave do OpenRouter é herdada por subprocessos; e as preferências de retenção/roteamento do provedor não estão configuradas como política do produto.

**Evidência.** `src/orquestrador/ferramentas/arquivos.py:17-58` define ignorados genéricos e `:113-144` lê arquivos de texto; a busca em `:188-251` pode alcançar `.env`, chaves privadas e configurações com credenciais. `src/orquestrador/ferramentas/processo.py:84-96` não fornece um ambiente mínimo ao subprocesso, então `OPENROUTER_API_KEY` e variáveis de tracing são herdadas por scripts Node, formatadores e Cypress. `src/orquestrador/config.py:87-116` configura endpoint/chave/headers, mas não uma política de privacidade. A CLI carrega `.env` na raiz fixa em `src/orquestrador/cli.py:133-136`, enquanto a mensagem de validação fala em arquivo ao lado da configuração em `src/orquestrador/config.py:103-106`.

**Impacto.** Código, segredo e dados de cliente podem ser enviados a modelos/provedores não aprovados ou expostos a processos que não precisam deles. Sem contrato e telemetria de roteamento, não é possível responder a uma auditoria do cliente.

**Correção sugerida.** Adicionar uma `PoliticaDePrivacidade`: allowlist de raízes/extensões, `.llmignore`, denylist forte (`.env*`, credenciais, PEM, stores de nuvem) e scanner de segredo/alta entropia que bloqueia ou redige antes do LLM. Tornar Zero Data Retention, coleta de dados negada e allowlist de provedores opções configuráveis e conservadoras; sem fallback para provedor fora da política. O OpenRouter documenta `zdr=true` e restrições de roteamento ([ZDR](https://openrouter.ai/docs/guides/features/zdr), [provider routing e privacidade](https://openrouter.ai/docs/guides/privacy/provider-logging/)). Subprocessos devem receber ambiente mínimo: retirar chave do LLM e tracing, liberando separadamente apenas variáveis Cypress que o projeto autorizar. Para produto, preferir armazenamento do sistema operacional; `.env` ao lado da configuração pode continuar como conveniência de desenvolvimento documentada.

### A11 — Erros do provedor e retries não pertencem a um contrato operacional claro

**Problema.** Falhas de autenticação, limite, timeout, rede e resposta estruturada não são normalizadas. O cliente pode fazer retries internos invisíveis para a telemetria do pipeline.

**Evidência.** `src/orquestrador/llm/estruturado.py:138-160` trata apenas `NotImplementedError` no fallback de structured output. `src/orquestrador/cli.py:195-200` captura só erros de ferramenta e configuração. `src/orquestrador/config.py:97-98` e `src/orquestrador/llm/client.py:39` habilitam retries no cliente sem conectá-los ao orçamento e ao registro de tentativas.

**Impacto.** O usuário recebe traceback ou gasto adicional pouco explicável; uma indisponibilidade pode parecer falha do artefato e acionar reparo indevido. O custo e a duração deixam de ser previsíveis.

**Correção sugerida.** Criar `ErroDeProvedor` com categorias autenticação, rate limit, timeout, transitório e contrato inválido. Erros operacionais nunca viram `delta.violacoes`. Centralizar retries limitados no orquestrador, com tentativa, request ID, espera e custo registrados; configurar o cliente subjacente sem retry oculto quando possível. A CLI deve encerrar com mensagem acionável e códigos distintos para configuração, ferramenta, gate e provedor.

### A12 — Custo é observado depois, não controlado antes

**Problema.** Há contagem de tokens e duração, mas não teto monetário ou de chamadas, estimativa prévia ou interrupção segura por orçamento.

**Evidência.** `ConfigEstagio`, em `src/orquestrador/config.py:124-134`, oferece `max_tokens` e tentativas por estágio. `RegistroDeChamada`, em `src/orquestrador/contratos.py:640-658`, guarda tokens e tempo, mas não custo, rota/provedor efetivo, cache ou limite acumulado.

**Impacto.** Um backend grande ou loop repetido pode ultrapassar a expectativa comercial sem o usuário conseguir aprovar o custo antes. Isso dificulta precificação, suporte e proteção contra abuso.

**Correção sugerida.** Introduzir limites duros por execução e recurso: chamadas, tokens de entrada/saída, bytes de ferramenta, tempo e moeda. O comando deve mostrar estimativa em faixa e plano de unidades antes de aplicar, interrompendo antes da próxima chamada quando o teto não comportar a tentativa. Registrar custo e provedor/rota retornados pela API; o OpenRouter expõe custo no accounting de uso ([usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)). Manter preço apenas em configuração/metadata do provedor, nunca espalhado no código.

### A13 — `Any` esconde contratos de estado e dependências importantes

**Problema.** `Any` aparece justamente nas fronteiras que coordenam modelo, registro, callbacks, estado do grafo e fixtures. Nem todo `Any` é ruim: JSON externo realmente precisa de uma fronteira dinâmica. O problema é deixá-lo atravessar o domínio.

**Evidência.** `src/orquestrador/pipeline.py:124-135` tipa cache/modelo como `Any`; `:420-430` faz o mesmo com callbacks e retorno genérico. `src/orquestrador/agentes/executor.py:79-81` perde os tipos de modelo e registro. `src/orquestrador/agentes/mapeador.py:282`, `:339-341` e `:446` usa `Any` no estado/saída. `src/orquestrador/observabilidade/telemetria.py:73,107` não tipa registro/chaves. `src/orquestrador/simulacao.py:56,111,132` representa passos de fixture como `dict[str, Any]`. Em contraste, `dict[str, Any]` no instante de decodificar JSON em configuração/parser é aceitável se convertido imediatamente.

**Impacto.** Mudanças de assinatura passam pelo type checker e falham só em runtime. Fixtures malformadas e callbacks incompatíveis parecem válidos. Em código majoritariamente produzido por IA, esse tipo de contrato frouxo favorece APIs inventadas e campos inconsistentes.

**Correção sugerida.** Usar `BaseChatModel`/protocolos mínimos, um `Protocol` para registrador, `TypeVar` em `_ciclo`, `TypedDict` estrito para estado do LangGraph e modelos Pydantic discriminados para passos de simulação. Criar `JsonValue` apenas na borda e converter para DTOs. Separar dicionários soltos de estágios/gates em modelos nomeados. Ativar Pyright estrito em `src` sem criar um baseline permanente de ignores.

### A14 — Configuração validada pode voltar a ficar inválida pela CLI

**Problema.** Limites numéricos aceitam valores sem restrições de sinal; a CLI altera um modelo já validado e usa truthiness, tratando zero e negativos de forma inconsistente. Caminhos e URL também têm contratos fracos.

**Evidência.** `src/orquestrador/config.py:124-134` e `:161-173` usam `int`/`float` livres para tentativas, timeout e tamanhos. `src/orquestrador/cli.py:138-140` faz mutação depois da validação com `if args.max_tentativas`, ignorando zero e aceitando negativo truthy. `src/orquestrador/config.py:38-48` mantém diretórios como strings sem confinamento e a URL do provedor não usa tipo de URL.

**Impacto.** Um valor inválido chega a loops, subprocessos ou rede e produz comportamento difícil de diagnosticar. A validação Pydantic dá uma falsa sensação de segurança.

**Correção sugerida.** Usar `PositiveInt`, `NonNegativeFloat`, limites máximos sensatos e URL validada. Tornar configuração imutável ou `validate_assignment=True`; overrides devem usar `model_copy(update=...)` e ser revalidados. No `argparse`, usar um parser de inteiro positivo. Validar todos os caminhos derivados depois de resolver a raiz do projeto.

### A15 — A organização por pastas é boa, mas faltam duas fronteiras e `pipeline.py` faz demais

**Problema.** `agentes/`, `gates/`, `ferramentas/`, `llm/` e `observabilidade/` expressam responsabilidades úteis, porém dependências são instanciadas dentro dos gates e a orquestração também persiste, executa Cypress, preserva schema e monta telemetria. `contratos.py` mistura domínio, DTOs de scripts, superfície e observabilidade.

**Evidência.** `src/orquestrador/pipeline.py` tem 606 linhas e concentra preparação (`:146-173`), persistência (`:224-328`), loop (`:332-383`), Cypress/cobertura (`:385-416`), grafo (`:420-531`) e execução (`:533-606`). `src/orquestrador/contratos.py` tem 702 linhas. Gates criam adaptadores concretos em `src/orquestrador/gates/gate_a.py:40` e `src/orquestrador/gates/gate_b.py:35-44`. O executor também escreve arquivos em `src/orquestrador/agentes/executor.py:110-143`.

**Impacto.** Testar uma política exige montar I/O real; alterações de persistência ou ferramenta vazam para o fluxo. A tendência natural será aumentar o pipeline até ele virar um segundo framework interno.

**Correção sugerida.** Manter as pastas atuais e adicionar apenas fronteiras que pagam o custo: `artefatos/repositorio.py` para transação, hashes e versões; `politicas/privacidade.py` e `politicas/custo.py`; `adaptadores/` para OpenRouter, `qa-api` e Graphify. Deixar `aplicacao/pipeline.py` controlar estados e chamar portas injetadas. Separar `contratos.py` em domínio, contrato da skill e observabilidade quando houver uma mudança funcional, sem uma refatoração “big bang”. Não criar interface/repositório para cada função pura.

### A16 — A suíte cobre o caminho conhecido, mas não as falhas que importam ao produto

**Problema.** Há boa cobertura de unidades e do dry-run, porém faltam classes de defeito nas fronteiras. Alguns testes acoplam-se a métodos privados e outros exigem comportamentos perigosos.

**Evidência.** `tests/test_dry_run.py:41-47` pula integração se Node ou a skill não estiverem disponíveis; `pyproject.toml:82-90` não declara markers nem `--strict-markers`. `tests/test_loop_reparo.py` chama `_ciclo` diretamente e verifica detalhes de montagem; testes de ferramentas verificam listas renderizadas exatas. `tests/test_gate_cobertura.py:139-150` exige fail-open; `tests/test_falhas_isoladas.py:226-238` exige deleção. A execução integral atual passa em 4,97 s, o que é positivo, mas mostra que quase toda a suíte é isolada/simulada.

**Impacto.** A suíte pode continuar verde diante de quebra da skill, wheel incompleto, travessia de caminho, relatório Cypress velho, perda de arquivo ou mudança do provedor. Testes estruturais tornam refatoração legítima cara sem aumentar confiança.

**Correção sugerida.** Separar `unit`, `integration` e `e2e` com markers estritos. Manter unidades rápidas em Python 3.12/3.13; criar job Windows obrigatório de integração em 3.13 com Node 24 e referência exata da skill, falhando se houver skip inesperado. Adicionar testes de contrato para códigos/JSON, wheel limpo, rollback/sobrescrita/stale files, bloco 0, Cypress não zero/relatório antigo, 401/429/5xx/timeouts, privacidade e linguagens suportadas. Usar testes baseados em propriedade só nas fronteiras combinatórias e goldens para artefatos canônicos. Um smoke real de LLM, manual ou noturno, deve ter orçamento mínimo e nunca ser requisito de todo PR.

### A17 — O parser de superfície restringe silenciosamente a matriz de suporte

**Problema.** A extração da superfície Cypress usa um parser lexical/regex caseiro e aceita apenas extensões JavaScript. Isso não sustenta uma promessa ampla de frameworks e sintaxes.

**Evidência.** `src/orquestrador/superficie.py:35` reconhece `.js/.mjs/.cjs`. `src/orquestrador/javascript.py:44-55` implementa tokenização/remoção de comentários e strings por heurística. Os testes exercitam formas conhecidas, mas não constituem uma gramática.

**Impacto.** TypeScript, aliases, chamadas encadeadas, templates e sintaxe nova podem ser omitidos ou interpretados incorretamente, gerando falso resultado do Gate B.

**Correção sugerida.** Declarar por enquanto “Cypress em JavaScript” como suporte formal. Substituir a heurística por parser AST determinístico executado em Node, cobrindo JavaScript e depois TypeScript, com corpus golden de construções suportadas/não suportadas. Não usar LLM para decidir a superfície. Para backends, criar adaptadores explícitos por linguagem/framework e falhar com mensagem clara fora da matriz.

### A18 — README e comentários já apresentam sinais de deriva

**Problema.** O README tenta ser arquitetura, histórico, manual e status. Comentários/docstrings repetem fase e implementação; alguns já estão desatualizados.

**Evidência.** `README.md:12-15` e `:313` descrevem prompts como conteúdo futuro/placeholders, embora `prompts/mapeador.md` e `prompts/executor.md` já sejam extensos; só `prompts/auditor.md` permanece placeholder. `src/orquestrador/llm/montagem.py:9,43` e `src/orquestrador/agentes/mapeador.py:8,310` repetem a fase antiga. O README também concentra instalação, árvore, princípios, stubs e decisões (`README.md:357`, `:454`, `:469-476`).

**Impacto.** Agentes futuros obedecerão informação velha com muita convicção. Quanto mais o código for escrito por IA, mais duplicação narrativa vira fonte de regressão.

**Correção sugerida.** Reduzir o README a proposta, quickstart, arquitetura em uma página, matriz de suporte e limitações atuais. Registrar decisões duráveis em ADRs curtos: seis princípios; autoridade/fail-closed dos gates; transação de artefatos; privacidade/roteamento; contrato com `qa-api`; orçamento. Docstrings devem explicar responsabilidade e invariantes atuais, não “Fase 1/2”; comentários locais explicam o porquê de uma escolha não óbvia. O estado de implementação deve estar em issues/roadmap versionado, não repetido em cinco arquivos.

### A19 — A execução ainda não é reproduzível o bastante para suporte

**Problema.** O JSONL registra chamadas, mas falta um manifesto imutável da execução com versões e hashes determinantes.

**Evidência.** `src/orquestrador/cli.py:160-167` registra caminhos e início, mas não versão do aplicativo, commit da skill, commits dos dois repositórios, hashes dos prompts/configuração/artefatos ou rota efetiva. `src/orquestrador/observabilidade/registro.py:50-58` escreve eventos sem versão explícita do schema.

**Impacto.** Um chamado “ontem passou, hoje falhou” não pode ser reproduzido. Alteração editorial de prompt ou atualização da skill fica invisível; logs futuros podem ficar ilegíveis após mudança de schema.

**Correção sugerida.** Criar `manifesto-execucao.json` com `schema_version`, `run_id`, versão do orquestrador, Python/Node, commit/estado sujo do backend e testes, versão da skill, configuração redigida, hashes dos prompts e artefatos, modelos configurados, provedor/rota efetivos, request IDs, tokens e custo. Logs devem ser sem código-fonte por padrão, com política de retenção e exportação opt-in.

### A20 — Preservar schema do cliente é correto, mas o estado final precisa dizer “requer revisão”

**Problema.** O pipeline evita sobrescrever schemas preexistentes, mas uma divergência conhecida vira aviso e o recurso pode terminar como sucesso completo.

**Evidência.** `src/orquestrador/pipeline.py:288-328` preserva o schema e registra campos emitidos que não existem no arquivo mantido, sem transformar isso em estado impeditivo.

**Impacto.** A proteção contra sobrescrita é boa, mas o usuário pode receber specs incompatíveis com o contrato mantido e interpretar o resultado como final.

**Correção sugerida.** Manter a preservação. Produzir um diff de schema legível por máquina e encerrar como `REQUER_REVISAO`, distinto de aprovado/reprovado, até o cliente aceitar ou ajustar o contrato. Nunca atualizar automaticamente schema existente para satisfazer teste gerado.

## 3. Ferramentas

Recomendação geral: adotar uma cadeia pequena e ortogonal. `uv` resolve ambiente/lock/auditoria; Ruff resolve formato/lint; Pyright resolve contratos; pytest/coverage mede comportamento; pre-commit só antecipa verificações rápidas. O restante deve justificar uma classe de risco específica.

| Ferramenta | Adotar? | Por quê | Esforço | Configuração inicial concreta |
|---|---:|---|---:|---|
| **uv + `uv.lock`** | **Sim, agora** | Dá resolução reproduzível, sincronização congelada, build e execução uniformes no Windows. O lock deve ser commitado. A documentação define que `uv sync --frozen` não atualiza o lock ([uv projects](https://docs.astral.sh/uv/concepts/projects/sync/)). | Baixo | Mover dependências de desenvolvimento para `[dependency-groups] dev`; manter em `[project.dependencies]` apenas dependências diretas de runtime com intervalos compatíveis. Rodar `uv lock`, versionar `uv.lock`; CI: `uv lock --check` e `uv sync --frozen --all-groups`. Não listar transitivas manualmente. |
| **Ruff lint** | **Sim, agora** | Alto retorno em código gerado por IA: imports esquecidos, exceções frágeis, mutáveis, segurança básica, `pathlib`, argumentos não usados e modernização. É linter único e rápido ([Ruff linter](https://docs.astral.sh/ruff/linter/)). | Baixo–médio | `target-version="py312"`, `line-length=100`, `src=["src","tests"]`; selecionar `E4,E7,E9,F,I,UP,B,C4,PIE,RUF,S,PTH,ARG`; em `tests/**`, ignorar só `S101`. Corrigir violações reais e usar ignores locais com justificativa. Não habilitar `ALL` nem `SIM` de início: geram ruído/churn em estilo. |
| **Ruff format** | **Sim, agora** | Elimina discussão de estilo e substitui Black/isort; o formatter é compatível com o ecossistema e integra com o linter ([Ruff formatter](https://docs.astral.sh/ruff/formatter/)). | Baixo | `[tool.ruff.format] line-ending="lf"`; CI: `uv run ruff format --check .`; local/pre-commit: `uv run ruff format .`. Manter LF no repositório mesmo no Windows e adicionar `.gitattributes` se necessário. |
| **Pyright** | **Sim, em seguida** | O risco de tipo está nas fronteiras e no estado do LangGraph. Pyright pega APIs/campos inventados por IA antes do runtime e tem configuração strict granular ([configuração](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)). | Médio | `[tool.pyright] include=["src","tests"]`, `exclude=[".venv",".execucoes"]`, `pythonVersion="3.12"`, `pythonPlatform="Windows"`, `typeCheckingMode="standard"`, `strict=["src"]`, `reportUnnecessaryTypeIgnoreComment="error"`. Corrigir `src` sem baseline de ignores; migrar testes para strict depois. |
| **pytest-cov / Coverage.py** | **Sim, agora** | Os 178 casos não mostram quais ramos críticos nunca executam. Branch coverage é mais útil que contagem de linhas. pytest-cov permite configuração central ([pytest-cov](https://pytest-cov.readthedocs.io/en/latest/config.html), [Coverage.py](https://coverage.readthedocs.io/en/latest/config.html)). | Baixo | `[tool.coverage.run] branch=true, source_pkgs=["orquestrador"]`; `[tool.coverage.report] fail_under=85, precision=1, show_missing=true, skip_covered=true`. CI: `pytest --cov --cov-report=term-missing:skip-covered`. Começar em 85% e ratchetar sem redução até 90%; não perseguir 100%. |
| **pre-commit** | **Sim, mínimo** | Feedback antes do push sem transformar cada commit numa mini-CI ([pre-commit](https://pre-commit.com/)). | Baixo | Hooks oficiais para whitespace/EOF/TOML/YAML/merge conflict e hooks locais `uv run --frozen ruff check --fix` + `uv run --frozen ruff format`. Não rodar pytest completo, Pyright, download, auditoria ou integração no hook. CI continua soberana. |
| **GitHub Actions (Windows)** | **Sim, bloqueador de merge** | O produto assume Windows; CI só em Linux não testa paths, `.cmd`, encoding e subprocessos reais. | Médio | Jobs: (1) unit/lint/type em `windows-latest`, Python 3.12 e 3.13; (2) integração obrigatória em 3.13 + Node 24 + skill em ref exata, proibindo skips inesperados; (3) build de wheel e instalação em ambiente limpo. Cache do uv, `uv sync --frozen`, Ruff, Pyright e pytest com cobertura. Linux pode ser informativo, não substituto. |
| **`uv audit`** | **Sim, CI** | O uv atual já audita o lock contra vulnerabilidades; evita adicionar outra ferramenta para o mesmo papel ([`uv audit`](https://docs.astral.sh/uv/reference/cli/#uv-audit)). | Baixo | Fixar uma versão de uv com suporte a audit (>= 0.12 no workflow); rodar `uv audit --frozen` em PR e agenda semanal. Exceções devem ter CVE/GHSA, justificativa, responsável e expiração. Não adicionar `pip-audit` enquanto isso cobrir a necessidade. |
| **Hypothesis** | **Sim, seletivo** | Excelente para espaços combinatórios onde exemplos manuais falham: caminhos Windows, normalização de endpoint, extração JSON e invariantes de gates ([Hypothesis](https://hypothesis.readthedocs.io/en/latest/)). | Médio | Usar apenas em `NomeDeRecurso`/confinamento, normalização de rotas, parser de resposta e propriedade “prompt de reparo nunca contém histórico”. Perfil CI determinístico, exemplos reproduzíveis e deadline desativado só quando necessário. Não converter toda a suíte. |
| **Golden/snapshot de artefatos** | **Sim, sem biblioteca inicialmente** | Manifestos, deltas, prompts renderizados e run manifests são contratos de produto; aprovação explícita captura mudanças editoriais/estruturais. | Baixo–médio | Criar `tests/golden/`; normalizar path, newline, timestamp, UUID, PID e ordem; comparar texto/JSON canônico. Atualização só por flag/comando explícito e diff revisável. Não autoatualizar no teste e não snapshotar logs inteiros. |
| **Build/release de wheel** | **Sim, agora** | O modo editável esconde ausência de package data. Uma release só é válida se o artefato instalado funcionar. | Médio | `uv build`; instalar o wheel num ambiente temporário vazio e rodar `orquestrador --help`, `orquestrador init` e smoke dry-run empacotado. Tag deve coincidir com versão do pacote. Para publicar, usar Trusted Publishing em vez de token persistente ([PyPA](https://packaging.python.org/en/latest/guides/tool-recommendations/)). |
| **SemVer + `CHANGELOG.md`** | **Sim** | Há três contratos versionáveis: CLI/configuração, artefatos JSON e compatibilidade com a skill. | Baixo | SemVer manual; `schema_version` independente nos artefatos; changelog no formato Added/Changed/Fixed/Security; release por tag. Não adotar Commitizen/release bot antes de haver cadência regular. |
| **Scanner de segredos** | **Sim, CI** | O produto lida com chave de API e lê repositórios de clientes; lint não cobre segredo commitado. | Baixo | Ativar secret scanning do host ou Gitleaks em CI com baseline pequeno e exceções justificadas. Complementar com scanner runtime antes do envio ao LLM; são problemas diferentes. |
| **Markers do pytest** | **Sim** | Evita uma integração silenciosamente pulada e torna o contrato da CI legível. | Baixo | Declarar `unit`, `integration`, `e2e` em `pyproject.toml`; adicionar `--strict-markers`. Job de integração deve selecionar explicitamente o marker e falhar se não coletar testes. |
| **Black, isort, Flake8, Bandit** | **Não** | Duplicam Ruff e criam conflitos de regra/formatador. | Zero | Não configurar. Se uma regra específica de segurança faltar, avaliar primeiro plugin/regra Ruff ou um teste direcionado. |
| **mypy** | **Não** | Duplica Pyright e divide a fonte de verdade de tipagem. | Zero | Um type checker apenas. Escolher Pyright pelo feedback rápido e modo strict por diretório. |
| **Poetry ou PDM** | **Não** | Duplicam gestão de projeto/lock do uv sem benefício claro aqui. | Zero | Padronizar os comandos em uv. |
| **tox/nox** | **Não, por enquanto** | Com um SO-alvo, dois Pythons e poucos comandos, a matriz da CI + uv é suficiente. | Zero | Reavaliar apenas se a matriz local virar difícil de reproduzir ou houver múltiplos ambientes de release. |
| **SonarQube, mutation testing, cobertura 100%** | **Não, por enquanto** | Alto custo de operação e tendência a otimizar métrica antes de corrigir riscos conhecidos. | Zero | Primeiro gates fail-closed, transações, privacidade e testes de fronteira. Mutação pode ser um experimento futuro apenas em normalizadores/gates puros. |
| **Framework dedicado de snapshots** | **Não, inicialmente** | A quantidade de goldens prevista não justifica dependência e convenções extras. | Zero | JSON/texto canônico + pytest bastam; reavaliar se diffs/atualizações manuais virarem gargalo. |

Configurações-base sugeridas, reunidas para evitar ambiguidade:

```toml
[tool.ruff]
target-version = "py312"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "UP", "B", "C4", "PIE", "RUF", "S", "PTH", "ARG"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.ruff.format]
line-ending = "lf"

[tool.pyright]
include = ["src", "tests"]
exclude = [".venv", ".execucoes"]
pythonVersion = "3.12"
pythonPlatform = "Windows"
typeCheckingMode = "standard"
strict = ["src"]
reportUnnecessaryTypeIgnoreComment = "error"

[tool.coverage.run]
branch = true
source_pkgs = ["orquestrador"]

[tool.coverage.report]
fail_under = 85
precision = 1
show_missing = true
skip_covered = true

[tool.pytest.ini_options]
addopts = "-q --strict-markers"
markers = [
  "unit: teste isolado, sem processo externo ou rede",
  "integration: usa Node, skill externa ou filesystem integrado",
  "e2e: executa o fluxo instalado ponta a ponta",
]
```

## 4. Conteúdo proposto para `AGENTS.md`

Este arquivo deve ser a fonte canônica das regras para todos os agentes. Ele não deve copiar o README inteiro nem conter estado temporário de sprint.

```markdown
# AGENTS.md

## Propósito

Este repositório implementa um orquestrador multi-agente em Python, LangGraph e
OpenRouter que gera suítes Cypress de API a partir de um backend. O objetivo
central é enumerar e provar cobertura por verificadores determinísticos, não
confiar na autoavaliação de um LLM.

Este arquivo é a fonte de verdade para agentes de IA. Regras específicas de uma
ferramenta podem importar este arquivo, mas não devem duplicar suas invariantes.

## Antes de alterar qualquer coisa

1. Leia o `README.md` da raiz por inteiro.
2. Leia `pyproject.toml`, `config.toml` e os módulos/testes relacionados à tarefa.
3. Execute `git status --short` e preserve alterações preexistentes do usuário.
4. Entenda o contrato da skill `qa-api` antes de alterar um adaptador.
5. Faça a menor mudança que satisfaz o comportamento pedido.

## Invariantes arquiteturais — não quebrar

1. O handoff entre estágios é um artefato versionado em disco. O consumidor deve
   reabrir e validar o artefato; não passe histórico de conversa nem dependa apenas
   do objeto que continuou em memória.
2. Todo prompt de reparo é exatamente:
   `instrução_fixa + artefato_atual + delta.violacoes`.
   Não inclua mensagens anteriores, resumos de tentativa, raciocínio, respostas
   antigas ou qualquer outro histórico. O artefato atual deve ser canônico e o
   delta deve conter somente as violações da tentativa corrente.
3. Agentes são stateless entre unidades de trabalho. Estado durável pertence a
   artefatos em disco, nunca à memória conversacional do modelo.
4. Quem aprova ou reprova cobertura é código determinístico. LLM cria; LLM não
   decide se a cobertura está completa e não corrige/verifica o próprio gabarito.
5. O auditor semântico fica fora do loop quente. Não o transforme em gate de cada
   tentativa nem use sua opinião para substituir um verificador determinístico.
6. Nenhum nome de modelo pode aparecer em código-fonte ou prompt. Modelos,
   provedores e parâmetros vêm de configuração.

Se uma tarefa exigir contrariar uma invariável, pare e exponha o conflito. Não a
contorne silenciosamente.

## Fronteiras e responsabilidades

- `agentes/`: criação de artefatos; não decide aprovação.
- `gates/`: validação determinística, sem chamadas de LLM.
- `ferramentas/` e `adaptadores/`: filesystem, subprocessos e contratos externos.
- `llm/`: cliente, structured output e montagem canônica de prompts.
- `observabilidade/`: eventos, métricas e manifests sem código-fonte/segredos por
  padrão.
- `pipeline`: coordena estados; não deve concentrar persistência, política de
  privacidade, cálculo de custo ou parsing específico de ferramenta.
- Funções puras recebem dados e devolvem dados. I/O deve ficar explícito na borda.
- Falha de ferramenta/provedor é erro operacional, não violação para reparo do LLM.
- Gates falham fechados: ausência ou inconsistência de evidência nunca aprova.

## Artefatos e filesystem

- Grave candidatos em staging, valide e publique de forma atômica.
- Nunca sobrescreva ou apague arquivo preexistente do cliente sem política de
  propriedade, hash anterior e recuperação/rollback.
- Confine todo caminho à raiz permitida após `Path.resolve()`; não use comparação
  textual por prefixo. Considere regras e nomes reservados do Windows.
- Artefatos persistidos têm `schema_version`, serialização canônica e hash.
- Não deixe arquivos de uma tentativa anterior participarem da tentativa atual.

## Skill externa `qa-api`

- A skill está em outro repositório e é somente leitura para este projeto.
- Nunca modifique, formate, mova, versione ou faça commit em arquivos da skill.
- Invoque os scripts `.mjs` por subprocesso através dos adaptadores existentes.
- Mudança de CLI, JSON, código de saída ou versão exige teste de contrato e
  atualização explícita da matriz de compatibilidade.

## Segurança, privacidade e custo

- Nunca leia ou envie `.env`, chaves privadas, tokens, credenciais ou arquivos
  excluídos pela política do projeto ao LLM.
- Redija segredos de logs, erros, fixtures e eventos.
- Não propague a chave do provedor de LLM para Node, Cypress ou formatadores.
- Não faça chamada externa em teste unitário.
- Respeite allowlist de provedor, retenção e orçamento. Não introduza fallback que
  amplie o compartilhamento de dados ou o custo sem configuração explícita.

## Idioma, comentários e documentação

- Código, nomes de domínio, mensagens, testes, comentários e documentação ficam em
  português. Nomes exigidos por bibliotecas/protocolos externos podem permanecer no
  idioma original.
- Docstrings explicam responsabilidade, contrato e invariantes atuais.
- Comentários explicam o porquê de decisões não óbvias; não narram a linha seguinte.
- Não use comentários de fase (`Fase 1`, `futuro`, `placeholder`) como fonte de
  verdade. Decisões duráveis pertencem a ADRs; status temporário pertence ao
  roadmap/issues.
- `prompts/` é conteúdo editorial versionado. Não reformule prompts como efeito
  colateral de uma mudança de código. Mudança de prompt exige golden/diff próprio.

## Tipagem e tratamento de erros

- `Any` é permitido na borda de JSON de terceiro e deve ser convertido logo para
  DTO/modelo validado. Não use `Any` para esconder modelo, estado, callback ou
  registrador.
- Preserve a causa com `raise ... from erro` e use a taxonomia de erros do projeto.
- Não capture `Exception` para aprovar, continuar silenciosamente ou fabricar saída.
- Valide configuração na criação; overrides também devem ser revalidados.

## Dependências

- Use `uv` e mantenha `uv.lock` versionado.
- Adicione apenas dependências diretas justificadas. Não edite transitivas à mão.
- Não introduza segundo formatter, linter, type checker ou gerenciador de ambiente.
- Python suportado: 3.12 e 3.13. Ambiente principal: Windows e Node 24. Não adicione
  Docker como requisito.

## Verificação

Quando `uv.lock` existir, prefira os comandos congelados:

```powershell
uv sync --frozen --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

No estado anterior à adoção do lock, use:

```powershell
python -m pytest
python -m orquestrador --dry-run --recurso pedidos
```

- Testes unitários não dependem de Node, skill ou rede.
- Integração com Node/skill deve ter marker explícito e não pode ser pulada no job
  obrigatório da CI.
- O dry-run é um simulador do mesmo pipeline, não prova compatibilidade com provedor
  real e não substitui o teste de wheel instalado.
- Toda correção de bug inclui teste que falha antes e passa depois.

## Conclusão da tarefa

Antes de encerrar:

1. Revise `git diff` e confirme que só arquivos da tarefa mudaram.
2. Rode as verificações proporcionais ao risco e relate comandos/resultados.
3. Verifique que nenhuma invariável, segredo ou arquivo externo foi afetado.
4. Atualize documentação/ADR somente se o contrato atual mudou.
5. Não faça commit, push, release ou alteração fora do repositório sem pedido
   explícito do usuário.
```

## 5. Conteúdo proposto para os arquivos em `.claude/`

A divisão correta é simples: `AGENTS.md` contém toda regra de engenharia e arquitetura; `.claude/CLAUDE.md` apenas importa essa fonte e documenta comportamento específico do Claude Code. Regras por caminho em `.claude/rules/` só devem existir quando forem realmente exclusivas daquele caminho, e jamais repetir os seis princípios. O Claude Code suporta importação com `@caminho`, inclusive relativa ao arquivo que importa ([documentação de memória](https://code.claude.com/docs/en/memory)).

Não recomendo criar vários arquivos agora. Criaria/ajustaria somente os dois abaixo.

### `.claude/CLAUDE.md`

```markdown
@../AGENTS.md

# Regras específicas do Claude Code

O `AGENTS.md` importado acima é a fonte canônica de arquitetura, segurança,
qualidade e verificação. Não copie suas regras para este arquivo. Se houver conflito,
pare e peça esclarecimento em vez de escolher silenciosamente uma versão.

- Use skills apenas quando a tarefa as exigir e leia o `SKILL.md` inteiro antes.
- Não crie regras em `.claude/rules/` para repetir conteúdo de `AGENTS.md`.
- Uma regra em `.claude/rules/` deve ter escopo de caminho explícito e tratar apenas
  comportamento específico do Claude Code naquele caminho.
- Não execute a skill de push por iniciativa própria. Commit e push exigem pedido
  explícito do usuário.
- Antes de concluir, confira o diff e informe verificações executadas e limitações.
```

### `.claude/skills/push/SKILL.md`

O arquivo atual é perigoso porque manda “commitar tudo” e usa `git add -A` (`.claude/skills/push/SKILL.md:8-9,33`). Em um working tree compartilhado com mudanças preexistentes, isso mistura autoria e pode publicar trabalho do usuário. Substituição proposta:

```markdown
---
name: push
description: Cria commit e envia somente as alterações da tarefa atual, quando o usuário pedir explicitamente.
disable-model-invocation: true
---

# Commit e push seguros

Use esta skill somente quando o usuário pedir explicitamente commit e push. A
invocação autoriza essas duas ações apenas para os arquivos da tarefa atual; não
autoriza rebase, force push, limpeza do working tree ou inclusão de mudanças alheias.

## Procedimento

1. Leia o `AGENTS.md` da raiz e obedeça a todas as suas regras.
2. Execute `git status --short`, identifique branch/upstream e inspecione `git diff`
   e `git diff --staged`.
3. Liste os caminhos alterados pela tarefa atual. Preserve toda mudança preexistente
   ou não relacionada, inclusive arquivos não rastreados.
4. Nunca use `git add -A`, `git add .` ou glob amplo. Faça stage somente dos caminhos
   explícitos da tarefa com `git add -- <caminho1> <caminho2> ...`.
5. Se não for possível distinguir com segurança a autoria de uma alteração, pare e
   peça ao usuário que escolha; não presuma que ela pertence ao commit.
6. Rode as verificações exigidas pelo `AGENTS.md`. Não use `--no-verify`.
7. Revise `git diff --cached --check` e `git diff --cached`. Confirme que não há
   segredo, arquivo externo, artefato temporário ou mudança fora do escopo.
8. Crie um único commit com mensagem imperativa, em português, assunto de até 72
   caracteres e corpo apenas se necessário para explicar o porquê.
9. Envie com `git push origin HEAD`, usando o upstream existente quando aplicável.
10. Nunca use `--force`, `--force-with-lease`, rebase, reset destrutivo ou alteração
    de histórico sem um pedido novo e explícito.
11. Informe hash do commit, branch, destino do push e verificações executadas.

Se hooks ou push falharem, preserve o estado, diagnostique e relate. Não contorne a
proteção nem inclua arquivos adicionais para “fazer passar”.
```

Não criaria neste momento `.claude/rules/arquitetura.md`, `.claude/rules/testes.md` ou equivalentes: seriam cópias concorrentes do `AGENTS.md`. Se no futuro houver regras genuinamente locais, por exemplo “alterações em `prompts/**` exigem atualizar um golden”, ainda prefiro manter a regra geral em `AGENTS.md`; um arquivo por caminho só deve acrescentar instruções de operação exclusivas do Claude.

## 6. Roteiro priorizado

**Critério de priorização.** Primeiro, risco de falso sucesso, perda/vazamento de dados e irreversibilidade; segundo, fechamento da promessa determinística central; terceiro, reprodutibilidade de build/suporte; quarto, eficiência e expansão de mercado. Número de arquivos ou facilidade de implementação não deve superar esses critérios.

### 1. Fechar os caminhos perigosos antes de qualquer expansão

- Fazer bloco 0 e gates falharem fechados; introduzir `ERRO_DA_FERRAMENTA` e corrigir `ResultadoGate.combinar`.
- Tratar Cypress não zero e relatório ausente/antigo como falha; não declarar runtime validado quando não executado.
- Desabilitar remoção insegura e implementar staging, diário de propriedade, escrita atômica e rollback.
- Validar/confinar recurso e caminhos no Windows.
- Impedir leitura/envio de segredos e remover chave/tracing do ambiente dos subprocessos.
- Criar testes de regressão para cada caso antes da implementação.

**Critério de saída:** nenhum erro, evidência ausente ou arquivo residual produz “aprovado”; uma falha no meio preserva byte a byte os arquivos preexistentes; corpus de traversal/segredos passa.

### 2. Tornar o disco e os contratos externos fontes reais de verdade

- Introduzir `RepositorioDeArtefatos`, schemas versionados, hashes e reidratação entre estágios.
- Corrigir o bundle de reparo sem violar a fórmula fixa; adicionar teste que garante ausência de histórico.
- Versionar o contrato `qa-api`, criar matriz de compatibilidade e `orquestrador doctor`.
- Criar `manifesto-execucao.json` com versões, commits, prompts, configuração redigida e hashes.

**Critério de saída:** é possível interromper e retomar uma execução usando apenas artefatos em disco; alterar/corromper um artefato ou skill é detectado antes do próximo estágio.

### 3. Implementar a prova determinística para uma matriz de suporte pequena

- Implementar diff grafo × manifesto por adaptadores e normalização determinística.
- Declarar inicialmente as combinações de backend realmente suportadas e Cypress JavaScript.
- Substituir parser lexical por AST Node para JavaScript/TypeScript quando TypeScript entrar na matriz.
- Para linguagem/framework não suportado, falhar com diagnóstico; não tentar “universalizar” via prompt.
- Manter auditor semântico fora do loop e só implementá-lo depois que o denominador determinístico estiver sólido.

**Critério de saída:** para cada combinação anunciada, fixtures positivas e negativas provam que endpoint/operação ausente reprova; combinação não anunciada nunca termina como cobertura completa.

### 4. Instalar o piso de engenharia e CI

- Adotar uv/lock, Ruff, Pyright strict em `src`, coverage de ramos, markers e pre-commit mínimo.
- Criar CI Windows 3.12/3.13, integração obrigatória com Node 24/skill exata, `uv audit` e scanner de segredos.
- Adicionar Hypothesis nos normalizadores/confinamento e goldens canônicos para artefatos/prompts.
- Corrigir comentários/README desatualizados e criar os ADRs essenciais.

**Critério de saída:** PR não integra com lock divergente, tipo/lint falho, cobertura abaixo do piso, vulnerabilidade não excepcionada, segredo detectado ou integração pulada.

### 5. Transformar checkout de desenvolvimento em produto instalável

- Empacotar prompts; criar `init`, `doctor`, `plan`/estimativa e UX de configuração.
- Construir e instalar wheel limpo em toda release; versionar CLI, schemas e changelog.
- Definir armazenamento de chave no Windows e modo `.env` apenas como opção de desenvolvimento.
- Publicar matriz de compatibilidade e política de atualização da skill.

**Critério de saída:** uma máquina Windows limpa instala o wheel, configura dois repositórios e executa o dry-run sem conhecer a estrutura do checkout do orquestrador.

### 6. Colocar custo, privacidade e suporte em nível comercial

- Orçamento rígido por execução/recurso, estimativa em faixa, limites de chamadas/bytes/tempo e accounting real.
- Política ZDR/allowlist/fallback, consentimento explícito e relatório de quais arquivos/fornecedores receberão dados.
- Telemetria local por padrão, remota opt-in, sem código-fonte, com retenção definida.
- Run bundle redigido para suporte e reprodução.

**Critério de saída:** antes de enviar código, o cliente sabe faixa de custo e destinos; depois, consegue auditar custo/rota sem expor o código nos logs.

### 7. Expandir frameworks apenas a partir de corpus e demanda

- Adicionar um adaptador por vez, com projetos-fixture reais, golden de grafo e falsos negativos conhecidos.
- Medir precisão/completude do extrator e tempo/custo por porte de projeto.
- Só então avaliar paralelismo entre recursos, auditor semântico e otimizações de throughput.

**Critério de saída:** cada nova combinação tem contrato, fixtures, limite conhecido e observabilidade; “suporta várias linguagens” nunca significa fallback silencioso para julgamento do LLM.

## 7. O que não fazer

- **Não vender “cobertura completa” enquanto Gate A estiver stub ou fail-open.** Chamar de preview técnico é honesto; maquiar com aviso não é.
- **Não colocar um LLM para julgar a completude ou validar a saída de outro LLM.** Isso reintroduz o problema original e viola o princípio 4.
- **Não mover o auditor semântico para o loop quente.** Ele aumenta custo/latência e não substitui um denominador determinístico.
- **Não acrescentar histórico, resumo de tentativas ou raciocínio ao prompt de reparo.** Corrigir a seleção do artefato atual, não abandonar a fórmula linear.
- **Não tornar agentes stateful entre recursos.** Cache técnico imutável é aceitável; memória conversacional como contrato não é.
- **Não modificar, vendorizar ou “corrigir” a skill `qa-api`.** Fixar e testar a compatibilidade do consumidor; mudanças na skill pertencem ao outro repositório.
- **Não hardcodar nome de modelo, provedor ou preço no código.** Tudo configurável e registrado; defaults pertencem a configuração versionada.
- **Não manter aprovação quando relatório/script está ausente.** Indisponibilidade é erro operacional, não sucesso degradado.
- **Não sobrescrever/apagar arquivo do cliente diretamente.** Sem staging, ownership, hash e rollback, a opção deve ficar desabilitada.
- **Não usar comparação textual de caminhos nem aceitar recurso como path.** Centralizar confinamento com semântica Windows.
- **Não prometer suporte universal por colocar mais instruções no prompt.** Suporte é uma matriz de adaptadores determinísticos e fixtures.
- **Não paralelizar escrita por recurso agora.** Antes é preciso isolamento de diretórios, ownership e publicação transacional; paralelismo precoce só torna corrupção intermitente.
- **Não adotar banco, fila, microserviços, Kubernetes ou Docker nesta fase.** Artefatos locais versionados atendem a arquitetura e o ambiente declarado; operação distribuída ainda não resolve o gargalo principal.
- **Não criar uma abstração/interface para cada função.** Adicionar somente as fronteiras de artefato, política e adaptador que isolam I/O ou risco real.
- **Não empilhar Ruff + Black + isort + Flake8 + Bandit, nem Pyright + mypy.** Uma ferramenta por responsabilidade reduz divergência e custo cognitivo.
- **Não adicionar Poetry/PDM, tox/nox, SonarQube ou mutation testing por checklist.** uv + CI explícita cobrem o estágio atual; reavaliar quando houver dor medida.
- **Não perseguir 100% de cobertura nem snapshotar tudo.** Cobrir ramos de risco e contratos; métricas perfeitas incentivam testes estruturais frágeis.
- **Não rodar toda a suíte, Pyright ou auditoria de rede em pre-commit.** Hooks lentos são ignorados; a CI faz a verificação completa.
- **Não autoatualizar goldens.** Mudança de artefato ou prompt precisa aparecer como diff editorial revisável.
- **Não duplicar invariantes em `AGENTS.md`, `CLAUDE.md`, README e comentários.** `AGENTS.md` é a regra canônica; `CLAUDE.md` importa; ADR explica a decisão; README ensina o uso.
- **Não usar `git add -A` em uma skill de push.** Agentes compartilham working tree e não podem atribuir a si mudanças preexistentes.
- **Não coletar telemetria remota por padrão nem registrar código-fonte.** Opt-in, minimização e retenção explícita são requisitos de confiança, não melhorias futuras.
- **Não fazer retries invisíveis ou ilimitados.** Toda nova tentativa consome orçamento e precisa ser atribuível.
- **Não atualizar schemas preexistentes para fazer o teste passar.** Produzir diff e estado `REQUER_REVISAO`; o contrato do cliente tem precedência.
