# ADR 0010 — `Path` como valor é permitido em `dominio/`

**Status:** Aceita

## Contexto

A regra original dizia 'sem I/O, sem `Path`, sem subprocess'. Quatro símbolos do domínio usam `Path` na anotação — entre eles `Recurso`, o símbolo mais importado do repositório e literalmente a unidade de trabalho do princípio 3.

## Decisão

A regra passa a ser sobre **acesso**, não sobre o tipo. `Path` entra como valor; álgebra de caminho (`/`, `.parent`, `.with_suffix`) é permitida; abrir, ler, escrever, listar e resolver, não.

## Consequências

`dominio/` continua construível e validável sem nenhum arquivo por perto, que é o que a regra existe para garantir. A fronteira ficou mais sutil, e por isso ganhou teste: `test_dominio_nao_toca_no_disco` recusa import de `subprocess`/`os`/`io`/`shutil`/rede, chamada de `open()` e métodos de acesso a disco de `Path`. `Path.replace` ficou de fora da lista porque `str.replace` tem o mesmo nome — checagem que reprova o inocente é desligada na primeira vez que atrapalha.
