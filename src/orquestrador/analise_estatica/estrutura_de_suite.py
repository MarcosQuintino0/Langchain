"""A árvore `describe` / `context` / `it` de um spec Cypress — parser puro.

Entra o texto do `.cy.js`, sai o aninhamento que ele declara, com o título e a
linha de cada bloco. Sem I/O e sem dependência do projeto, como todo módulo deste
pacote.

Separado de `tags_cypress.py` porque muda por outro motivo: aquele acompanha a
convenção de marcação (`@endpoint`, `@cat`), este acompanha a **estrutura** que a
norma de código exige — operação no `describe`, circunstância no `context`,
comportamento no `it`. Um lê comentário, o outro lê código.

Por que um neutralizador em vez de casar chave com regex
--------------------------------------------------------
Contar `{` e `}` direto no fonte quebra no primeiro `it("um { aberto")` e em todo
bloco `/* comentado */`. `neutralizar` devolve uma cópia do texto com o conteúdo
de string e de comentário trocado por espaço, preservando posição e quebra de
linha: as chaves que sobram são todas de verdade, e o deslocamento continua
servindo para recortar o título do fonte original.

O que este parser não faz: resolver template. Título em crase com `${campo}` é
lido como dinâmico e marcado — a forma data-driven é legítima, e fingir que se
leu o título resolvido produziria falso positivo de duplicidade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# `describe(`, `context(`, `it(` e os apelidos do Mocha, fora de acesso a
# propriedade (`objeto.it(` não é bloco de teste).
_CHAMADA = re.compile(
    r"(?<![\w$.])(?P<tipo>describe|context|it|specify)(?:\.(?P<modificador>only|skip))?\s*\("
)

_ABERTURA_DE_CORPO = re.compile(r"(=>|\))\s*$")

TIPOS_DE_GRUPO = frozenset({"describe", "context"})
TIPOS_DE_TESTE = frozenset({"it", "specify"})


@dataclass(frozen=True)
class Bloco:
    """Um `describe`, `context` ou `it`, com o que ele contém.

    `dinamico` marca título em template, com `${campo}` dentro: o texto está ali,
    mas não resolvido. Quem checa duplicidade ou tamanho precisa saber disso, ou
    acusaria dois `it` de uma varredura data-driven como se fossem o mesmo.
    """

    tipo: str
    titulo: str
    linha: int
    # Os limites do corpo `{...}`, em deslocamento no fonte. É por eles que o gate
    # pergunta "este `expect` está dentro de qual `it`?" — sem recortar o texto de
    # novo com outra heurística, que é como duas leituras do mesmo arquivo passam a
    # discordar.
    inicio: int = 0
    fim: int = 0
    dinamico: bool = False
    modificador: str | None = None
    filhos: tuple[Bloco, ...] = field(default_factory=tuple)

    def contem(self, deslocamento: int) -> bool:
        return self.inicio < deslocamento < self.fim


def neutralizar(fonte: str) -> str:
    """Cópia do fonte com string e comentário virados espaço, mesma indexação."""
    saida = list(fonte)
    indice = 0
    total = len(fonte)
    while indice < total:
        caractere = fonte[indice]
        if caractere in "\"'`":
            aspas = caractere
            indice += 1
            while indice < total:
                if fonte[indice] == "\\":
                    saida[indice] = " "
                    if indice + 1 < total:
                        saida[indice + 1] = " "
                    indice += 2
                    continue
                if fonte[indice] == aspas:
                    break
                if fonte[indice] != "\n":
                    saida[indice] = " "
                indice += 1
            indice += 1
            continue
        if caractere == "/" and fonte[indice : indice + 2] == "//":
            while indice < total and fonte[indice] != "\n":
                saida[indice] = " "
                indice += 1
            continue
        if caractere == "/" and fonte[indice : indice + 2] == "/*":
            while indice < total and fonte[indice : indice + 2] != "*/":
                if fonte[indice] != "\n":
                    saida[indice] = " "
                indice += 1
            saida[indice : indice + 2] = "  "
            indice += 2
            continue
        indice += 1
    return "".join(saida)


def _titulo(fonte: str, neutro: str, apos_parentese: int) -> tuple[str, bool, int] | None:
    """O primeiro argumento string da chamada: (texto, dinâmico, posição final)."""
    indice = apos_parentese
    while indice < len(neutro) and neutro[indice].isspace():
        indice += 1
    if indice >= len(neutro) or neutro[indice] not in "\"'`":
        return None
    aspas = neutro[indice]
    fim = neutro.find(aspas, indice + 1)
    if fim == -1:
        return None
    bruto = fonte[indice + 1 : fim]
    return bruto, aspas == "`" or "${" in bruto, fim + 1


def _corpo(neutro: str, desde: int) -> tuple[int, int] | None:
    """O par de chaves do corpo do bloco, pulando objeto de opções.

    A chave que interessa é a que vem depois de `=>` ou de `)` — a de
    `it("x", { tags: [...] }, () => {` vem depois de vírgula, e seria a errada.
    """
    indice = desde
    while indice < len(neutro):
        if neutro[indice] == "{" and _ABERTURA_DE_CORPO.search(neutro[desde:indice].rstrip()):
            profundidade = 0
            for fim in range(indice, len(neutro)):
                if neutro[fim] == "{":
                    profundidade += 1
                elif neutro[fim] == "}":
                    profundidade -= 1
                    if profundidade == 0:
                        return indice, fim
            return None
        if neutro[indice] == ";":
            return None
        indice += 1
    return None


def extrair_estrutura(fonte: str) -> tuple[Bloco, ...]:
    """Os blocos de topo do spec, cada um com os filhos aninhados."""
    neutro = neutralizar(fonte)
    achados: list[tuple[int, int, str, str, bool, str | None]] = []
    for casamento in _CHAMADA.finditer(neutro):
        lido = _titulo(fonte, neutro, casamento.end())
        if lido is None:
            continue
        titulo, dinamico, apos_titulo = lido
        corpo = _corpo(neutro, apos_titulo)
        if corpo is None:
            continue
        inicio, fim = corpo
        achados.append(
            (
                inicio,
                fim,
                casamento.group("tipo"),
                titulo,
                dinamico,
                casamento.group("modificador"),
            )
        )

    achados.sort(key=lambda achado: (achado[0], -achado[1]))
    linhas_ate = _linhas_acumuladas(fonte)
    return _aninhar(achados, linhas_ate)


def _linhas_acumuladas(fonte: str) -> list[int]:
    """Deslocamento inicial de cada linha, para traduzir posição em número."""
    posicoes = [0]
    for indice, caractere in enumerate(fonte):
        if caractere == "\n":
            posicoes.append(indice + 1)
    return posicoes


def _numero_da_linha(posicoes: list[int], deslocamento: int) -> int:
    baixo, alto = 0, len(posicoes) - 1
    while baixo < alto:
        meio = (baixo + alto + 1) // 2
        if posicoes[meio] <= deslocamento:
            baixo = meio
        else:
            alto = meio - 1
    return baixo + 1


def _aninhar(
    achados: list[tuple[int, int, str, str, bool, str | None]], posicoes: list[int]
) -> tuple[Bloco, ...]:
    """Monta a árvore por continência: quem começa dentro do corpo do outro é filho."""

    def construir(indice: int, limite: int) -> tuple[list[Bloco], int]:
        blocos: list[Bloco] = []
        while indice < len(achados) and achados[indice][0] < limite:
            inicio, fim, tipo, titulo, dinamico, modificador = achados[indice]
            filhos, indice = construir(indice + 1, fim)
            blocos.append(
                Bloco(
                    tipo=tipo,
                    titulo=titulo,
                    linha=_numero_da_linha(posicoes, inicio),
                    inicio=inicio,
                    fim=fim,
                    dinamico=dinamico,
                    modificador=modificador,
                    filhos=tuple(filhos),
                )
            )
        return blocos, indice

    raizes, _ = construir(0, len(posicoes) and 10**9)
    return tuple(raizes)


def literais(fonte: str, neutro: str) -> list[tuple[int, str]]:
    """Os literais de texto do fonte: (deslocamento do conteúdo, conteúdo).

    Sai do neutralizado justamente porque lá as aspas de dentro de comentário já
    foram apagadas — procurar aspas no fonte cru faria um `// não use "aspas"`
    abrir um literal que nunca fecha, e o resto do arquivo viraria texto.
    """
    achados: list[tuple[int, str]] = []
    indice = 0
    while indice < len(neutro):
        if neutro[indice] in "\"'`":
            aspas = neutro[indice]
            fim = neutro.find(aspas, indice + 1)
            if fim == -1:
                break
            achados.append((indice + 1, fonte[indice + 1 : fim]))
            indice = fim + 1
            continue
        indice += 1
    return achados


@dataclass(frozen=True)
class Chamada:
    """Uma chamada `nome(...)`, com os argumentos separados no nível de topo."""

    inicio: int
    fim: int
    argumentos: tuple[str, ...]


def chamadas(neutro: str, nome: str) -> list[Chamada]:
    """As chamadas de `nome` no texto já neutralizado, com os argumentos.

    Separar argumento no nível de topo é o que distingue `expect(valor, "por quê")`
    de `expect(valor).to.have.property("campo", null)`: a vírgula da segunda está
    dentro de outro par de parênteses, e contá-la daria a asserção por explicada.
    """
    padrao = re.compile(rf"(?<![\w$.]){re.escape(nome)}\s*\(")
    achados: list[Chamada] = []
    for casamento in padrao.finditer(neutro):
        inicio = casamento.end()
        profundidade = 1
        corte = inicio
        partes: list[str] = []
        indice = inicio
        while indice < len(neutro):
            caractere = neutro[indice]
            if caractere in "([{":
                profundidade += 1
            elif caractere in ")]}":
                profundidade -= 1
                if profundidade == 0:
                    partes.append(neutro[corte:indice])
                    break
            elif caractere == "," and profundidade == 1:
                partes.append(neutro[corte:indice])
                corte = indice + 1
            indice += 1
        if profundidade == 0:
            achados.append(
                Chamada(
                    inicio=casamento.start(),
                    fim=indice,
                    argumentos=tuple(parte.strip() for parte in partes),
                )
            )
    return achados


def percorrer(blocos: tuple[Bloco, ...]) -> list[tuple[Bloco, tuple[Bloco, ...]]]:
    """Cada bloco com a cadeia de ancestrais que o contém, do mais externo ao pai."""
    achatado: list[tuple[Bloco, tuple[Bloco, ...]]] = []

    def descer(atuais: tuple[Bloco, ...], ancestrais: tuple[Bloco, ...]) -> None:
        for bloco in atuais:
            achatado.append((bloco, ancestrais))
            descer(bloco.filhos, (*ancestrais, bloco))

    descer(blocos, ())
    return achatado
