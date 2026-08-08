# CLI

Três formas de invocação:

```bash
orquestrador init                                # escreve o config.toml do projeto
orquestrador doctor                              # diagnostica o ambiente
orquestrador --dry-run --recurso pedidos         # execução sem chamar modelo
```

`python -m orquestrador ...` é equivalente.

## Códigos de saída

O contrato com quem automatiza. Um pipeline que sai com 3 e um que sai com 1 pedem
coisas diferentes de quem agenda a execução.

<!-- INICIO DOS CODIGOS DE SAIDA: gerado por cli/codigos_de_saida.py -->
| Código | Significado |
| --- | --- |
| `0` | todo recurso pedido terminou aprovado |
| `1` | ao menos um recurso foi reprovado por um gate ou falhou num estágio |
| `2` | erro de quem invocou ou do ambiente: configuração inválida, comando inexistente, ferramenta indisponível |
| `3` | publicado, e alguma coisa precisa de olho humano — hoje, schema do consumidor que declara menos campos do que o backend tem |
| `4` | o provedor de LLM não respondeu dentro da política de retentativa. Esperar e repetir é a resposta certa |
<!-- FIM DOS CODIGOS DE SAIDA -->

Em CI, o que importa é **não ser 0**: zero é indistinguível de trabalho feito.

## Argumentos

<!-- INICIO DA AJUDA: gerado por cli/principal.py::parse_args -->
```
usage: orquestrador [-h] [--config CONFIG] [--recurso RECURSOS] [--dry-run]
                    [--max-tentativas MAX_TENTATIVAS] [--rodar-cypress] [--auditor]
                    [--remover-reprovados]

Orquestrador multi-agente de testes de API (skill qa-api).

options:
  -h, --help            show this help message and exit
  --config CONFIG       arquivo de configuração
  --recurso RECURSOS    nome do recurso (repetível). No dry-run, o padrão é todos os
                        das fixtures.
  --dry-run             roda ponta a ponta sem chamar nenhum modelo, usando fixtures.
  --max-tentativas MAX_TENTATIVAS
                        sobrescreve max_tentativas de todos os gates (inteiro >= 1).
  --rodar-cypress       executa o Cypress no Bloco 3 (por padrão é pulado).
  --auditor             RECUSADO enquanto o auditor semântico for stub: encerra com
                        erro.
  --remover-reprovados  apaga o que ESTA ferramenta criou nos recursos que não
                        terminaram aprovados. Nunca toca em arquivo preexistente nem
                        em arquivo que mudou desde que o criamos.

comandos: `orquestrador init` escreve o config.toml do projeto; `orquestrador doctor`
diagnostica o ambiente. Sem comando, esta é a execução do pipeline.
```
<!-- FIM DA AJUDA -->

## `--dry-run`

Substitui **apenas a resposta do modelo**, por fixtures de `fixtures/roteiros/`.
Tools, scripts `.mjs`, gates e deltas são reais, e a escrita acontece numa sandbox
dentro do diretório da execução — o projeto do consumidor não é tocado.

Ele não prova compatibilidade com provedor real. Prova que o fluxo, os gates e o
loop de reparo funcionam.

## `--auditor`

Recusado com código diferente de zero enquanto o auditor for stub. Um comando que
encerra com 0 anunciando um veredito é, em CI, indistinguível de auditoria feita.
