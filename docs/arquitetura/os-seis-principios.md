# Os seis princípios

São eles que explicam por que quase toda decisão deste projeto é a menos óbvia. O [`AGENTS.md`](https://github.com/MarcosQuintino0/Langchain/blob/main/AGENTS.md) os repete como **limite** — lá eles dizem o que não pode ser quebrado; aqui, por quê.

### Os seis princípios

1. **O que trafega entre estágios é artefato em disco, nunca histórico de
   conversa.** Os arquivos (`graph.json`, `cobertura.json`, `inventario.json`, os
   `.cy.js`, `report.json`) são a única memória compartilhada. Qualquer agente
   pode morrer e ser reinstanciado do zero sem perda.
2. **Loop de reparo envia apenas o delta:** `instrucao_fixa_do_estagio +
   artefato_atual + delta.violacoes`. Nunca o histórico das tentativas. Vive em
   `src/orquestrador/llm/montagem.py`, num lugar só, para não
   escapar por descuido.
3. **Agentes são stateless entre unidades de trabalho.** Um recurso por vez,
   histórico zerado entre recursos.
4. **Quem reprova é script; LLM só cria.** Nenhum LLM decide se a cobertura está
   completa.
5. **O auditor semântico fica fora do loop quente.** Caro, com falso positivo
   alto, sob demanda, com humano triando.
6. **Modelo configurável por estágio.** Nenhum nome de modelo no código.
