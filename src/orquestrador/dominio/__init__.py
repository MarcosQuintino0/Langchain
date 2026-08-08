"""Contratos e regras puras: o vocabulário comum entre todos os estágios.

Nada aqui abre, lê, escreve, lista ou resolve caminho; nada roda subprocesso; e
nenhum módulo daqui importa outro subpacote de `orquestrador` além de
`excecoes`. `Path` entra só como valor — `Recurso.caminho_testes` é álgebra de
caminho, não acesso a disco.

A regra tem teste: `test_invariante_estrutura_do_codigo.py::test_dominio_nao_toca_no_disco`.
"""
