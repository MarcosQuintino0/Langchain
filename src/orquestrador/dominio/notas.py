"""Notas de descoberta — o que a exploração estabeleceu, antes de virar artefato.

O mapeador explora num passo e serializa noutro; as notas são o que atravessa a
fronteira. São texto livre POR DESENHO — prender a exploração num JSON foi o que
concentrava 60% da saída do estágio em raciocínio de serialização —, mas duas
seções têm forma combinada, porque código monta o inventário a partir delas:

    ## Endpoints do recurso
    - GET /clientes | handler listar | src/.../ClienteController.java:34

    ## Rotas dinâmicas não resolvidas
    - registrarRotas(prefixo) | src/rotas.js:12 | prefixo calculado em runtime

O formato da linha é o MESMO que a semente estática injeta na entrada: confirmar
um endpoint é copiar a linha, e o parser daqui é o inverso do render de lá.

Este módulo não decide nada: parseia e confere. Quem julga o que entra nas notas
é o modelo; quem reprova é o Gate A, depois. A regra de fronteira de `dominio/`
vale inteira — nada aqui abre arquivo nem conhece estágio.
"""

from __future__ import annotations

import re

from orquestrador.dominio.endpoint import normalizar_endpoint
from orquestrador.dominio.inventario import Endpoint, RotaDinamica

__all__ = [
    "SECAO_ENDPOINTS",
    "SECAO_ROTAS_DINAMICAS",
    "endpoints_das_notas",
    "endpoints_nao_citados",
    "rotas_dinamicas_das_notas",
]

SECAO_ENDPOINTS = "## Endpoints do recurso"
SECAO_ROTAS_DINAMICAS = "## Rotas dinâmicas não resolvidas"

# `- MÉTODO /rota | handler nome | arquivo[:linha]` — o formato da semente. A
# palavra `handler` é opcional no parse: medido na primeira execução real, o
# modelo copiou as linhas da semente normalizando a palavra para fora
# (`| CustomerController.create |`), e a exigência estrita mandava o inventário
# inteiro para o fallback de modelo — pagando uma chamada para reobter o que o
# parse já tinha nas mãos. Aceitar liberal e emitir conservador.
_LINHA_DE_ENDPOINT = re.compile(
    r"^\s*[-*]\s*`?(?P<metodo>[A-Z]+)\s+(?P<rota>/\S*?)`?\s*\|\s*(?:handler\s+)?"
    r"`?(?P<handler>[^|`\s]+)`?\s*\|\s*(?P<evidencia>\S.*?)\s*$"
)

# `- expressão | arquivo[:linha] | motivo`
_LINHA_DE_ROTA_DINAMICA = re.compile(
    r"^\s*[-*]\s*(?P<expressao>[^|]+?)\s*\|\s*(?P<evidencia>[^|]+?)\s*\|\s*(?P<motivo>\S.*?)\s*$"
)


def _secao(notas: str, titulo: str) -> list[str]:
    """As linhas de uma seção `##`, do título até o próximo `##` ou o fim."""
    linhas = notas.splitlines()
    try:
        inicio = next(i for i, linha in enumerate(linhas) if linha.strip() == titulo)
    except StopIteration:
        return []
    corpo: list[str] = []
    for linha in linhas[inicio + 1 :]:
        if linha.lstrip().startswith("## "):
            break
        corpo.append(linha)
    return corpo


def _evidencia(texto: str) -> tuple[str, int | None]:
    """Separa `arquivo:linha` — a linha é opcional, o arquivo pode conter `:`."""
    caminho, separador, resto = texto.rpartition(":")
    if separador and resto.isdigit():
        return caminho.strip(), int(resto)
    return texto.strip(), None


def endpoints_das_notas(notas: str) -> list[Endpoint]:
    """Os endpoints declarados na seção combinada, como objetos do domínio.

    Linha fora do formato é ignorada em vez de virar erro: a seção convive com
    prosa ("nenhum além da semente"), e o chamador decide o que fazer com uma
    lista vazia — é ele que sabe se existe fallback.
    """
    encontrados: list[Endpoint] = []
    vistos: set[str] = set()
    for linha in _secao(notas, SECAO_ENDPOINTS):
        achado = _LINHA_DE_ENDPOINT.match(linha)
        if not achado:
            continue
        arquivo, numero = _evidencia(achado.group("evidencia"))
        endpoint = Endpoint(
            metodo=achado.group("metodo"),
            rota=achado.group("rota"),
            handler=achado.group("handler"),
            arquivo=arquivo,
            linha=numero,
        )
        # Duplicata silenciosamente descartada, e não erro: o Inventario montado a
        # partir daqui recusa duplicata com mensagem própria, e duas fontes de
        # recusa para o mesmo defeito produzem mensagens divergentes.
        if endpoint.canonico not in vistos:
            vistos.add(endpoint.canonico)
            encontrados.append(endpoint)
    return encontrados


def rotas_dinamicas_das_notas(notas: str) -> list[RotaDinamica]:
    nao_resolvidas: list[RotaDinamica] = []
    for linha in _secao(notas, SECAO_ROTAS_DINAMICAS):
        texto = linha.strip()
        if not texto or texto.lstrip("-* ").lower().startswith(("nenhuma", "nenhum")):
            continue
        achado = _LINHA_DE_ROTA_DINAMICA.match(linha)
        if not achado:
            continue
        arquivo, numero = _evidencia(achado.group("evidencia"))
        nao_resolvidas.append(
            RotaDinamica(
                expressao=achado.group("expressao").strip(),
                arquivo=arquivo,
                linha=numero,
                motivo=achado.group("motivo").strip(),
            )
        )
    return nao_resolvidas


def endpoints_nao_citados(notas: str, canonicos: list[str]) -> list[str]:
    """Quais dos endpoints dados as notas nem mencionam.

    É a guarda entre a exploração e a serialização: a semente estática é o
    universo mínimo conhecido ANTES do modelo trabalhar, e nota que não cita um
    endpoint da semente perdeu informação que já era certa. A checagem é por
    forma canônica em texto normalizado — presença, não posição: o endpoint pode
    aparecer na seção própria, numa regra ou numa incerteza, e qualquer menção
    prova que ele não foi esquecido.
    """
    normalizadas = " ".join(notas.split())
    ausentes: list[str] = []
    for canonico in canonicos:
        alvo = normalizar_endpoint(canonico)
        if alvo not in normalizadas:
            ausentes.append(alvo)
    return ausentes
