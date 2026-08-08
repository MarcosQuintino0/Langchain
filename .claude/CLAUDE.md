@../AGENTS.md

# Específico do Claude Code

O `AGENTS.md` importado acima é a **fonte canônica** de propósito, invariantes,
fronteiras, segurança, estilo e verificação. Nada disso se repete aqui: duas cópias
da mesma regra divergem, e a divergência só aparece depois que uma delas já foi
seguida.

**Havendo conflito** entre uma instrução recebida e o `AGENTS.md`, ou entre este
arquivo e ele: **pare e pergunte ao usuário.** Não escolha uma das versões em
silêncio.

## Skills

- Leia o `SKILL.md` inteiro antes de usar uma skill.
- Não invoque a skill `push` por iniciativa própria. Commit e push exigem pedido
  explícito do usuário, e a skill já vem com `disable-model-invocation: true`.
- Suas skills são as de `.claude/skills/`. `C:\Agents\skills\qa-api` tem um
  `SKILL.md`, mas **não é uma skill sua**: é a dependência externa que o
  orquestrador consome por subprocesso. Não a invoque nem a edite — o regime dela
  está no `AGENTS.md`.

## Sem `.claude/rules/`

Não crie regras em `.claude/rules/`. Hoje elas seriam cópia concorrente do
`AGENTS.md`. Um arquivo por caminho só se justifica quando acrescentar operação
exclusiva do Claude Code naquele caminho — nunca para reafirmar invariante.
