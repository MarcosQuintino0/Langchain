<!--
A norma de escrita do código Cypress gerado. Não é prompt de estágio: é carregada
por `carregar_prompt("padrao-de-codigo-cypress")` e injetada no `executor.md` pelo
placeholder `padrao_de_codigo`. O auditor semântico consome a mesma norma quando
deixar de ser stub — é por isso que ela é arquivo próprio, e não uma seção do
prompt do executor: norma que mora dentro de um estágio só pode ser lida por ele.

Este arquivo diz COMO se escreve. O que cobrir (categorias, cenários obrigatórios,
oráculo mínimo de cada uma) é do `executor.md`, e não se repete aqui.

Cada regra de régua traz o código `QAORQ-` que a cobra. O catálogo dos códigos está
em `gates/codigos.py`, e um teste exige que os dois lados concordem: código citado
aqui que não exista lá reprova, e vice-versa.

Sem placeholders: o texto entra em outro prompt já montado.
-->

# Padrão de código da suíte

## O princípio

**Quem lê o teste é uma pessoa júnior que não conhece este backend.** Ela precisa
entender o que está sendo testado, por que aquilo importa e o que quebrou quando
falhar — sem abrir um helper, sem conhecer a nossa taxonomia interna e sem saber
HTTP de cor.

Todas as regras abaixo saem daí. Quando duas regras conflitarem, ganha a que deixa
o código mais legível para essa pessoa.

Corolário que economiza discussão: **linguagem de negócio no que se lê, linguagem
técnica no que se prova.** O título diz "recusa o cadastro sem e-mail"; a asserção
é quem menciona `422`.

## Um arquivo por operação

Cada operação da API tem seu arquivo, com nome em português dizendo o que ela faz:

```
customers/
  criar-cliente.cy.js
  listar-clientes.cy.js
  consultar-cliente.cy.js
  alterar-cliente.cy.js
  excluir-cliente.cy.js
  _support/
```

Não agrupe por técnica de teste. `validacoes.cy.js` e `seguranca.cy.js` obrigam
quem procura um defeito a conhecer a nossa classificação antes de achar o teste;
"criar cliente está quebrado" precisa abrir **um** arquivo e ver ali tudo que pode
acontecer ao criar um cliente — o caminho feliz, o dado inválido e o acesso negado.

Uma capacidade coesa que não seja uma operação HTTP isolada (um fluxo de várias
chamadas, um upload com etapas) ganha arquivo próprio com o nome da capacidade.

## As camadas do `_support/`

Cada camada tem uma responsabilidade e uma proibição. A proibição é o que a mantém
útil: camada que faz de tudo deixa de dizer onde procurar.

| Arquivo | Responsabilidade | Nunca contém |
| --- | --- | --- |
| `_support/api.js` | as operações do recurso, sobre o client compartilhado | `cy.request`, asserção, limpeza, rota literal |
| `_support/factories.js` | os corpos enviados: válido, sem campo, com tipo errado, fronteiras | entidade persistida, request, asserção |
| `_support/helpers.js` | criar massa pela API, identidade, hooks, registro e limpeza | asserção, oráculo da ação principal |
| `_support/asserts.js` | as verificações que se repetem, recebendo respostas prontas | qualquer request |

O spec importa de `_support/` e do que a superfície do projeto oferece. **O spec
nunca fala HTTP direto** — `cy.request` mora no client compartilhado do projeto,
fora do recurso. `QAORQ-073`

Crie cada camada só quando ela tiver conteúdo: `factories.js` quando o recurso
aceitar corpo; `helpers.js` quando houver massa criada pela API, identidade
específica ou limpeza; `asserts.js` quando uma verificação se repetir — verificação
de um cenário só fica no próprio teste. Todo export precisa de consumidor dentro do
recurso, e todo import relativo precisa resolver.

**O `_support/` é uma fundação pequena, e o tamanho dele não acompanha o número de
cenários.** Um recurso com 250 casos de teste tem o mesmo `_support/` de um com 40:
uma função por operação da API, um construtor de corpo válido com sobrescritas, os
helpers de massa e limpeza, e as poucas verificações que se repetem. As quatro
camadas juntas costumam ficar entre 100 e 250 linhas.

