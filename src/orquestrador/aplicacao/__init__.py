"""Coordena os estágios e a persistência do que eles produzem.

É a camada que conhece a ordem — Bloco 0, 1, gate A, 2, gate B, publicação,
Bloco 3 — e o que fazer quando um passo falha. Ela não parseia saída de
ferramenta (isso é `ferramentas/`), não decide aprovação (é `gates/`) e não
monta prompt (é `llm/` e `agentes/`).

Três módulos, por três razões de mudar: `pipeline.py` quando a **ordem** dos
blocos mudar, `ciclo_de_reparo.py` quando a mecânica do loop mudar, e
`persistencia.py` quando mudar **o que vai para o disco**.
"""
