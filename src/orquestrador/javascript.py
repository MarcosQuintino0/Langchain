"""Leitura dos `export` de um módulo JavaScript — parser puro.

Sem dependência nenhuma do projeto: entra texto, sai uma lista de `ExportJs`. Mora
fora de `ferramentas/` justamente por isso — `ferramentas/` fala com o mundo
(subprocess, disco, `Config`), e este módulo não fala com ninguém. Quem faz o I/O é
`ferramentas/superficie.py`, que traduz o resultado daqui para os contratos.

Copiar, nunca reconstruir
-------------------------
Uma assinatura remontada por regex é um palpite que pode divergir do real, e quem
lê vai confiar nela. O que sai daqui é o texto do arquivo.

O recorte da declaração
-----------------------
A varredura começa na linha do `export` e para em uma de três coisas:

* **`{` que abre corpo de função ou de classe** — a declaração acaba antes dele, e
  sai só a assinatura;
* **`;` no nível zero** — fim do statement;
* **quebra de linha no nível zero** sem operador pendurado (ASI).

O discriminador do `{` é o ponto delicado. Um `{` só abre corpo quando vem **depois
de `)`, depois de `=>` ou depois do nome de uma classe**. Em qualquer outra posição
— depois de `=`, depois de `export`, dentro de um literal — ele é **valor** ou
**lista de nomes**, e precisa ser consumido balanceado até o fim do statement.

Tratar todo `{` de nível zero como corpo (o que este módulo fazia antes) acerta a
função e erra o resto: `export const Rotas = { … }` virava `export const Rotas =`,
que **parece informação** — quem lê inventa as chaves achando que está seguindo a
superfície. É pior do que o export ausente, porque não há sinal de que falta algo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Teto por declaração: um objeto de rotas grande é útil, um arquivo inteiro não.
MAX_DECLARACAO = 2000
# Quantas linhas de continuação buscar antes de desistir de fechar o balanceamento.
MAX_LINHAS_DA_DECLARACAO = 60
MARCA_DE_TRUNCAMENTO = "\n… (truncado)"

_EXPORT = re.compile(r"^export\b(?P<resto>.*)$")
_PALAVRA_EXPORT = re.compile(r"^export\b")
_NOME_APOS_PALAVRA = re.compile(
    r"^(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)"
)
_LISTA_DE_NOMES = re.compile(r"^\{([^}]*)\}")
_ESTRELA = re.compile(r"^\*(?:\s+as\s+([A-Za-z_$][\w$]*))?")
_IDENTIFICADOR = re.compile(r"[A-Za-z_$][\w$]*")
# Fim de linha que continua na próxima: operador pendurado, sem ponto e vírgula.
_CONTINUA = re.compile(r"(=>|[=,(\[+\-*/.:?]|\|\||&&|\?\?)$")
# `export class X {` e `export default class X {`: aí o primeiro `{` é o corpo.
_CLASSE = re.compile(r"^\s*(?:default\s+)?(?:abstract\s+)?class\b")


@dataclass(frozen=True)
class ExportJs:
    """Um export, com a declaração verbatim e o comentário imediatamente acima."""

    nome: str
    declaracao: str
    comentario: str | None = None


def extrair_exports(fonte: str) -> list[ExportJs]:
    """Todos os `export` de um módulo, na ordem em que aparecem."""
    linhas = fonte.splitlines()
    achados: list[ExportJs] = []
    indice = 0

    while indice < len(linhas):
        casamento = _EXPORT.match(linhas[indice])
        if not casamento:
            indice += 1
            continue

        declaracao, consumidas = recortar_declaracao(linhas, indice)
        comentario = comentario_acima(linhas, indice)
        # Declaração vazia não descreve nada e enganaria quem for copiar.
        if declaracao.strip():
            for nome in nomes_exportados(declaracao):
                achados.append(ExportJs(nome, declaracao, comentario))
        indice += max(1, consumidas)

    return achados


# ---------------------------------------------------------------------------
# Recorte
# ---------------------------------------------------------------------------


def recortar_declaracao(linhas: list[str], inicio: int) -> tuple[str, int]:
    """Declaração a partir da linha do `export`, e quantas linhas ela consumiu."""
    janela = linhas[inicio : min(len(linhas), inicio + MAX_LINHAS_DA_DECLARACAO)]
    texto = "\n".join(janela)
    primeira = _EXPORT.match(janela[0]) if janela else None
    eh_classe = bool(primeira and _CLASSE.match(primeira.group("resto")))

    parenteses = colchetes = chaves = 0
    aspa: str | None = None
    indice = 0
    # Último caractere que conta para decidir o que um `{` está abrindo. Espaço,
    # comentário e quebra de linha não contam.
    ultimo_significativo = -1
    corte = len(texto)

    while indice < len(texto):
        caractere = texto[indice]

        if aspa:
            if caractere == "\\":
                indice += 2
                continue
            if caractere == aspa:
                aspa = None
                ultimo_significativo = indice
            indice += 1
            continue

        if texto.startswith("//", indice):
            fim = texto.find("\n", indice)
            if fim < 0:
                break
            indice = fim
            continue

        if texto.startswith("/*", indice):
            fim = texto.find("*/", indice)
            if fim < 0:
                break
            indice = fim + 2
            continue

        if caractere in "\"'`":
            aspa = caractere
        elif caractere == "(":
            parenteses += 1
        elif caractere == ")":
            parenteses -= 1
        elif caractere == "[":
            colchetes += 1
        elif caractere == "]":
            colchetes -= 1
        elif caractere == "{":
            if parenteses <= 0 and colchetes <= 0 and chaves <= 0:
                if eh_classe or _abre_corpo(texto, ultimo_significativo):
                    corte = indice
                    break
                # Não é corpo: é objeto literal ou lista de nomes. Consome.
            chaves += 1
        elif caractere == "}":
            chaves -= 1
        elif caractere == ";" and parenteses <= 0 and colchetes <= 0 and chaves <= 0:
            corte = indice
            break
        elif caractere == "\n" and parenteses <= 0 and colchetes <= 0 and chaves <= 0:
            anterior = texto[: ultimo_significativo + 1]
            if anterior.strip() and not _CONTINUA.search(anterior):
                corte = indice
                break

        if not caractere.isspace():
            ultimo_significativo = indice
        indice += 1

    recorte = texto[:corte].rstrip()
    if len(recorte) > MAX_DECLARACAO:
        recorte = recorte[:MAX_DECLARACAO].rstrip() + MARCA_DE_TRUNCAMENTO
    return recorte, recorte.count("\n") + 1


def _abre_corpo(texto: str, ultimo: int) -> bool:
    """O `{` desta posição abre um corpo de função?

    Só depois de `)` — fim da lista de parâmetros — ou de `=>`. Depois de `=`, de
    `export` ou de vírgula, ele abre um valor.
    """
    if ultimo < 0:
        return False
    if texto[ultimo] == ")":
        return True
    return texto[ultimo] == ">" and ultimo >= 1 and texto[ultimo - 1] == "="


# ---------------------------------------------------------------------------
# Nomes
# ---------------------------------------------------------------------------


def nomes_exportados(declaracao: str) -> list[str]:
    """Nome(s) que a declaração exporta.

    Lida com a declaração inteira, não com a primeira linha: é o que faz uma lista
    de nomes quebrada em várias linhas continuar sendo lida.
    """
    # `_EXPORT` é para uma LINHA: com `.*$` ele não atravessa quebra de linha, e a
    # declaração aqui costuma ter várias (objeto literal, lista de nomes).
    texto = declaracao.strip()
    inicio = _PALAVRA_EXPORT.match(texto)
    if not inicio:
        return []
    limpo = texto[inicio.end() :].strip()

    # `export * from "./x.js"` e `export * as Client from "./x.js"`.
    estrela = _ESTRELA.match(limpo)
    if estrela:
        return [estrela.group(1) or "*"]

    if limpo.startswith("default"):
        return ["default"]

    lista = _LISTA_DE_NOMES.match(limpo)
    if lista:
        nomes: list[str] = []
        for item in lista.group(1).split(","):
            pedaco = item.strip()
            if not pedaco:
                continue
            # `nome as alias` exporta o alias.
            alvo = pedaco.split(" as ")[-1].strip()
            if _IDENTIFICADOR.fullmatch(alvo):
                nomes.append(alvo)
        return nomes

    declarado = _NOME_APOS_PALAVRA.match(limpo)
    return [declarado.group(1)] if declarado else []


def comentario_acima(linhas: list[str], indice: int) -> str | None:
    """Bloco de comentário contíguo imediatamente acima da declaração."""
    coletadas: list[str] = []
    numero = indice - 1

    # Bloco /** ... */
    if numero >= 0 and linhas[numero].strip().endswith("*/"):
        while numero >= 0:
            coletadas.insert(0, linhas[numero])
            if linhas[numero].lstrip().startswith(("/*", "/**")):
                break
            numero -= 1
        return "\n".join(coletadas).strip() or None

    while numero >= 0 and linhas[numero].lstrip().startswith("//"):
        coletadas.insert(0, linhas[numero].strip())
        numero -= 1
    return "\n".join(coletadas) or None


# ---------------------------------------------------------------------------
# Tags de cobertura nos specs
# ---------------------------------------------------------------------------

# `@endpoint MÉTODO /rota  @cat CAT-07` — a marcação que a skill exige em cada `it`.
# A rota vai até dois espaços ou o fim da linha, porque `@cat` costuma vir alinhado
# depois dela.
_TAG = re.compile(
    r"@endpoint\s+(?P<endpoint>[A-Z]+\s+\S+?)\s{1,}@cat\s+(?P<cat>CAT-\d{2}|\S+)"
)


@dataclass(frozen=True)
class TagsDoSpec:
    """O que as tags de um spec dizem, e o quanto disso é confiável.

    `dinamicas` conta as tags cujo valor é template (`@cat ${...}`), usadas na forma
    data-driven que a skill permite. Elas existem, mas só resolvem em tempo de
    execução — este parser não as resolve, e quem usa `pares` precisa saber que a
    lista está incompleta quando `dinamicas` não é zero.
    """

    pares: set[tuple[str, str]]
    dinamicas: int


def extrair_tags(fonte: str) -> TagsDoSpec:
    """Pares `(endpoint, categoria)` marcados no spec.

    Parser deliberadamente literal: quem tem autoridade sobre a contagem de cobertura
    é o `qa-cobertura.mjs` da skill. O que sai daqui serve para **nomear** o que
    faltou num delta, nunca para decidir se faltou.
    """
    pares: set[tuple[str, str]] = set()
    dinamicas = 0
    for casamento in _TAG.finditer(fonte):
        endpoint = " ".join(casamento.group("endpoint").split())
        cat = casamento.group("cat")
        if "${" in endpoint or "${" in cat:
            dinamicas += 1
            continue
        pares.add((endpoint, cat))
    return TagsDoSpec(pares=pares, dinamicas=dinamicas)