Uma função por cenário é o erro a evitar: `criarClienteSemEmail`,
`criarClienteComEmailInvalido`, `criarClienteComEmailLongo` são o mesmo
`clienteValido({ email })` chamado com argumentos diferentes. Quem varia o dado é o
teste, no ponto onde a variação é lida — é isso que o construtor por sobrescrita
existe para permitir.

**O endereço da API e a montagem da request não são seus.** Eles vêm dos módulos
compartilhados que o projeto já tem (o client HTTP, o mapa de rotas, a autenticação),
listados na seção da superfície. Rota literal dentro do recurso é duplicação que
some do lugar onde alguém iria procurar quando a rota mudar.

## Nada de comando customizado do Cypress

Não escreva `Cypress.Commands.add`. As operações do recurso são funções importadas
de `_support/api.js`, e ponto.

São três motivos, e o terceiro é o que decide:

1. `cy.criarCliente()` aparece do nada — quem lê não sabe onde a função mora nem o
   que ela faz. `import { criarCliente } from "./_support/api.js"` é clicável.
2. Comando global vale para o projeto inteiro, então uma mudança sua atinge suítes
   de outros recursos que você não leu.
3. **Comando global não é verificável.** Se tudo é global, nenhum gate consegue
   provar que um spec respeitou as camadas — a regra vira honra, e honra não reprova.

A própria documentação do Cypress recomenda comando customizado só para o que é
usado em quase toda suíte e precisa encadear. Nada do que este recurso faz se
qualifica.

## A estrutura: três níveis, sempre os mesmos

```js
describe('Criar cliente', () => {
  context('quando os dados estão corretos', () => {
    it('cadastra o cliente e devolve os dados salvos', () => {});
  });
});
```

- **`describe`** — a operação. Um por arquivo, com o mesmo nome do arquivo em prosa.
- **`context`** — a circunstância. Começa sempre com **"quando"**.
- **`it`** — o que o sistema faz naquela circunstância.

`context` é alias de `describe` no Mocha: mesmo comportamento, palavra diferente.
A palavra diferente é o ponto — `describe` nomeia uma coisa, `context` nomeia uma
situação, e usar o mesmo verbo para as duas obriga o leitor a deduzir qual é qual
pela posição.

**Todo `it` mora dentro de um `context`**, mesmo que o arquivo tenha um só; e não
existe quarto nível. Se `context` fosse opcional, cada suíte sairia com uma cara.
`QAORQ-070`

Lidos de cima para baixo, os três viram uma frase: *Criar cliente, quando os dados
estão errados, recusa o cadastro sem e-mail.* Esse é o teste de aceite — leia em
voz alta; se não soar como português, algum dos três níveis está errado.

## O nome do teste

Um `it` bem escrito diz o que prova sem que ninguém precise abri-lo.

**Ele é o terceiro nível de uma frase só, então nunca repete o que os níveis de
cima já disseram.** "deve retornar erro ao criar cliente com e-mail inválido"
dentro de `Criar cliente` › `quando os dados estão errados` gagueja três vezes.

**O que é igual vem antes; o que muda vem no fim.** É isso que permite bater o olho:

```js
// o olho lê a linha inteira, toda vez
it('sem e-mail o cadastro é recusado', () => {});
it('recusa quando o nome está em branco', () => {});
it('e-mail repetido não é aceito', () => {});

// o resultado alinha numa coluna e o olho para no que difere
it('recusa o cadastro sem e-mail', () => {});
it('recusa o cadastro com nome em branco', () => {});
it('recusa o cadastro com e-mail já usado por outro cliente', () => {});
```

As regras: `QAORQ-071`

1. **Começa com verbo de ação no presente**, com o sistema como sujeito — `cadastra`,
   `recusa`, `devolve`, `impede`, `mantém`. Proibidos: `deve`, `testa`, `verifica`,
   `checa`, `valida`, `garante que`, `should`. `deve` é uma palavra repetida em toda
   linha do relatório que não carrega informação nenhuma.
