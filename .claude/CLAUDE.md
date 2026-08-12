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
- Suas skills são as de `.claude/skills/`, e só elas. `C:\Agents\skills\qa-api`
  tem um `SKILL.md`, mas **não é uma skill sua e não é mais dependência deste
  projeto**: o orquestrador foi desacoplado dela em 2026-08-10 e não invoca mais
  nada de lá. Não a invoque, não a edite, não copie trecho dela para cá — nem para
  consultar como algo era feito. O regime dela está no `AGENTS.md`.

## Quando o usuário pedir "rode a suíte inteira"

O procedimento — comandos, backend, recursos, e a tabela de onde tirar cada
número — está em **"A suíte completa com o modelo real"**, no `AGENTS.md`. Não
está repetido aqui de propósito: são instruções que mudam junto com a CLI, e duas
cópias divergem sem avisar. O que segue é só o que é seu, como agente.

**Nunca dispare essa execução por iniciativa própria.** Ela gasta dinheiro do
usuário e leva dezenas de minutos. "Rodar a suíte" numa frase sobre testes
costuma significar `python -m pytest`; a suíte paga é a que tem `--recurso` e
chama o provedor. Na dúvida entre as duas, pergunte.

**Se a mudança foi do Bloco 2 para frente, proponha `--reaproveitar` em vez da
suíte completa** — norma de código, prompt do executor, fatiamento, reparo, gate
do Gate B. O procedimento está em "Iterar no executor sem pagar o pipeline
inteiro", no `AGENTS.md`. Custa um quinto do tempo e mede melhor: rodar o
pipeline completo faz o mapeador e o planejador variarem, e aí não se sabe se o
que mudou no resultado foi a sua alteração ou o sorteio.

Duas coisas suas nesse ciclo: **fixe um `run_id` e não o troque** entre as voltas
da mesma investigação, e **dispare o gate em seco** contra a suíte publicada antes
de dar a qualquer régua nova o poder de reprovar. Custo zero, e é o que impede
uma régua mal calibrada de queimar as três tentativas do Gate B.

**Rode pelo venv de execução congelado** (`.venv-execucao`, ver o tutorial no
`AGENTS.md`) — nunca pelo `.venv` de desenvolvimento: o usuário edita o checkout
enquanto a suíte anda, e os prompts são relidos do disco a cada chamada. Lembre
de reinstalar (`pip install .`) antes de cada suíte.

**Rode em segundo plano** e diga ao usuário como acompanhar o log ao vivo:

```powershell
Get-Content -Wait .execucoes\<run_id>\execucao.jsonl
```

**O relatório é o entregável, não a execução.** Terminar a suíte e responder "deu
certo" é não ter feito a tarefa: o motivo de gastar é comparar com a execução
anterior. Traga sempre dólares, tokens, tempo e tentativas por gate, e compare
com o run anterior pelo `execucoes comparar`. Se algum número não existir, diga
qual e por quê — não preencha lacuna com estimativa apresentada como medição.

**Não confunda "a suíte passou" com "os testes ficaram bons".** O código de saída
diz que os gates aprovaram. Se o usuário mudou prompt, fatiamento ou modelo, ele
quer saber o que mudou no conteúdo: quantos `it` por categoria, se o cleanup
continua conferindo o resultado, se algum endpoint ficou sem cobertura.

## Sem `.claude/rules/`

Não crie regras em `.claude/rules/`. Hoje elas seriam cópia concorrente do
`AGENTS.md`. Um arquivo por caminho só se justifica quando acrescentar operação
exclusiva do Claude Code naquele caminho — nunca para reafirmar invariante.
