# `superficie-do-projeto.json`

O que `analise_estatica/extrator_de_superficie.py` enxerga em
`fixtures/projeto-testes/` — três módulos compartilhados e os exports de cada um.

## O que ele prova

Que o executor recebe os nomes de export, a declaração **verbatim** e os dois
caminhos relativos de import que ele precisa para escrever um spec que resolve.

É o único dos quatro goldens cujo conteúdo vem de **parsing real**: os outros
congelam serialização. Por isso é aqui que uma regressão no leitor de JavaScript
aparece primeiro.

## O que é mudança legítima

* um arquivo novo em `fixtures/projeto-testes/cypress/support/api/`;
* uma forma de `export` que o extrator passou a reconhecer — o parser é
  heurístico, e ampliá-lo é trabalho previsto.

Nos dois casos, o diff deve **crescer**. Um export que some é regressão até prova
em contrário: o sintoma no consumidor é import que não resolve no Gate B, e o delta
que ele devolve não é acionável.

## Cuidado com a profundidade

`import_do_recurso` sai de `cypress/e2e/apis/<recurso>/` e `import_do_support` sai
de `.../<recurso>/_support/` — bases **diferentes**, um nível de distância. Um `..`
a mais aqui é o defeito mais fácil de introduzir e o mais difícil de ler no diff.
