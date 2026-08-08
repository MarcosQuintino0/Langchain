"""O que existe no código-fonte, sem executá-lo.

Responde perguntas sobre o código dos dois lados — que nomes um módulo do projeto
de testes exporta, com que assinatura, que tags de cobertura um spec declara, que
endpoints HTTP o backend expõe — lendo o texto e nada além dele. Nenhum módulo
daqui roda Node, abre subprocesso ou fala com a rede: a resposta vem da leitura,
nunca da execução.

O backend entrou aqui junto com o diff grafo × manifesto do Gate A, e pelo mesmo
critério dos outros: a rota de um endpoint é sintaxe da linguagem e convenção do
framework, não CLI de terceiro. Quem não sabe ler uma linguagem **diz que não
sabe** — a matriz de suporte é declarada em `extrator_de_endpoints`, e o que está
fora dela aparece no resultado em vez de virar silêncio.

Ler arquivo do disco é permitido, e é o único I/O que este pacote faz. A fronteira
com `ferramentas/` não é "toca em `Path` ou não", é **o que muda o módulo**:
`ferramentas/` acompanha a CLI, o código de retorno e o formato de saída de uma
ferramenta de terceiro; `analise_estatica/` acompanha a sintaxe da linguagem e a
convenção de marcação da skill. Enquanto o parser de `export` e o localizador dos
módulos compartilhados moravam em pacotes diferentes, quem procurava "onde se lê o
JavaScript" tinha que saber os dois lugares.

Este pacote **não reexporta nada**. Importe do módulo que define o símbolo: um
atalho aqui criaria uma segunda rota de import para o mesmo nome, e duas rotas é
como se perde a resposta para "quem é o dono disto".
"""
