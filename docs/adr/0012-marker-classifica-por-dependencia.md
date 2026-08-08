# ADR 0012 — Marker classifica por dependência, não por escopo

**Status:** Aceita

## Contexto

549 de 695 testes não tinham marker. Ao aplicá-los, a pergunta era se `tmp_path` e `sys.executable` contam como I/O.

## Decisão

O marker responde *'do que este teste precisa além do venv?'*. `tmp_path` e `sys.executable` ficam em `unit`: disco temporário e o próprio interpretador vêm com o pytest.

## Consequências

`integration` passa a significar 'precisa de máquina preparada', que é o conjunto cuja ausência faz um teste **pular sozinho** — e pulo silencioso é o que a checagem da CI existe para caçar. Classificar por mecanismo jogaria 15 de 26 arquivos em `integration` e esvaziaria o significado. O resultado é contraintuitivo o bastante para estar escrito no `pyproject.toml`, ao lado dos markers.
