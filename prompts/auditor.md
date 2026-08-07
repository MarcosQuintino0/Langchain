<!--
PLACEHOLDER DA FASE 1 — o auditor é stub nesta fase (ver `agentes/auditor.py`).

Instrução fixa do auditor semântico. Ele roda FORA do loop quente: sob demanda, em
sessão limpa, com humano triando o veredito. Nunca é chamado por um gate.

O que a Fase 2 escreve aqui: como refutar uma justificativa de `naoAplica` diante
do backend e como reconhecer oráculo fraco (status flexível, mera existência do
corpo, ausência de erro 5xx). Fonte: `references/auditar-cobertura.md`.

Placeholders disponíveis:
  {{caminho_backend}}    raiz do backend
  {{caminho_manifesto}}  caminho do _support/cobertura.json auditado
  {{amostra_specs}}      lista de specs da amostra
  {{schema_json}}        JSON Schema de `ResultadoAuditoria`
-->

# Auditor semântico — instrução do estágio

<!-- FASE 2: refutação de naoAplica e detecção de oráculo fraco. -->

## Contrato de saída

```json
{{schema_json}}
```
