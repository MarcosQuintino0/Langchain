# `schemas-do-dominio.json`

O `model_json_schema()` dos 12 contratos que atravessam a fronteira do processo —
para o `.mjs` da skill, para o disco do consumidor ou para o log —, com
`description` **removido recursivamente**.

## O que ele prova

Nome de campo, tipo, alias, `required` e `additionalProperties` de cada contrato
público. É o documento que o `validar-suite-gerada.mjs` e o próprio Pydantic usam
para decidir se uma saída de modelo é aceitável.

Foi este golden que provou que a divisão de `contratos.py` em `dominio/` (Etapa 6)
foi um movimento **puro**: o Pydantic chaveia `$defs` pelo nome da classe, sem
módulo, então mover a definição não muda uma linha aqui.

## Sem `description`, de propósito

Se as docstrings entrassem, melhorar a explicação de um campo churnaria o golden —
e golden que churna por motivo cosmético é golden que se regenera sem ler.

## O que é mudança legítima

Campo novo, tipo trocado, alias trocado, obrigatoriedade trocada. Todas exigem
conferir o lado da skill: as três primeiras mudam o que ela recebe, a última muda o
que ela recusa.

## O que não é

Diferença que apareça sem você ter tocado num modelo de `dominio/`. Nesse caso o
Pydantic mudou de versão, e o que precisa de revisão é o pin, não o arquivo.

## 2026-08-10 — dossiê no contrato do mapeador

`SaidaMapeador` ganhou o campo opcional `dossie` (`DossieDoRecurso`): as
regras de negócio com evidência, o contrato de erro, os parâmetros de consulta,
as incertezas e a checklist negativa que o mapeador lê de qualquer forma e antes
descartava. O diff é aditivo — nenhum campo existente mudou de nome, tipo ou
obrigatoriedade — e o `` novo entra pelo mesmo mecanismo de sempre.
