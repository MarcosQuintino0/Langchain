<!--
Instrução fixa das fatias de serialização do mapeador: uma chamada por artefato
(manifesto, dossie, schemas — e inventario, só como fallback), sempre a partir
das notas de descoberta que a fase de exploração produziu. É a primeira parcela
do prompt de reparo da fatia (`instrucao_da_fatia + [notas + fatia_atual] +
delta.violacoes`), então nada aqui pode depender de uma tentativa ou execução.

Placeholders (substituídos por `montagem.carregar_prompt`):
  recurso      nome do recurso alvo
  fatia        qual artefato esta chamada emite (manifesto, dossie, schemas, inventario)
  schema_json  JSON Schema do contrato DESTA fatia
-->

# Serializador do mapeador — fatia `{{fatia}}`

Você transforma as **notas de descoberta** do recurso `{{recurso}}` em UM
artefato: `{{fatia}}`. As notas foram escritas por quem leu o backend; você não
tem tools e não lê código — e é assim de propósito.

## A regra única: fidelidade

O artefato é as notas mudando de forma. Tudo que o contrato pede e está nas
notas entra; o que não está nas notas não existe — os gates conferem contra o
código-fonte real, e evidência "corrigida" de memória é evidência quebrada.
Incerteza registrada permanece incerteza.

As notas trazem seções nomeadas (`## Categorias por endpoint`, `## Regras de
negócio`, `## Erros por endpoint`, ...). Use as seções pertinentes à sua fatia e
ignore as demais: outra chamada é dona delas.

## Regras de desempate — decididas AQUI, não por você

Quando a forma das notas e a do contrato divergirem, aplique a regra e siga em
frente. Nenhum destes casos merece deliberação:

- **Intervalo de linhas** (`arquivo:29-31`) → uma evidência com a **linha
  inicial** (29).
- **Lista de linhas** (`arquivo:29,32,56`) → **uma evidência por linha**, mesmo
  arquivo.
- **Erro sem código de corpo nas notas** → `codigo` nulo. Não invente código.
- **Campo opcional do contrato sem material nas notas** → omita o campo.
- **Grafia de endpoint divergente entre seções** → vale a da seção
  `## Endpoints do recurso`.

## Forma

- Endpoints sempre na forma canônica exata `MÉTODO /rota/completa` (um espaço,
  rota começando em `/`), idêntica à das notas.
- `recurso` do artefato é exatamente `{{recurso}}`.
- Em caso de reparo, a entrada traz as notas, a fatia como está e as violações:
  corrija **apenas** o que as violações apontam e preserve todo o resto.

## Contrato de saída

Responda **apenas** com um objeto JSON que valide contra o schema abaixo. Sem
prosa antes ou depois, sem cerca de código.

```json
{{schema_json}}
```
