---
name: push
description: Commita e envia SOMENTE os arquivos da tarefa atual, com stage explícito por caminho. Use quando o usuário digitar /push ou pedir explicitamente para commitar e enviar o trabalho.
disable-model-invocation: true
---

# push

Cria **um** commit com os arquivos da tarefa atual e envia para a branch corrente.

**Este working tree é compartilhado com outros agentes.** Um `git add -A` aqui
commita o trabalho de outra pessoa sob a sua autoria, e pode publicar mudança do
usuário que ele ainda não queria enviar. É o motivo desta skill existir na forma
abaixo.

A invocação autoriza commit e push **dos caminhos da sua tarefa**. Ela não autoriza
incluir mudança alheia, criar branch, rebase, force push nem limpar o working tree.

## Passos

1. Levante o estado, numa chamada só:

   ```bash
   git status --short && git branch --show-current && git diff && git diff --staged
   ```

   Se o diff for grande, complemente com `git diff --stat`. Arquivo novo não aparece
   em `git diff`: liste com `git status --short` e leia os que importam.

2. **Separe o que é seu.** Liste os caminhos que *esta tarefa* alterou. Todo o resto
   — inclusive arquivo não rastreado, mudança preexistente e trabalho de outro
   agente — fica de fora, intacto.

   **Se não der para distinguir a autoria de uma alteração, pare e pergunte ao
   usuário.** Não presuma que ela é sua. Silêncio custa uma pergunta; palpite custa
   um commit misturado que ninguém consegue desfazer limpo.

3. Escreva a mensagem a partir do que o diff **faz**, não dos arquivos que ele toca:

   - primeira linha em português, imperativo, até ~72 caracteres, sem ponto final;
   - corpo (quando houver mais de uma mudança ou uma decisão não óbvia): linhas
     curtas explicando **por quê**, não o que o diff já mostra;
   - nada de "atualiza arquivos", "várias melhorias" ou lista de nomes de arquivo.

4. Rode as verificações do `AGENTS.md` que forem proporcionais ao risco da mudança.

5. Faça stage **explícito, caminho por caminho**:

   ```bash
   git add -- <caminho1> <caminho2>
   ```

   Nunca `git add -A`, nunca `git add .`, nunca glob amplo (`git add src/`,
   `git add *.py`). O `--` separa caminhos de opções e evita que um nome de arquivo
   seja lido como flag.

6. Confira o que foi para o índice **antes** de commitar:

   ```bash
   git status --short && git diff --cached --stat && git diff --cached --check
   ```

   Se algo que não é seu apareceu no índice, tire com
   `git restore --staged -- <caminho>` e recomece o passo 5.

7. Commite e envie:

   ```bash
   git commit -m "<mensagem>" && git push origin HEAD
   ```

   `HEAD` de propósito: empurra para a branch atual, seja ela qual for. Se a branch
   ainda não existe no remoto, use `git push -u origin HEAD`.

8. Relate: a mensagem usada, a branch, o hash curto, os caminhos commitados e as
   verificações que rodou.

## Limites

- Se não houver nada seu para commitar, diga isso e pare — não crie commit vazio e
  não "aproveite" para incluir mudança alheia.
- Se o push for recusado (branch atrás do remoto), **não** force. Relate o erro e
  sugira `git pull --rebase` ao usuário.
- Nada de `--force`, `--force-with-lease`, rebase, `reset` destrutivo, `stash`,
  `clean` ou reescrita de histórico. Nem para "resolver rápido".
- Não use `--no-verify` nem pule hooks. Hook que falha é problema a resolver, não a
  contornar — e nunca se resolve incluindo mais arquivos para fazer passar.
- Não crie branch e não altere commit já existente.
- Se hook ou push falharem, **preserve o estado**, diagnostique e relate.
