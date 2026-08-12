"""Tags `@endpoint` / `@cat` / `@campo` de um spec Cypress — parser puro.

Entra o texto do `.cy.js`, sai o que ele declara cobrir. Sem I/O e sem dependência
do projeto, como todo módulo deste pacote.

Autoridade
----------
Até o desacoplamento da skill, quem contava cobertura era o `qa-cobertura.mjs` e
o que saía daqui servia só para **nomear** o que faltou num delta. Agora não: com
`gates/cobertura.py`, este parser é o numerador da conta. Por isso ele deixou de
apenas contar as tags que não resolveu e passou a **resolver** as de varredura.

O resolvedor de varredura
-------------------------
A norma manda escrever varredura por campo em tabela (`QAORQ-079`), e tabela
produz `@campo ${campo}` — uma tag que só existe resolvida em tempo de execução.
Contar isso como "campo não testado" puniria exatamente a forma que a norma
exige. Então, quando a tag está dentro de um `forEach` sobre **lista literal**, os
valores saem da lista e a tag vira uma por caso.

São as mesmas condições que o prompt do executor já declara: lista literal, no
próprio spec, com interpolação de identificador simples. Fora delas o parser não
inventa — marca como dinâmica, e quem consome sabe que a conta está incompleta.

Separado de `exports_javascript.py` porque muda por outro motivo: aquele acompanha
a sintaxe de `export` do JavaScript, este acompanha a convenção de marcação que a
norma de código exige em cada `it`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orquestrador.analise_estatica.estrutura_de_suite import neutralizar

# A linha de marcação de um `it`: `@endpoint MÉTODO /rota  @cat CAT-07  @campo email`.
# A rota vai até dois espaços ou o próximo `@`, porque as outras tags vêm alinhadas
# depois dela.
_LINHA = re.compile(r"@endpoint\s+(?P<endpoint>[A-Z]+\s+[^\s@]+)(?P<resto>[^\n]*)")
_CAT = re.compile(r"@cat\s+([^\s@]+)")
# Até o próximo `@` ou o fim da linha, porque a norma permite lista separada por
# vírgula: `@campo externalCode, name`. Recortar no primeiro espaço perdia o
# segundo campo em silêncio — e silêncio, aqui, vira lacuna de cobertura inventada.
_CAMPO = re.compile(r"@campo\s+([^@\n]+)")

# `].forEach(` — o começo de um bloco de varredura sobre lista literal.
_VARREDURA = re.compile(r"\]\s*\.\s*forEach\s*\(")
_INTERPOLACAO = re.compile(r"\$\{\s*([\w$.]+)\s*\}")


@dataclass(frozen=True)
class TagsDoSpec:
    """O que as tags de um spec declaram cobrir, e o quanto disso é confiável.

    `dinamicas` conta as tags cuja interpolação o parser NÃO conseguiu resolver —
    template fora de um `forEach` sobre lista literal, ou valor que não é dado. Elas
    existem e cobrem alguma coisa; simplesmente não se sabe o quê. Quem usa os
    conjuntos precisa tratar `dinamicas > 0` como conta incompleta, e não fingir
    que a ausência de um par é prova de lacuna.
    """

    pares: set[tuple[str, str]]
    campos: set[tuple[str, str]]
    dinamicas: int


@dataclass(frozen=True)
class _Varredura:
    inicio: int
    fim: int
    valores: dict[str, tuple[str, ...]]


def _casar_para_tras(neutro: str, fim_do_colchete: int) -> int | None:
    """Do `]` de volta até o `[` que o abriu."""
    profundidade = 0
    for indice in range(fim_do_colchete, -1, -1):
        if neutro[indice] == "]":
            profundidade += 1
        elif neutro[indice] == "[":
            profundidade -= 1
            if profundidade == 0:
                return indice
    return None


def _casar_para_frente(neutro: str, inicio: int) -> int | None:
    """Do `{` do corpo até o `}` que o fecha."""
    profundidade = 0
    for indice in range(inicio, len(neutro)):
        if neutro[indice] == "{":
            profundidade += 1
        elif neutro[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return indice
    return None


def _varreduras(fonte: str, neutro: str) -> list[_Varredura]:
    """Os blocos `[...].forEach(...)` com os valores literais de cada chave."""
    achados: list[_Varredura] = []
    for casamento in _VARREDURA.finditer(neutro):
        abre = _casar_para_tras(neutro, casamento.start())
        if abre is None:
            continue
        # O corpo começa depois do `=>`, não na primeira chave: a chave anterior é
        # a desestruturação do parâmetro (`({ campo, esperado }) => {`), e casar
        # nela daria um bloco de duas palavras que não contém tag nenhuma.
        seta = neutro.find("=>", casamento.end())
        if seta == -1:
            continue
        corpo = neutro.find("{", seta)
        if corpo == -1:
            continue
        fim = _casar_para_frente(neutro, corpo)
        if fim is None:
            continue
        lista = fonte[abre : casamento.start() + 1]
        # A chave é lida no fonte ORIGINAL: o neutralizado apagou os valores, que
        # são justamente o que interessa.
        valores: dict[str, list[str]] = {}
        for par in re.finditer(r"([\w$]+)\s*:\s*[\"'`]([^\"'`]*)[\"'`]", lista):
            valores.setdefault(par.group(1), []).append(par.group(2))
        achados.append(
            _Varredura(
                inicio=corpo,
                fim=fim,
                valores={chave: tuple(lista) for chave, lista in valores.items()},
            )
        )
    return achados


def _resolver(valor: str, varredura: _Varredura | None) -> list[str] | None:
    """O valor com a interpolação expandida, ou `None` quando não dá para saber."""
    interpolacoes = _INTERPOLACAO.findall(valor)
    if not interpolacoes:
        return [valor]
    if varredura is None or len(interpolacoes) != 1:
        return None
    # `caso.campo` e `campo` casam com a mesma chave: a desestruturação do
    # `forEach(({ campo }) => ...)` e o acesso pontilhado nomeiam o mesmo dado.
    chave = interpolacoes[0].rsplit(".", 1)[-1]
    possiveis = varredura.valores.get(chave)
    if not possiveis:
        return None
    return [_INTERPOLACAO.sub(escolha.replace("\\", "\\\\"), valor) for escolha in possiveis]


def extrair_tags(fonte: str) -> TagsDoSpec:
    """O que o spec declara cobrir, com as varreduras expandidas."""
    neutro = neutralizar(fonte)
    varreduras = _varreduras(fonte, neutro)

    pares: set[tuple[str, str]] = set()
    campos: set[tuple[str, str]] = set()
    dinamicas = 0

    for linha in _LINHA.finditer(fonte):
        dona = next(
            (v for v in varreduras if v.inicio < linha.start() < v.fim),
            None,
        )
        endpoints = _resolver(" ".join(linha.group("endpoint").split()), dona)
        if endpoints is None:
            dinamicas += 1
            continue

        resto = linha.group("resto")
        for bruto in _CAT.findall(resto):
            resolvidos = _resolver(bruto, dona)
            if resolvidos is None:
                dinamicas += 1
                continue
            pares.update((endpoint, cat) for endpoint in endpoints for cat in resolvidos)
        for bruto in _CAMPO.findall(resto):
            for parte in bruto.split(","):
                nome = parte.strip()
                if not nome:
                    continue
                resolvidos = _resolver(nome, dona)
                if resolvidos is None:
                    dinamicas += 1
                    continue
                campos.update((endpoint, campo) for endpoint in endpoints for campo in resolvidos)

    return TagsDoSpec(pares=pares, campos=campos, dinamicas=dinamicas)
