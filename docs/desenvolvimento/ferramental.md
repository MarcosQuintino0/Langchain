# Ferramental

Cada ferramenta aqui existe por um defeito concreto que ela pega. A pergunta que
esta página responde para cada uma é sempre a mesma: **o que aconteceria sem ela?**

## Ruff — lint e formatação

*O que faz aqui:* uma passada só de lint e formatação, com `select` explícito.
Além do básico, tem `S` (flake8-bandit, porque subprocesso e caminho são o coração
deste projeto), `PTH` (usar `pathlib` em vez de `os.path`), `ARG` (argumento não
usado) e `PLC0415` (import fora do topo).

*Sem ele:* o `PLC0415` já pagou o próprio custo duas vezes — os dois imports tardios
que sobraram no projeto têm seis linhas de comentário explicando por que existem,
e é a regra ligada que obriga essa explicação. Sem ela, import dentro de função vira
hábito e a aresta de dependência some do topo do arquivo.

*Por que não `black` + `flake8` + `isort`:* três ferramentas, três configurações e
três versões para manter sincronizadas. O Ruff faz o mesmo numa passada.

**Regras deliberadamente fora do `select`**, com o motivo escrito no
`pyproject.toml`: `D101`/`D103` (docstring obrigatória vira docstring burocrática),
`N818` (nosso `FalhaDeGate` não vai virar `FalhaDeGateError`), `C901`/`PLR0912` —
complexidade ciclomática mediria mal um `executar` que é um laço linear com muito
comentário.

## Pyright — tipagem

*O que faz aqui:* `standard` no repositório inteiro, **`strict` em `src`**, com
`reportUnnecessaryTypeIgnoreComment = "error"` e sem baseline de ignores.

*Sem ele:* o LangGraph devolve `CompiledStateGraph[Unknown, ...]`, e sem alguém
cortando essa propagação num ponto só, tudo que sai do agente vira `Unknown` —
inclusive o que atravessa telemetria, parser e detecção de erro. `grafo_react.py`
existe em parte por isso.

*O `reportUnnecessaryTypeIgnoreComment` é a parte que mais paga:* ele reprova
supressão que já não suprime nada. Sem isso, o `# pyright: ignore` fica no arquivo
depois que o defeito foi corrigido, e o próximo leitor acredita nele.

## pytest — testes

*O que faz aqui:* `--strict-markers` ligado, `--cov` **fora** do `addopts`, e três
markers de classe com um hook de coleta que exige exatamente um por teste.

*Por que `--cov` fica de fora:* o piso é global e a suíte é rodada em fatias o tempo
todo durante o desenvolvimento. Um piso no `addopts` faria `pytest tests/x.py`
falhar por motivo alheio ao que se está testando — e teste que falha por motivo
errado é teste que se aprende a ignorar.

## pytest-cov / coverage — cobertura

*O que faz aqui:* `branch = true` e `fail_under = 85`, medido na CI sobre a suíte
inteira.

*Sem `branch`:* cobertura de linha diz que o `if` foi executado; cobertura de ramo
diz se o `else` também foi. Num projeto cheio de "aprova ou reprova", é o ramo que
importa.

*Sobre o número:* 85 é piso, não meta. Ele existe para reprovar a queda, não para
premiar a subida — perseguir 100% produz teste que exercita linha sem verificar
comportamento.

## pre-commit — o que roda antes do commit

*O que faz aqui:* fim de linha LF, arquivo terminando em newline, TOML e YAML
parseáveis, marcador de conflito, e o Ruff — com hooks **locais** (`language:
system`), não o mirror oficial.

*Por que hook local:* o mirror pinaria uma segunda versão do Ruff, e duas versões
divergindo produzem o pior resultado possível — o commit passa e a CI reprova, ou o
contrário.

*O que deliberadamente NÃO entra:* pytest e Pyright. Um hook que demora trinta
segundos é um hook que se aprende a pular com `--no-verify`, e aí ele deixa de pegar
até o que era barato.

*Não está no extra `dev`:* é ferramenta de máquina, não dependência do projeto.
`pipx install pre-commit`.

## uv — build e teste do wheel

*O que faz aqui:* só uma coisa, e é a que importa: `test_wheel_limpo` constrói o
wheel, cria um ambiente vazio, instala e verifica que os prompts chegaram lá dentro.

*Sem ele:* o `pip install` do wheel instalava com sucesso e falhava no primeiro
comando, porque `prompts/` ficava de fora e era procurado na árvore de fontes. Foi
um defeito real, e é o único teste da suíte marcado `integration`.

*Não é gerenciador de dependência aqui:* as versões são pinadas à mão no
`pyproject.toml`, com as transitivas junto.

## MkDocs Material — este site

*O que faz aqui:* renderiza `docs/` e, na CI, reprova link quebrado e página fora
da navegação com `mkdocs build --strict`. O site **não é publicado** — ver
[ADR 0011](../adr/0011-documentacao-sem-publicacao.md).

*Sem `mkdocstrings`:* o projeto deixou `D101`/`D103` fora do `select` de propósito.
Autodoc sobre um pacote assim produz páginas com o nome da função e mais nada, e
página vazia é pior que nenhuma — promete referência que não existe.
