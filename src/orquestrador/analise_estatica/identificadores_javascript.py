"""O que um módulo JS liga e o que ele chama — parser puro.

Entra o texto, saem três conjuntos: os nomes que o arquivo **importa**, os que ele
**liga** por conta própria (declaração, parâmetro, desestruturação) e os que ele
**chama**. Chamar um nome que não está em nenhum dos dois primeiros é `ReferenceError`
na primeira execução.

Por que isto não precisa da superfície do projeto
-------------------------------------------------
A primeira ideia foi cruzar o que o spec usa com os exports que o projeto oferece.
Não é preciso: o que vem do projeto chega por `import`, então já está no primeiro
conjunto. A pergunta "de onde vem este nome?" se responde dentro do próprio
arquivo, e uma regra que não depende do projeto do cliente vale em qualquer um.

Separado de `exports_javascript.py` porque olha para o outro lado do módulo — lá é
o que ele oferece, aqui é o que ele consome — e de `estrutura_de_suite.py` porque
aquele acompanha a convenção de teste, e este a sintaxe de ligação do JavaScript.

O que ele deliberadamente não faz: escopo. Um nome ligado dentro de uma função
conta como ligado no arquivo inteiro. É falso negativo assumido — sombra de escopo
não é o defeito que esta análise procura, e distingui-la exigiria um parser de
verdade em troca de nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orquestrador.analise_estatica.estrutura_de_suite import neutralizar

# O que o ambiente oferece sem ninguém importar: JavaScript, Mocha/Chai e Cypress.
# Lista fechada porque é vocabulário de plataforma — não muda de projeto para
# projeto, e o que muda de projeto chega por import, que é justamente o ponto.
GLOBAIS: frozenset[str] = frozenset(
    {
        # Mocha e Chai
        "describe",
        "context",
        "it",
        "specify",
        "xit",
        "xdescribe",
        "before",
        "beforeEach",
        "after",
        "afterEach",
        "expect",
        "assert",
        # Cypress
        "cy",
        "Cypress",
        # JavaScript
        "Array",
        "Boolean",
        "Date",
        "Error",
        "JSON",
        "Map",
        "Math",
        "Number",
        "Object",
        "Promise",
        "RegExp",
        "Set",
        "String",
        "Symbol",
        "BigInt",
        "console",
        "decodeURIComponent",
        "encodeURIComponent",
        "isNaN",
        "parseFloat",
        "parseInt",
        "structuredClone",
        "require",
        "window",
        "globalThis",
        "fetch",
        "URL",
        "URLSearchParams",
        "TextEncoder",
        "Intl",
        "Function",
        "Proxy",
        "Reflect",
        "WeakMap",
        "WeakSet",
    }
)

_IMPORT_CHAVES = re.compile(r"import\s*(?:[\w$]+\s*,\s*)?\{(?P<nomes>[^}]*)\}\s*from")
_IMPORT_SIMPLES = re.compile(r"import\s+(?P<nome>[\w$]+)\s*(?:,\s*\{[^}]*\}\s*)?from")
_IMPORT_ESTRELA = re.compile(r"import\s*\*\s*as\s+(?P<nome>[\w$]+)\s+from")

_DECLARACAO = re.compile(r"(?<![\w$])(?:function|class)\s*\*?\s*(?P<nome>[\w$]+)")
_ATRIBUICAO = re.compile(r"(?<![\w$])(?:const|let|var)\s+(?P<alvo>[^=;]+?)\s*[=;]")
_PARAMETROS = re.compile(
    r"(?:function\s*\*?\s*[\w$]*\s*|(?<![\w$]))\((?P<lista>[^()]*)\)\s*(?:=>|\{)"
)
_SETA_SEM_PARENTESES = re.compile(r"(?<![\w$.])(?P<nome>[\w$]+)\s*=>")
_CAPTURA = re.compile(r"catch\s*\(\s*(?P<nome>[\w$]+)\s*\)")

# `nome(` que não é acesso a propriedade: `objeto.metodo()` é do objeto, não do
# arquivo, e cobrar por ele acusaria toda chamada encadeada do Cypress.
_CHAMADA = re.compile(r"(?<![\w$.])(?P<nome>[A-Za-z_$][\w$]*)\s*\(")

# Palavra que é sintaxe, não nome: `if (`, `for (`, `return (` e afins casariam
# com o padrão de chamada e não são identificador nenhum.
_PALAVRAS_RESERVADAS: frozenset[str] = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "typeof",
        "instanceof",
        "new",
        "delete",
        "void",
        "await",
        "yield",
        "function",
        "class",
        "do",
        "else",
        "in",
        "of",
        "with",
        "throw",
        "case",
        "import",
        "export",
        "super",
    }
)

_NOME = re.compile(r"[A-Za-z_$][\w$]*")


@dataclass(frozen=True)
class UsoDeIdentificadores:
    importados: frozenset[str]
    ligados: frozenset[str]
    chamados: frozenset[str]

    @property
    def disponiveis(self) -> frozenset[str]:
        return self.importados | self.ligados | GLOBAIS

    def nao_resolvidos(self) -> frozenset[str]:
        """Nomes chamados que nada neste arquivo fornece."""
        return frozenset(self.chamados - self.disponiveis)


def _nomes_de(trecho: str) -> set[str]:
    """Os identificadores de um trecho de ligação, ignorando o que é valor.

    `{ id, token }` liga `id` e `token`; `{ nome: apelido }` liga `apelido`; um
    valor padrão (`= "abc"`) não liga nada. Tudo que sobra depois do `=` sai fora.
    """
    sem_padrao = re.sub(r"=[^,]*", "", trecho)
    return {nome for nome in _NOME.findall(sem_padrao) if nome not in _PALAVRAS_RESERVADAS}


def analisar_identificadores(fonte: str) -> UsoDeIdentificadores:
    """Os três conjuntos, lidos do texto neutralizado."""
    neutro = neutralizar(fonte)

    importados: set[str] = set()
    for casamento in _IMPORT_CHAVES.finditer(fonte):
        for parte in casamento.group("nomes").split(","):
            nome = parte.split(" as ")[-1].strip()
            if nome:
                importados.add(nome)
    for padrao in (_IMPORT_SIMPLES, _IMPORT_ESTRELA):
        for casamento in padrao.finditer(fonte):
            importados.add(casamento.group("nome"))

    ligados: set[str] = set()
    for casamento in _DECLARACAO.finditer(neutro):
        ligados.add(casamento.group("nome"))
    for casamento in _ATRIBUICAO.finditer(neutro):
        ligados |= _nomes_de(casamento.group("alvo"))
    for casamento in _PARAMETROS.finditer(neutro):
        ligados |= _nomes_de(casamento.group("lista"))
    for casamento in _SETA_SEM_PARENTESES.finditer(neutro):
        ligados.add(casamento.group("nome"))
    for casamento in _CAPTURA.finditer(neutro):
        ligados.add(casamento.group("nome"))

    chamados = {
        casamento.group("nome")
        for casamento in _CHAMADA.finditer(neutro)
        if casamento.group("nome") not in _PALAVRAS_RESERVADAS
    }

    return UsoDeIdentificadores(
        importados=frozenset(importados),
        ligados=frozenset(ligados),
        chamados=frozenset(chamados),
    )
