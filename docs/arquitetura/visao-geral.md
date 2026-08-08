# Visão geral

Quatro blocos, um recurso por vez. Dois deles chamam um modelo; os outros dois não
chamam modelo nenhum, e é dessa assimetria que sai a promessa do projeto.

```mermaid
flowchart LR
    subgraph B0["Bloco 0 · zero token"]
        RX["qa-reindex.mjs<br/>Graphify · AST"] --> GRAFO[("graph.json")]
    end

    subgraph B1["Bloco 1 · um recurso por vez"]
        MAP["MAPEADOR<br/>LLM + 5 tools · ReAct"]
        GA{"GATE A<br/>validar-suite-gerada --so-manifesto<br/>+ diff grafo × manifesto"}
        MAP --> GA
        GA -- "reprova · delta" --> MAP
    end

    subgraph B2["Bloco 2 · um recurso por vez"]
        EXE["EXECUTOR<br/>LLM · sem tools"]
        GB{"GATE B<br/>prettier · eslint<br/>validar-suite-gerada · lacuna"}
        EXE --> GB
        GB -- "reprova · delta" --> EXE
    end

    subgraph B3["Bloco 3 · zero token"]
        CY["Cypress + qa-cobertura.mjs"]
    end

    GRAFO --> MAP
    GA -- aprova --> EXE
    GB -- aprova --> PUB[["publicação atômica<br/>no projeto do consumidor"]]
    PUB --> CY

    classDef llm fill:#4c1d95,stroke:#a78bfa,color:#fff
    classDef det fill:#065f46,stroke:#34d399,color:#fff
    classDef gate fill:#7c2d12,stroke:#fb923c,color:#fff
    class MAP,EXE llm
    class RX,CY,PUB det
    class GA,GB gate
```

**Roxo cria, verde e laranja verificam.** É o princípio 4 desenhado: nenhum nó roxo
decide se a cobertura está completa, e nenhum nó roxo valida a saída de outro nó
roxo. Essa distinção é a coisa mais importante do diagrama, e era invisível no
bloco de texto que ele substituiu.

## Por que o Gate A tem duas checagens

O validador da skill enxerga apenas o projeto de testes, nunca o backend — limite
deliberado dela. Ele prova *"entreguei o que planejei"*, nunca *"planejei tudo que
existe"*.

Os dois defeitos são diferentes, e só o segundo era o defeito de origem do projeto:

| Defeito | Quem pega |
| --- | --- |
| planejei N, entreguei menos que N | `validar-suite-gerada.mjs` (Gate A e B) |
| o backend tem N, planejei menos que N | **diff grafo × manifesto** (`QAORQ-002`) |

## O que cada bloco custa

O Bloco 0 e o Bloco 3 são determinísticos e não gastam token. Quase todo o custo
está no Bloco 1: é o mapeador que explora o backend, e é por isso que as tools dele
são instrumentadas uma a uma — ver [Artefatos de execução](../referencia/artefatos-de-execucao.md).
