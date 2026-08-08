# Convenções

As regras que valem como **limite** estão no `AGENTS.md`. Esta
página explica as que precisam de mais de uma linha.

## Onde um arquivo novo mora

Escolha o diretório pelo **único motivo dominante de mudança**. A tabela está no
`AGENTS.md`, e ela tem teste: `test_todo_subpacote_tem_linha_na_tabela` e
`test_a_estrela_marca_exatamente_o_que_nao_existe` comparam o documento com o disco
nos dois sentidos.

A raiz do pacote é **lista fechada** de cinco arquivos. Ela não é "onde ainda não
decidi".

## Nome de arquivo de teste

O nome nomeia o que o arquivo protege:

| Forma | Quando |
| --- | --- |
| `test_<subpacote>_<modulo>.py` | protege um módulo |
| `test_invariante_<nome>.py` | protege uma regra que atravessa módulos |
| `test_e2e_<nome>.py` | exercita o pipeline inteiro |

Um sufixo depois do módulo descreve a fatia: `test_pipeline_loop_reparo.py` promete
`pipeline.py`. Tem teste — o nome é lido como caminho, tentando todo corte possível
do sublinhado.

Achatado, e não um `test_lacunas.py` dentro de um diretório `gates/`: sem
`__init__.py` em cada nível, dois arquivos de mesmo nome-base em pastas diferentes
produzem `import file mismatch`, que é erro de coleta.

## Marker

Todo teste declara **exatamente um**: `unit`, `integration` ou `e2e`. A coleta
reprova sem ele.

A regra é *"do que este teste precisa além do venv?"*, não *"quantos syscalls ele
faz"*. Por isso `tmp_path` e `sys.executable` ficam em `unit`, por mais
contraintuitivo que soe — disco temporário e o próprio interpretador vêm com o
pytest. O que importa é que a ausência de uma dependência de máquina é o que faz um
teste **pular sozinho**, e é o pulo silencioso que a checagem da CI existe para
caçar.

## Docstring e comentário

- **Docstring de módulo explica a fronteira e a invariante** — o que ele é dono, o
  que deliberadamente não faz, e por quê. Não é índice de funções.
- **Comentário explica por que a alternativa óbvia está errada.** Se o leitor
  provavelmente pensaria "por que não fazer do jeito X?", responda.
- **Nunca narre a linha seguinte.**
- **Nunca deixe marcador de fase como fonte de verdade** (`Fase 1`, `TODO`,
  `futuro`). Estado temporário pertence ao plano; decisão durável pertence à
  docstring, escrita no presente.

## Mensagem de falha de teste

Quem lê é alguém daqui a seis meses, sem o contexto de hoje. Toda mensagem diz **o
que fazer**: qual diretório recebe o arquivo, qual documento atualizar, qual import
trocar.

## Golden

Nenhum golden tem mecanismo de atualização. Existe `--mostrar-golden`, que imprime e
não grava. Cada um tem um `.md` ao lado e, mais importante, **uma asserção
estrutural independente** — escrita primeiro. Ver
`tests/goldens/README.md`.

## Tabela publicada

Se a tabela sai de um dicionário no código, ela é **gerada** entre marcadores e o
teste compara. Vale para o catálogo de eventos, os códigos `QAORQ-`, os códigos de
saída e a ajuda da CLI. A regra existe porque toda tabela deste projeto mantida à
mão já divergiu pelo menos uma vez.

## Correção de bug

Inclui um teste que **falha antes e passa depois**. Escreva o teste primeiro e
veja-o falhar: teste escrito depois costuma provar o código, não o comportamento.
