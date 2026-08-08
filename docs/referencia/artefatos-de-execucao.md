# Artefatos de execução

Cada execução escreve em dois lugares, e a separação entre eles é a coisa mais
importante desta página: **um é nosso, o outro é do cliente**.

```mermaid
flowchart LR
    subgraph NOSSO[".execucoes/AAAAMMDD-HHMMSS-pid/ · nosso"]
        direction TB
        LOG["execucao.jsonl<br/>uma linha por evento"]
        MAN["manifesto-execucao.json<br/>ambiente, commits, hashes, política"]
        subgraph ART["artefatos/"]
            SUP["superficie-do-projeto.json"]
            INV["recurso/inventario.json"]
            DIV["recurso/divergencias-de-schema.json"]
        end
        subgraph COB["cobertura/recurso/"]
            GH["gate.html"]
            CH["cobertura.html"]
        end
        REP["cypress/recurso/report.json"]
        SBX["sandbox/ · só no --dry-run"]
    end

    subgraph CLIENTE["projeto de testes do consumidor · dele"]
        direction TB
        SPEC["cypress/e2e/apis/recurso/<br/>crud.cy.js · validacoes.cy.js · seguranca.cy.js<br/>_support/api.js · _support/cobertura.json"]
        SCH["cypress/fixtures/schemas/recurso/*.schema.json"]
        STG[".qa-staging-execucao-recurso/<br/>some quando o recurso passa"]
    end

    DIARIO["diario-de-propriedade.json<br/>na raiz de saída, ACIMA da execução"]

    NOSSO -. "publicação atômica,<br/>uma vez por recurso" .-> CLIENTE
    CLIENTE --> DIARIO
```

## Por que o diário fica acima da execução

Ele responde *"este arquivo é nosso?"*, e essa pergunta **atravessa execuções**. Um
spec que criamos na terça e que alguém editou na quarta não é nosso para apagar na
quinta — e é o diário que sabe disso.

## O staging só sobrevive quando o recurso falha

É o único diretório nosso que fica **dentro** do projeto do consumidor. Quando
sobra, ou está vazio — e aí é lixo, e some — ou tem o artefato reprovado, e aí o
evento `staging_mantido` diz onde ele está. Artefato reprovado não é apagado por
padrão: é o que se inspeciona para entender a falha.

## O que **não** vira artefato

O texto que uma tool devolveu. O `execucao.jsonl` grava `caracteres` e o prefixo
`ERRO:`, nunca o conteúdo — ver
`tests/test_observabilidade_medidas.py`.
O log mede; ele não transcreve.
