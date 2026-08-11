# Fluxo ponta a ponta

O que acontece entre `orquestrador --recurso pedidos` e o arquivo no disco de quem
nos contratou.

```mermaid
sequenceDiagram
    autonumber
    participant CLI as cli/principal
    participant PL as aplicacao/pipeline
    participant GX as Graphify
    participant MP as Mapeador (LLM)
    participant GA as Gate A
    participant ST as Área de staging
    participant EX as Executor (LLM)
    participant GB as Gate B
    participant PJ as Projeto do consumidor
    participant DI as Diário de propriedade

    CLI->>PL: rodar([Recurso])
    PL->>GX: preparar()
    GX-->>PL: graph.json utilizável?
    Note over PL: grafo inválido interrompe<br/>ANTES da primeira chamada de modelo

    PL->>ST: criar_area(fotografa o destino)

    loop até o Gate A aprovar
        PL->>MP: instrução fixa + (delta)
        MP-->>PL: Inventario + Manifesto + schemas
        PL->>ST: escreve
        PL->>GA: valida o staging
        GA-->>PL: aprovado / violações
    end

    loop até o Gate B aprovar
        PL->>EX: instrução fixa + manifesto + superfície + (delta)
        EX-->>PL: specs .cy.js
        PL->>ST: escreve
        PL->>GB: valida o staging
        GB-->>PL: aprovado / violações
    end

    PL->>PJ: publica (uma vez só)
    PL->>DI: registra o que criou e o que modificou
    PL->>PJ: Cypress (só com --rodar-cypress)
```

## O que o diagrama mostra e o texto não mostrava

**O projeto do consumidor aparece uma vez só, e depois dos dois gates.** Enquanto o
loop roda, cada tentativa reescreve a área de staging. O que uma tentativa ruim
destrói é a tentativa anterior — nunca o projeto de quem nos contratou.

**Os dois laços não compartilham nada.** O executor não recebe a exploração do
mapeador: recebe o manifesto, que é artefato em disco. É o princípio 1, e é ele que
permite matar e reinstanciar qualquer agente sem perda.

**A área de staging fotografa o destino antes de qualquer escrita.** É essa foto que
responde "este arquivo já era do consumidor?" na hora de gravar um schema — e é por
isso que o schema preexistente é preservado em vez de sobrescrito.