2. **Até 80 caracteres.** O Cypress imprime esses títulos; título que quebra a linha
   deixa de ser escaneável, que é a razão de ele existir.
3. **Único dentro do seu `context`** — dois títulos iguais significam que pelo menos
   um deles não diz o que testa.
4. **Sem código de categoria no texto.** `CAT-04` é tag, não nome.
5. **O resultado não é um número.** `retorna 400` é o mecanismo; o comportamento é
   `recusa o cadastro`. O status vive na mensagem da asserção.

Fora da régua, e por isso escrito aqui em vez de cobrado por gate: o título precisa
carregar o **dado que distingue** este teste dos irmãos ("sem e-mail", "com 121
caracteres").

E **toda afirmação do título precisa ser comprovada**: "persiste" exige releitura e
comparação; "não cadastra" exige prova de ausência; "não altera" exige o estado
anterior e a comparação posterior; "não remove" exige confirmar que o registro
continua lá. Não podendo comprovar, ajuste o título — nunca prometa o que o teste
não demonstra.

| Como não | Como sim | Por quê |
| --- | --- | --- |
| `deve retornar 400 ao criar cliente sem email` | `recusa o cadastro sem e-mail` | sem `deve`, sem repetir o `describe`, sem o número |
| `testa validação de campos` | `recusa o cadastro com nome em branco` | dizia o que o teste faz, não o que o sistema faz |
| `CAT-06 - autenticação` | `recusa o cadastro de quem não fez login` | nome não é etiqueta |
| `deve criar cliente e retornar 201 e o id e a data` | `cadastra o cliente e devolve os dados salvos` | quatro afirmações num nome; o resto é asserção |

## Toda asserção diz o que esperava

```js
expect(resposta.status, 'cliente sem e-mail não pode ser cadastrado').to.equal(422);
```

Sem a mensagem, a falha diz `expected 201 to equal 422` — dois números e nenhuma
pista. Com ela, quem abre o relatório do CI entende o defeito sem ler o teste.
`QAORQ-072`

A mensagem descreve **o comportamento esperado**, em linguagem de negócio. Não
repita o nome do campo (`"id persistido"`), não descreva a mecânica (`"status
HTTP"`), não diga o óbvio (`"resposta válida"`).

**Vale principalmente dentro do `_support/asserts.js`.** É lá que a mensagem mais
importa, porque quem lê o spec não vê aquela linha — e é lá que ela costuma faltar.

## Identificadores e encadeamento

- **Nenhum nome de uma letra**, em lugar nenhum — parâmetro de `.then`, variável,
  argumento de `forEach`. `criacao`, `consulta`, `resposta`; nunca `r`, `c`, `x`.
  Quem lê `r.body.id` precisa subir três linhas para descobrir o que é `r`. `QAORQ-077`
- **Quem interpreta resposta é a camada de verificação, não o spec.** `.status`
  nunca aparece no spec fora de um `expect` com mensagem: ler status é julgar o
  resultado, e julgar é trabalho do `asserts.js`. `QAORQ-073`
- `.body` o spec pode ler, mas só para **pegar um valor e seguir** — o id recém-criado
  que a próxima chamada precisa. Ler `.body` para decidir se está certo é a mesma
  violação, escrita de outro jeito. E nada de `resposta.body.items[0].nome` solto no
  meio do teste: dê nome ao valor antes de usá-lo.
- Abra `.then` só para **ler um valor resolvido**. O Cypress já enfileira comandos
  na ordem em que aparecem; `.then` para ordenar é ruído.
- No máximo **dois níveis** de aninhamento (criar → agir → reler). Precisou de três,
  a preparação vai para `helpers.js`.
- A verificação é um comando por si (`validarClienteCriado(criacao);`), não um elo
  pendurado na cadeia; e nunca se devolve a cadeia de comandos com `return`.
- **Nada de `cy.wait` com tempo fixo** para sincronizar estado, e **nada de `if`,
  `else` ou ternário decidindo o que o teste verifica** — teste que escolhe o próprio
  oráculo em tempo de execução não prova nada. `QAORQ-074`

Um teste tem três momentos — preparar, agir e verificar — e marcá-los com
`// Arrange:`, `// Act:` e `// Assert:`, seguidos de uma frase curta, é o que
permite achar o ponto de falha sem ler o corpo inteiro. Marque quando houver
preparação de estado ou identidade, duas ou mais chamadas relevantes, releitura ou
encadeamento não óbvio; num teste de uma chamada e verificação direta, os
marcadores só poluem. A releitura de confirmação faz parte do verificar, não é uma
segunda ação.

Fora isso, comente só o que não é evidente: a origem de um limite, a razão de uma
ordem que precisa ser aquela, um campo derivado ou imutável, um defeito conhecido.
Comentário que narra a linha seguinte é ruído.

## Constantes e segredos

Valor de contrato não se traduz: se a API devolve `"CANCELLED"`, o teste usa esse
texto — com significado dado por constante nomeada em português
(`const STATUS_CANCELADO = "CANCELLED";`), preservando o literal. Chaves de payload
e de resposta ficam idênticas às do backend.

**Nenhuma URL, credencial, token ou senha literal no código.** `QAORQ-075`

E **não crie arquivo `.env`.** O Cypress não lê `.env`; ele lê sozinho qualquer
variável de ambiente prefixada com `CYPRESS_`, e a entrega em `Cypress.env()`:

```
CYPRESS_apiUrl=https://api.homolog.exemplo.com
CYPRESS_senhaDoUsuarioDeTeste=...
```

```js
const url = Cypress.env("apiUrl");
```

Assim o segredo nunca vira arquivo — não há o que esquecer no `.gitignore`, e o CI
injeta pelo próprio cofre de variáveis. Um `.env` criado por conveniência é a via
mais comum de credencial de homologação chegar ao repositório.

Segredo nunca entra em spec, factory, fixture, título de teste ou log.

Mascare `Authorization`, `Cookie`, `password`, `accessToken`, `refreshToken` e
equivalentes ao registrar qualquer coisa. Nada de `cy.log(JSON.stringify(resposta))`
nem de imprimir headers e corpos sem limpar antes.

Autorização e isolamento se testam com **identidades distintas e reais** — não
simule um perfil que não existe no ambiente. E `failOnStatusCode: false` só quando o
teste realmente afirmar algo sobre o erro; caso contrário, ele silencia a falha que
deveria aparecer.

## Data-driven

Mesma ação e mesmo oráculo, muda só o dado → `forEach` sobre lista literal.
Muda o que se prova → `it` explícito.

- **Sempre:** varreduras por campo (campo ausente, tipo errado, fronteira).
- **Nunca:** fluxo positivo principal e regra de negócio — forçá-los numa tabela
  produz `if` por caso e dissolve o oráculo.

O array precisa ser **literal, no próprio spec**, só com dado (texto, número,
booleano, `null`, objeto e array desses). Função dentro do caso invalida o bloco
inteiro para o resolvedor estático, e o sintoma engana: o gate aponta a tag, e a
causa está na função escondida na lista. Construa a massa no corpo do `it`.

O título é template e precisa resolver **único** por caso, usando identificador
simples ou acesso pontilhado (`${campo}`) — um ternário no título não resolve.
Cada caso carrega o texto pronto de que o título precisa.

A tag `@campo` é **por iteração**, nunca a lista de campos toda acumulada numa tag só:

```js
[
  { campo: "email", valor: null, esperado: "nulo" },
  { campo: "email", valor: 42, esperado: "tipo errado" },
].forEach(({ campo, valor, esperado }) => {
  // @endpoint POST /api/v1/customers  @cat CAT-03  @campo ${campo}
  it(`recusa o cadastro com ${campo} ${esperado}`, () => {});
});
```

## Massa, isolamento e limpeza

- Cada `it` roda sozinho: sem depender de ordem nem de resultado de outro teste.
- Dados únicos por execução.
- **Registre o recurso criado para limpeza antes da primeira asserção que possa
  interromper o fluxo** — e registre junto a identidade capaz de excluí-lo.
- Limpeza em `afterEach`, tentando todos os itens e agregando as falhas ao final:
  não abandone a fila no primeiro status inesperado.
- A limpeza **confere o resultado** do que apagou. `DELETE` sem verificação é
  limpeza cega, e deixa massa para trás sem ninguém notar.
- Em operação rejeitada, registre também a criação indevida, caso o backend
  devolva um identificador.
- **Nunca compartilhe id** por `Cypress.env`, variável global mutável ou efeito de
  um teste anterior. Id que já existia no ambiente vira constante nomeada na
  factory, com um comentário dizendo de onde ele veio.
- `factories.js` constrói **apenas entradas**; entidade persistida nasce de request
  real, pelo `helpers.js`.
- Configuração global alterada é **restaurada ao valor anterior**, não apagada.

## Tags de rastreabilidade

Toda `it` leva uma linha de comentário `//` **imediatamente acima**, sem linha em
branco nem código entre ela e o teste:

```js
// @endpoint POST /api/v1/customers  @cat CAT-03  @campo email  @alvo email-formato
it('recusa o cadastro com e-mail sem arroba', () => {});
```

- **`@endpoint`** (obrigatória): método e rota completa, exatamente como no gabarito.
- **`@cat`** (obrigatória): uma por `it`. Cenário que provaria duas categorias vira
  dois testes.
- **`@campo`** (obrigatória em `CAT-02`, `CAT-03` e `CAT-04`): o campo exercitado,
  com o nome do schema. É a **única prova de cobertura por campo** — nenhum gate
  estático deduz do corpo do teste qual campo recebeu o valor inválido.
- **`@alvo`** (opcional): slug curto que diferencia testes do mesmo endpoint,
  categoria e campo.
- **`@bug`** (opcional): teste vermelho por defeito **confirmado por execução**,
  nunca por suspeita, com a linha `// bug: <o que o contrato exige>; <o que a API faz
  hoje>` logo abaixo.

A tag é índice, não oráculo: declara o que o teste pretende cobrir; quem prova é a
asserção.

## Como as camadas se encaixam

Fragmentos, de propósito — o que segue mostra a FORMA de cada camada, não o
tamanho dela. Os nomes de `apiRequest`, `RotasApi` e `tokenPadrao` são
ilustrativos: use os que a seção da superfície listar.

```js
// _support/api.js — a operação. Sem asserção, sem cy.request, sem rota literal.
export function criarCliente({ token, corpo }) {
  return apiRequest({ metodo: "POST", url: RotasApi.customers.raiz, corpo, token });
}

// _support/factories.js — só entradas, montadas por sobrescrita do caso válido.
export function clienteValido(sobrescritas = {}) {
  return { name: "Cliente Exemplo", email: "cliente@exemplo.com", ...sobrescritas };
}

// _support/helpers.js — cria massa de verdade e registra a limpeza. Sem oráculo.
export function criarClienteParaTeste({ token, corpo }) {
  return criarCliente({ token, corpo }).then((criacao) => {
    // Registrado ANTES de qualquer verificação: asserção que falha interrompe o
    // teste, e o cliente já existe no banco mesmo assim.
    if (criacao.body?.id) criados.push({ id: criacao.body.id, token });
    return criacao;
  });
}

// _support/asserts.js — recebe resposta pronta. Nunca faz request.
export function validarClienteCadastrado(criacao, enviado) {
  expect(criacao.status, "cliente com dados válidos deve ser cadastrado").to.equal(201);
  expect(criacao.body.name, "o nome salvo deve ser o que foi enviado").to.equal(enviado.name);
}
```

E o spec que consome as quatro:

```js
// criar-cliente.cy.js
describe("Criar cliente", () => {
  afterEach(() => {
    limparClientesCriados();
  });

  context("quando os dados estão corretos", () => {
    // @endpoint POST /api/v1/customers  @cat CAT-01
    it("cadastra o cliente e devolve os dados salvos", () => {
      // Arrange: um cliente completo e uma identidade com permissão
      const token = tokenPadrao();
      const corpo = clienteValido();

      // Act:
      criarClienteParaTeste({ token, corpo }).then((criacao) => {
        // Assert: a resposta confirma, e a releitura prova que ficou salvo
        validarClienteCadastrado(criacao, corpo);

        obterCliente({ token, id: criacao.body.id }).then((consulta) => {
          expect(consulta.body.name, "o cliente deve continuar salvo depois").to.equal(corpo.name);
        });
      });
    });
  });

  context("quando os dados estão errados", () => {
    // @endpoint POST /api/v1/customers  @cat CAT-02  @campo email
    it("recusa o cadastro sem e-mail", () => {
      criarClienteParaTeste({ token: tokenPadrao(), corpo: clienteSemEmail() }).then((resposta) => {
        validarCadastroRecusado(resposta, "e-mail é obrigatório");
      });
    });
  });
});
```

Repare no que o spec **não** tem: nenhum `cy.request`, nenhuma rota escrita à mão,
nenhum status interpretado fora de um `expect` com mensagem, nenhum `if`. E repare
no que ele tem: os três níveis lidos como frase, e cada `it` dizendo o que prova
antes de qualquer um abrir o corpo.

## O que nunca se produz

- `it.only`, `.skip`, teste vazio ou só comentado;
- asserção que só prova existência — `status < 500`, `body existe`, `success === true`,
  `items` é array. Ela passa com o backend devolvendo lixo. `QAORQ-076`
- repetir o valor devolvido pela própria resposta como expectativa;
- procurar um pedaço de texto genérico como única prova de que o erro é o certo;
- aceitar vários status como corretos sem dizer por que cada um deles é;
- inventar uma linguagem própria de teste (`testarCrudPadrao(...)`) para diminuir
  linhas: abstração que esconde a ação e o oráculo custa mais do que economiza;
- teste negativo que não prova ausência de efeito havendo leitura segura;
- duas ações principais independentes no mesmo `it`;
- helper que esconde a request **e** todas as asserções;
- rota, status, envelope, autenticação ou paginação inventados;
- camada inventada ao lado das quatro (`contrato.js`, `perfis.js`), dois nomes para
  a mesma operação, ou qualquer forma de *router harness*, serviço em memória e
  repositório falso — o teste de API exercita a API, não uma imitação dela;
- **rebaixar a expectativa para deixar a suíte verde.** Divergência entre contrato e
  implementação é defeito a sinalizar, não expectativa a ajustar. Havendo execução
  que comprove, o caminho é `@bug`.

## O que o gate confere

O resto desta norma é julgamento, e vale igual — só não tem quem o cobre
automaticamente.

| Código | Reprova |
| --- | --- |
| `QAORQ-070` | `it` fora de `context`, `context` que não começa com "quando", quarto nível de aninhamento |
| `QAORQ-071` | título com verbo proibido, acima de 80 caracteres, repetido no mesmo `context`, citando `CAT-xx`, ou cujo resultado é só um número |
| `QAORQ-072` | `expect` sem mensagem |
| `QAORQ-073` | `cy.request` dentro do recurso, `Cypress.Commands.add`, ou `.status` lido num statement sem asserção |
| `QAORQ-074` | `cy.wait` com número, ou `if`/`else` dentro do corpo de um teste |
| `QAORQ-075` | credencial literal: `Bearer <valor>` ou JWT escrito no código |
| `QAORQ-076` | teste cujas asserções só provam existência ou formato |
| `QAORQ-077` | identificador de uma letra |

O que **não** tem fiscal, e por isso vale como julgamento: se o import do spec veio
de camada permitida (depende da superfície de cada projeto), se a mensagem da
asserção realmente explica, e se o nome do teste descreve comportamento em vez de
mecanismo. Regra prometida e não cobrada ensina a não levar a sério o que está
escrito — se você ler uma ameaça de gate que não está na tabela acima, é engano.
