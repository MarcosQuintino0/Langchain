# Tipos de evento no JSONL

O catálogo abaixo é **gerado** a partir de `TipoDeEvento`, em
`src/orquestrador/observabilidade/eventos.py`, e
`tests/test_observabilidade_eventos.py` reprova se ele divergir do enum.
Era uma lista mantida à mão, e ela divergiu duas vezes no mesmo dia — não edite a
tabela: edite o enum e regenere.

<!-- INICIO DO CATALOGO DE EVENTOS: gerado por observabilidade/eventos.py -->
| Evento | O que registra |
| --- | --- |
| `execucao_iniciada` | abertura: dry-run, recursos pedidos, arquivo de configuração e os dois repositórios |
| `manifesto_de_execucao` | onde o `manifesto-execucao.json` foi escrito e quais campos não puderam ser coletados |
| `pulso` | sinal periódico de que a execução continua viva |
| `operacao_iniciada` | abertura de uma operação correlacionada por trace e span |
| `operacao_concluida` | fechamento bem-sucedido de uma operação, com sua duração |
| `operacao_falhou` | fechamento com falha de uma operação, sem conteúdo sensível da exceção |
| `bloco0` | preparação determinística: se o `graph.json` ficou utilizável, e por quê |
| `superficie` | módulos compartilhados do projeto de testes e os exports que o executor pode importar |
| `estagio_tentativa` | uma tentativa de um estágio: tamanho da instrução fixa, da entrada e uso de tools |
| `chamada_llm` | uma chamada ao modelo: estágio, recurso, tentativa, modelo e tokens de entrada e saída |
| `requisicao_llm_iniciada` | uma requisição real ao provedor começou, com contexto da unidade de trabalho |
| `requisicao_llm_concluida` | uma requisição real ao provedor terminou, com uso, duração e motivo de parada |
| `requisicao_llm_falhou` | uma requisição real ao provedor falhou, com categoria e identificador de suporte |
| `tool` | uma chamada de tool do mapeador: ordem, argumentos, tamanho do retorno e erro |
| `gate` | veredito de um gate numa tentativa, com violações e avisos |
| `delta` | o delta enviado ao reparo: códigos de violação e tamanho do artefato atual |
| `artefatos` | arquivos que um estágio escreveu na área de staging da execução |
| `schemas_preservados` | schemas que já eram do consumidor e o mapeador não sobrescreveu |
| `schemas_divergentes` | campos que o mapeador achou no backend e o schema preservado não declara |
| `publicacao` | o que a publicação fez no projeto do consumidor, arquivo a arquivo, com hash e classificação |
| `artefatos_reprovados` | o que ficou em disco em estado reprovado, e se chegou a ser publicado |
| `staging_mantido` | o staging do recurso sobreviveu ao fim porque tem artefato para inspecionar |
| `cypress` | execução da suíte: código de saída e relatório desta execução, ou o motivo de não rodar |
| `processo` | subprocesso externo medido por código, duração, bytes, timeout e hash do comando |
| `cobertura` | contadores do `qa-cobertura.mjs` e se houve execução de runtime |
| `recurso_falhou` | o recurso terminou reprovado, com o motivo |
| `recurso_concluido` | desfecho do recurso: estado, tentativas e execução de testes |
| `telemetria` | agregados de token e de caracteres por estágio, recurso e tentativa |
| `execucao_interrompida` | o laço de recursos parou no meio por ferramenta indisponível; lista quem não rodou |
| `execucao_abortada` | a execução terminou sem veredito, com o motivo |
| `execucao_concluida` | fechamento: sucesso, interrupção e o resumo por recurso |
<!-- FIM DO CATALOGO DE EVENTOS -->
