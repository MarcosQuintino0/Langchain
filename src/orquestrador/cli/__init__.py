"""A interface de linha de comando: argumentos, apresentação e códigos de saída.

Quatro módulos, quatro razões de mudar: `codigos_de_saida.py` quando o contrato
com quem automatiza mudar, `init.py` quando a configuração ganhar campo,
`doctor.py` quando o ambiente que uma execução real exige mudar, e
`principal.py` quando a execução mudar de forma.

`init` e `doctor` moram aqui, e não em `ferramentas/`, porque o que eles
produzem é **apresentação**: um arquivo comentado para uma pessoa preencher e
uma lista de vereditos para uma pessoa ler. O I/O de que precisam vem pronto
dos adaptadores; o que esta camada acrescenta é o texto que diz o que fazer
quando cada item falha.
"""
