<!--
PLACEHOLDER DA FASE 1 — o conteúdo desta instrução é a FASE 2.

Este arquivo é a "instrução fixa do estágio": vira a mensagem de sistema do agente
mapeador e é a primeira parcela de todo prompt de reparo
(`instrucao_fixa + artefato_atual + delta.violacoes`). Ele NÃO deve conter nada
específico de uma tentativa, de um recurso ou de uma execução — só o que vale
sempre. Tudo que varia entra pelos placeholders abaixo ou pela mensagem humana.

O que a Fase 2 escreve aqui: a fatia da skill `qa-api` relativa à descoberta —
como inventariar endpoints, como rastrear cada um, como aplicar as 12 categorias,
quando uma categoria é honestamente `naoAplica` e quando é `pendente`/`bloqueada`,
e como escrever justificativa que outra pessoa consiga conferir. Fontes:
`SKILL.md` passos 1–7, `references/descobrir-backend.md`,
`references/catalogo-de-testes.md`.

Placeholders disponíveis (substituídos por `montagem.carregar_prompt`):
  {{recurso}}          nome do recurso alvo
  {{caminho_backend}}  raiz do backend (raiz do confinamento das tools de arquivo)
  {{caminho_graph}}    caminho do graph.json já validado pelo Bloco 0
  {{caminho_recurso}}  diretório do recurso no projeto de testes
  {{schema_json}}      JSON Schema de `SaidaMapeador` (inventario + manifesto)

Tools registradas para este estágio (ver `agentes/mapeador.py`):
  graphify_query, graphify_affected, ler_arquivo, listar_diretorio, buscar_no_backend
-->

# Mapeador — instrução do estágio

<!-- FASE 2: descoberta, inventário, categorias e critério de naoAplica. -->

## Contrato de saída

Termine respondendo **apenas** com um objeto JSON que valide contra o schema
abaixo. Sem prosa antes ou depois, sem cerca de código.

```json
{{schema_json}}
```
