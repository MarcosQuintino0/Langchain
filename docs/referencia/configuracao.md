# Configuração

O `config.toml` nasce de `orquestrador init`, comentado campo a campo. Esta página
não o repete: **o arquivo gerado é a referência**, e mantê-lo aqui em paralelo
criaria a segunda cópia que sempre diverge.

```bash
orquestrador init
orquestrador doctor
```

O primeiro escreve o arquivo; o segundo diz o que ainda falta preencher.

## Os cinco blocos

| Bloco | O que decide |
| --- | --- |
| `[caminhos]` | onde estão o backend, o projeto de testes e a saída |
| `[openrouter]` | para onde o código-fonte pode ir, e sob qual política |
| `[estagios.NOME]` | modelo, temperatura e limites de cada estágio de LLM |
| `[gates.a]`, `[gates.b]` | o teto de tentativas de reparo de cada gate |
| `[execucao]` | Graphify, Cypress, formatadores, timeouts e tetos de leitura |

Eram seis. A seção `[skill]`, com a impressão digital dos `.mjs`, saiu no
desacoplamento de 2026-08-10, junto com `[caminhos].skill`, `[caminhos].scripts`,
`[execucao].node` e as flags de linha de comando dos validadores. Campo de
configuração que nada lê é pior que campo ausente: ele sobrevive no arquivo do
cliente, alguém o preenche com um caminho inventado, e o preenchimento não produz
efeito nenhum que denuncie o engano.

## Dois campos que merecem atenção

**`[estagios.*].modelo` nasce vazio, não com um placeholder.** Um caminho falso é
detectável e inofensivo; um identificador de modelo falso seria enviado ao provedor
como se fosse real. E nenhum nome de modelo pode aparecer em código (princípio 6),
então string vazia é o único valor que o `doctor` distingue de uma escolha
deliberada.

**`[openrouter].hosts_permitidos` é conjunto fechado.** Uma `base_url` fora dele não
carrega, e não há fallback — o que atravessa essa fronteira é o código-fonte de quem
nos contratou. Trocar o destino é a mudança de uma linha que passa despercebida numa
revisão; declarar o host novo junto é o que a torna revisável.

## Os caminhos são resolvidos contra o `config.toml`

Não contra o diretório atual. Um caminho relativo significa a mesma coisa
independentemente de onde o comando foi invocado.
