"""O que existe no código-fonte, sem executá-lo.

Responde perguntas sobre o JavaScript do projeto de testes — que nomes um módulo
exporta, com que assinatura, que tags de cobertura um spec declara — lendo o texto
e nada além dele. Nenhum módulo daqui roda Node, abre subprocesso ou fala com a
rede: a resposta vem da leitura, nunca da execução.

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
