"""Rotas HTTP que uma classe Java declara por anotação do Spring MVC.

Entra o texto de um `.java`, sai o que a classe declara: verbo, rota completa,
handler e linha. Nada de disco, nada de subprocesso, nada de `javac` — pelo mesmo
motivo de `exports_javascript`, o par de referência deste módulo: parser puro é
testável com uma string e não precisa do projeto inteiro montado para provar um
caso.

Por que não sai do `graph.json`
-------------------------------
Porque a informação não está lá. O extrator AST do Graphify emite, para Java, nós
de arquivo, de classe e de método com `source_file` e `source_location` — e **nada
sobre HTTP**. Anotação não vira nó, atributo de anotação não vira propriedade. O
grafo diz que `ProductController.create()` existe na linha 32; que ela responde
`POST /api/v1/products` só está no texto do fonte. Daí a divisão: o grafo é o
denominador de arquivos, este módulo lê a rota.

O que este parser recusa a adivinhar
------------------------------------
Rota montada por constante, concatenação ou `${propriedade}` **não** é ignorada e
**não** é chutada: vira `RotaNaoResolvida`, com a expressão original e o motivo.
Chutar produziria um endpoint que o diff cobrará do manifesto sem existir; ignorar
produziria silêncio, que é o defeito que o diff existe para eliminar. Registrar a
incerteza é a única saída que não mente.

Igual valem `@RequestMapping` de método sem `method=` (casa com todos os verbos, e
não há um endpoint só a nomear) e prefixo de classe irresolúvel, que contamina
todas as rotas da classe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "ANOTACOES_DE_CONTROLADOR",
    "ANOTACOES_DE_VERBO",
    "ControladorSpring",
    "RotaNaoResolvida",
    "RotaSpring",
    "extrair_controladores",
    "juntar_rota",
]

# Anotação de verbo fixo -> método HTTP. `@RequestMapping` fica de fora porque o
# verbo dele vem do atributo `method`, que pode faltar.
ANOTACOES_DE_VERBO: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}

# `@Controller` entra junto com `@RestController`: um controlador MVC clássico com
# `@ResponseBody` no método serve API igual, e deixá-lo de fora faria o diff
# aprovar um manifesto que ignora metade do backend.
ANOTACOES_DE_CONTROLADOR: frozenset[str] = frozenset({"RestController", "Controller"})

_REQUEST_MAPPING = "RequestMapping"

_PALAVRAS_DE_TIPO = frozenset({"class", "interface", "enum", "record"})

_DECLARACAO_DE_TIPO = re.compile(r"\b(class|interface|enum|record)\s+(\w+)")
_ANOTACAO = re.compile(r"@\s*(\w+)")
_IDENTIFICADOR_FINAL = re.compile(r"(\w+)\s*$")
_LITERAL = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
_REQUEST_METHOD = re.compile(r"\bRequestMethod\s*\.\s*(\w+)")
# `/{id:[0-9]+}` — o Spring aceita restrição de regex no próprio segmento, e ela
# não faz parte da rota que um teste chama.
_RESTRICAO_DE_VARIAVEL = re.compile(r"\{\s*(\w+)\s*:[^{}]*\}")


@dataclass(frozen=True)
class RotaNaoResolvida:
    """Declaração de rota que o parser leu mas não conseguiu reduzir a texto."""

    expressao: str
    linha: int
    motivo: str


@dataclass(frozen=True)
class RotaSpring:
    """Um endpoint resolvido: verbo, rota completa e onde ele foi declarado."""

    metodo: str
    rota: str
    handler: str
    linha: int


@dataclass(frozen=True)
class ControladorSpring:
    """Uma classe anotada como controlador e o que ela declara."""

    classe: str
    linha: int
    prefixos: tuple[str, ...] = ()
    rotas: tuple[RotaSpring, ...] = ()
    nao_resolvidas: tuple[RotaNaoResolvida, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Máscara: comentário e conteúdo de literal viram espaço
# ---------------------------------------------------------------------------


def mascarar(texto: str) -> str:
    """Mesmo texto, com comentário e miolo de literal trocados por espaço.

    A varredura estrutural (anotação, chave, parêntese, declaração de tipo) roda
    sobre a máscara; a extração de literal roda sobre o original, nos mesmos
    índices. Sem isso, um `@` dentro de string vira anotação e uma chave dentro de
    comentário desalinha todo o casamento de blocos — e o parser passa a errar
    exatamente nos arquivos que têm comentário explicando a rota.

    O comprimento é preservado caractere a caractere, e `\\n` nunca é apagado: é
    dele que sai o número de linha de cada achado.
    """
    saida: list[str] = []
    indice = 0
    total = len(texto)
    while indice < total:
        atual = texto[indice]
        proximo = texto[indice + 1] if indice + 1 < total else ""

        if atual == "/" and proximo == "/":
            while indice < total and texto[indice] != "\n":
                saida.append(" ")
                indice += 1
            continue
        if atual == "/" and proximo == "*":
            saida.append("  ")
            indice += 2
            while indice < total and not (
                texto[indice] == "*" and texto[indice : indice + 2] == "*/"
            ):
                saida.append("\n" if texto[indice] == "\n" else " ")
                indice += 1
            saida.append("  ")
            indice += 2
            continue
        if atual in {'"', "'"}:
            saida.append(atual)
            indice += 1
            while indice < total and texto[indice] != atual:
                if texto[indice] == "\\" and indice + 1 < total:
                    saida.append("  ")
                    indice += 2
                    continue
                saida.append("\n" if texto[indice] == "\n" else " ")
                indice += 1
            if indice < total:
                saida.append(atual)
                indice += 1
            continue

        saida.append(atual)
        indice += 1
    return "".join(saida)


def _linha_de(texto: str, posicao: int) -> int:
    return texto.count("\n", 0, posicao) + 1


def _fechar(mascara: str, inicio: int, abre: str, fecha: str) -> int:
    """Índice logo depois do delimitador que fecha o aberto em `inicio`.

    Devolve `len(mascara)` quando o arquivo termina sem fechar — fonte truncado é
    entrada possível, e estourar `IndexError` aqui esconderia a causa.
    """
    profundidade = 0
    for indice in range(inicio, len(mascara)):
        if mascara[indice] == abre:
            profundidade += 1
        elif mascara[indice] == fecha:
            profundidade -= 1
            if profundidade == 0:
                return indice + 1
    return len(mascara)


# ---------------------------------------------------------------------------
# Anotações e o que elas anotam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Anotacao:
    nome: str
    argumentos: str | None
    inicio: int
    fim: int
    linha: int


def _anotacoes(texto: str, mascara: str) -> list[_Anotacao]:
    achadas: list[_Anotacao] = []
    for casamento in _ANOTACAO.finditer(mascara):
        fim = casamento.end()
        # `@interface Foo` declara uma anotação; não anota nada.
        if casamento.group(1) == "interface":
            continue
        resto = mascara[fim:]
        deslocamento = len(resto) - len(resto.lstrip(" \t\n\r"))
        argumentos: str | None = None
        if fim + deslocamento < len(mascara) and mascara[fim + deslocamento] == "(":
            fecha = _fechar(mascara, fim + deslocamento, "(", ")")
            argumentos = texto[fim + deslocamento + 1 : max(fecha - 1, fim + deslocamento + 1)]
            fim = fecha
        achadas.append(
            _Anotacao(
                nome=casamento.group(1),
                argumentos=argumentos,
                inicio=casamento.start(),
                fim=fim,
                linha=_linha_de(texto, casamento.start()),
            )
        )
    return achadas


@dataclass(frozen=True)
class _Alvo:
    """O que vem depois de um bloco de anotações: um tipo, um método, ou nada."""

    especie: str  # "tipo", "metodo" ou "outro"
    nome: str


def _alvo_apos(mascara: str, posicao: int) -> _Alvo:
    """Salta as demais anotações do bloco e classifica a declaração que sobra."""
    indice = posicao
    total = len(mascara)
    while indice < total:
        while indice < total and mascara[indice] in " \t\n\r":
            indice += 1
        if indice < total and mascara[indice] == "@":
            casamento = _ANOTACAO.match(mascara, indice)
            if casamento is None:
                break
            indice = casamento.end()
            resto = mascara[indice:]
            deslocamento = len(resto) - len(resto.lstrip(" \t\n\r"))
            if indice + deslocamento < total and mascara[indice + deslocamento] == "(":
                indice = _fechar(mascara, indice + deslocamento, "(", ")")
            continue
        break

    fim = indice
    while fim < total and mascara[fim] not in "({;=":
        fim += 1
    cabeca = mascara[indice:fim]

    declaracao = _DECLARACAO_DE_TIPO.search(cabeca)
    if declaracao is not None:
        return _Alvo("tipo", declaracao.group(2))
    if fim < total and mascara[fim] == "(":
        identificador = _IDENTIFICADOR_FINAL.search(cabeca)
        if identificador is not None and identificador.group(1) not in _PALAVRAS_DE_TIPO:
            return _Alvo("metodo", identificador.group(1))
    return _Alvo("outro", "")


@dataclass(frozen=True)
class _Tipo:
    nome: str
    inicio: int
    corpo_inicio: int
    corpo_fim: int
    linha: int


def _tipos(texto: str, mascara: str) -> list[_Tipo]:
    encontrados: list[_Tipo] = []
    for casamento in _DECLARACAO_DE_TIPO.finditer(mascara):
        # `@interface` é declaração de anotação, não de tipo com corpo de método.
        anterior = mascara[: casamento.start()].rstrip()
        if anterior.endswith("@"):
            continue
        chave = mascara.find("{", casamento.end())
        if chave == -1:
            continue
        encontrados.append(
            _Tipo(
                nome=casamento.group(2),
                inicio=casamento.start(),
                corpo_inicio=chave,
                corpo_fim=_fechar(mascara, chave, "{", "}"),
                linha=_linha_de(texto, casamento.start()),
            )
        )
    return encontrados


def _tipo_que_contem(tipos: list[_Tipo], posicao: int) -> _Tipo | None:
    """O tipo mais interno cujo corpo contém `posicao`."""
    candidatos = [tipo for tipo in tipos if tipo.corpo_inicio < posicao < tipo.corpo_fim]
    return max(candidatos, key=lambda tipo: tipo.corpo_inicio) if candidatos else None


# ---------------------------------------------------------------------------
# Argumentos de anotação
# ---------------------------------------------------------------------------


def _partir_no_topo(argumentos: str) -> list[str]:
    """Separa a lista de argumentos nas vírgulas de profundidade zero."""
    mascara = mascarar(argumentos)
    partes: list[str] = []
    profundidade = 0
    inicio = 0
    for indice, caractere in enumerate(mascara):
        if caractere in "({[":
            profundidade += 1
        elif caractere in ")}]":
            profundidade -= 1
        elif caractere == "," and profundidade == 0:
            partes.append(argumentos[inicio:indice])
            inicio = indice + 1
    partes.append(argumentos[inicio:])
    return [parte.strip() for parte in partes if parte.strip()]


def _expressao_de_rota(argumentos: str | None) -> str | None:
    """A expressão de `value=`/`path=` (ou o único argumento posicional).

    `None` quando a anotação não fala de caminho — `@PostMapping(consumes = ...)`
    herda a rota da classe, e isso é resolução, não incerteza.
    """
    if argumentos is None or not argumentos.strip():
        return None
    for parte in _partir_no_topo(argumentos):
        mascara = mascarar(parte)
        igual = mascara.find("=")
        if igual == -1:
            return parte.strip()
        chave = parte[:igual].strip()
        if chave in {"value", "path"}:
            return parte[igual + 1 :].strip()
    return None


def _literais(expressao: str) -> list[str] | None:
    """Os textos de `"a"` ou de `{"a", "b"}`; `None` se houver algo a avaliar."""
    alvo = expressao.strip()
    if alvo.startswith("{") and alvo.endswith("}"):
        alvo_interno = alvo[1:-1]
        itens = _partir_no_topo(alvo_interno) if alvo_interno.strip() else []
    else:
        itens = [alvo]

    textos: list[str] = []
    for item in itens:
        casamento = _LITERAL.match(item.strip())
        if casamento is None:
            return None
        textos.append(casamento.group(1))
    return textos


def juntar_rota(prefixo: str, sufixo: str) -> str:
    """Prefixo da classe + caminho do método, na forma que o manifesto usa.

    Normaliza barra dupla, barra final e restrição de regex (`{id:[0-9]+}` vira
    `{id}`): as três variam entre quem escreve o controlador e nenhuma delas muda
    qual requisição o teste faz. Comparar sem normalizar transformaria estilo de
    código em violação de cobertura.
    """
    bruto = f"/{prefixo.strip()}/{sufixo.strip()}"
    bruto = _RESTRICAO_DE_VARIAVEL.sub(r"{\1}", bruto)
    partes = [parte for parte in bruto.split("/") if parte]
    return "/" + "/".join(partes) if partes else "/"


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------


def extrair_controladores(texto: str) -> list[ControladorSpring]:
    """Controladores Spring declarados neste fonte, com rotas e incertezas."""
    mascara = mascarar(texto)
    anotacoes = _anotacoes(texto, mascara)
    tipos = _tipos(texto, mascara)
    if not anotacoes or not tipos:
        return []

    por_nome = {tipo.nome: tipo for tipo in tipos}
    controladores: dict[str, _Tipo] = {}
    prefixos: dict[str, tuple[str, ...]] = {}
    incertezas: dict[str, list[RotaNaoResolvida]] = {}
    rotas: dict[str, list[RotaSpring]] = {}

    for anotacao in anotacoes:
        if anotacao.nome not in ANOTACOES_DE_CONTROLADOR:
            continue
        alvo = _alvo_apos(mascara, anotacao.fim)
        tipo = por_nome.get(alvo.nome) if alvo.especie == "tipo" else None
        if tipo is not None:
            controladores[tipo.nome] = tipo
            prefixos.setdefault(tipo.nome, ("",))
            incertezas.setdefault(tipo.nome, [])
            rotas.setdefault(tipo.nome, [])

    if not controladores:
        return []

    for anotacao in anotacoes:
        if anotacao.nome != _REQUEST_MAPPING:
            continue
        alvo = _alvo_apos(mascara, anotacao.fim)
        if alvo.especie != "tipo" or alvo.nome not in controladores:
            continue
        expressao = _expressao_de_rota(anotacao.argumentos)
        if expressao is None:
            continue
        textos = _literais(expressao)
        if textos is None or any("${" in texto_ for texto_ in textos):
            prefixos[alvo.nome] = ()
            incertezas[alvo.nome].append(
                RotaNaoResolvida(
                    expressao=expressao,
                    linha=anotacao.linha,
                    motivo=(
                        f"prefixo de @RequestMapping da classe {alvo.nome} não é literal "
                        "de texto; nenhuma rota desta classe pôde ser montada"
                    ),
                )
            )
            continue
        prefixos[alvo.nome] = tuple(textos) or ("",)

    for anotacao in anotacoes:
        verbo = ANOTACOES_DE_VERBO.get(anotacao.nome)
        if verbo is None and anotacao.nome != _REQUEST_MAPPING:
            continue
        alvo = _alvo_apos(mascara, anotacao.fim)
        if alvo.especie != "metodo":
            continue
        dono = _tipo_que_contem(tipos, anotacao.inicio)
        if dono is None or dono.nome not in controladores:
            continue

        verbos = (verbo,) if verbo is not None else _verbos_de_request_mapping(anotacao)
        if verbos is None:
            incertezas[dono.nome].append(
                RotaNaoResolvida(
                    expressao=f"@RequestMapping({(anotacao.argumentos or '').strip()})",
                    linha=anotacao.linha,
                    motivo=(
                        f"{dono.nome}.{alvo.nome} usa @RequestMapping sem `method=`: "
                        "casa com todos os verbos, e não há um endpoint único a nomear"
                    ),
                )
            )
            continue

        sufixos = _sufixos(anotacao, dono.nome, alvo.nome, incertezas[dono.nome])
        if sufixos is None:
            continue
        base = prefixos[dono.nome]
        if not base:
            # Prefixo da classe irresolúvel: a rota do método existe, mas a rota
            # completa não. Já há uma incerteza registrada no nível da classe.
            continue
        for metodo_http in verbos:
            for prefixo in base:
                for sufixo in sufixos:
                    rotas[dono.nome].append(
                        RotaSpring(
                            metodo=metodo_http,
                            rota=juntar_rota(prefixo, sufixo),
                            handler=f"{dono.nome}.{alvo.nome}",
                            linha=anotacao.linha,
                        )
                    )

    return [
        ControladorSpring(
            classe=tipo.nome,
            linha=tipo.linha,
            prefixos=prefixos[nome],
            rotas=tuple(rotas[nome]),
            nao_resolvidas=tuple(incertezas[nome]),
        )
        for nome, tipo in sorted(controladores.items())
    ]


def _verbos_de_request_mapping(anotacao: _Anotacao) -> tuple[str, ...] | None:
    verbos = tuple(_REQUEST_METHOD.findall(anotacao.argumentos or ""))
    return verbos or None


def _sufixos(
    anotacao: _Anotacao,
    classe: str,
    metodo: str,
    incertezas: list[RotaNaoResolvida],
) -> tuple[str, ...] | None:
    expressao = _expressao_de_rota(anotacao.argumentos)
    if expressao is None:
        return ("",)
    textos = _literais(expressao)
    if textos is None or any("${" in texto for texto in textos):
        incertezas.append(
            RotaNaoResolvida(
                expressao=expressao,
                linha=anotacao.linha,
                motivo=(
                    f"caminho de {classe}.{metodo} não é literal de texto "
                    "(constante, concatenação ou propriedade): a rota não pôde ser montada"
                ),
            )
        )
        return None
    return tuple(textos) or ("",)
