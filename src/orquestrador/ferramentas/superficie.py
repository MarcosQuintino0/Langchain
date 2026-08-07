"""Extração determinística da superfície de módulos compartilhados.

O executor escreve `_support/api.js` e os specs, e para isso precisa importar o
client, as rotas e os asserts base do projeto. Ele tem que acertar três coisas que
ninguém conta a ele: **o nome do export, a forma dos argumentos e a profundidade do
caminho relativo**. O Gate B verifica exatamente isso, e o delta que ele devolve
("import não resolve") não é acionável — o loop de reparo não converge sobre
informação que o executor nunca teve.

Este módulo custa zero token e roda uma vez por execução: a superfície é do
**projeto**, não do recurso.

Aqui mora só o I/O: achar os arquivos, calcular os caminhos de import e montar o
artefato. A leitura do JavaScript é de `orquestrador.javascript`, que é puro e
testável sem disco.
"""

from __future__ import annotations

import os
from pathlib import Path

from orquestrador.config import Config
from orquestrador.contratos import (
    ExportCompartilhado,
    ModuloCompartilhado,
    SuperficieDoProjeto,
)
from orquestrador.excecoes import ProjetoNaoPreparado
from orquestrador.ferramentas.arquivos import relativo_a
from orquestrador.javascript import ExportJs, extrair_exports

__all__ = ["EXTENSOES", "caminho_de_import", "extrair", "extrair_exports"]

EXTENSOES = (".js", ".mjs", ".cjs")

# Nomes de âncora para calcular profundidade de caminho. Não precisam existir: o que
# importa é quantos níveis o layout tem, não como as pastas se chamam.
_RECURSO = "_recurso_"
_SUBDOMINIO = "_subdominio_"


def extrair(config: Config) -> SuperficieDoProjeto:
    """Lê os módulos compartilhados do projeto e devolve a superfície.

    Levanta `ProjetoNaoPreparado` quando o diretório não existe ou não há export
    algum — preparar o projeto é outro fluxo da skill, e adivinhar aqui produziria
    imports inexistentes.
    """
    raiz = config.caminhos.support_abs
    relativo = config.caminhos.support_compartilhado

    if not raiz.is_dir():
        raise ProjetoNaoPreparado(
            f"módulos compartilhados não encontrados em {raiz}.\n"
            "O orquestrador gera testes num projeto de testes JÁ PREPARADO: ele precisa "
            "do client HTTP, das rotas e dos asserts base para escrever os imports do "
            "recurso.\n"
            "O que fazer: prepare o projeto seguindo references/preparar-projeto.md da "
            "skill qa-api (ou adapte a arquitetura-base de assets/cypress-api-base/), "
            f"ou ajuste [caminhos].support_compartilhado (hoje {relativo!r}) para o "
            "diretório que o projeto realmente usa."
        )

    # Âncoras de profundidade. Todos os recursos ficam em <dir_recursos>/<nome>, e um
    # recurso grande e composto ganha ainda uma subpasta de sub-domínio — o que muda
    # o caminho do spec, mas não o do `_support/`, que não desce junto.
    dir_recurso = config.caminhos.dir_recursos_abs / _RECURSO
    modulos: list[ModuloCompartilhado] = []

    for arquivo in sorted(raiz.rglob("*")):
        if not arquivo.is_file() or arquivo.suffix.lower() not in EXTENSOES:
            continue
        exports = extrair_exports(arquivo.read_text(encoding="utf-8", errors="replace"))
        if not exports:
            continue
        modulos.append(
            ModuloCompartilhado(
                caminho=relativo_a(arquivo, config.caminhos.projeto_testes),
                import_do_recurso=caminho_de_import(arquivo, dir_recurso),
                import_do_subdominio=caminho_de_import(
                    arquivo, dir_recurso / _SUBDOMINIO
                ),
                import_do_support=caminho_de_import(arquivo, dir_recurso / "_support"),
                exports=[_para_contrato(exportado) for exportado in exports],
            )
        )

    if not modulos:
        raise ProjetoNaoPreparado(
            f"nenhum export encontrado em {raiz}.\n"
            "O diretório existe mas não expõe client, rotas nem asserts base — o "
            "executor não teria o que importar.\n"
            "O que fazer: prepare o projeto seguindo references/preparar-projeto.md da "
            "skill qa-api, ou aponte [caminhos].support_compartilhado para o diretório "
            "correto."
        )

    return SuperficieDoProjeto(raiz=relativo.replace("\\", "/"), modulos=modulos)


def _para_contrato(exportado: ExportJs) -> ExportCompartilhado:
    return ExportCompartilhado(
        nome=exportado.nome,
        declaracao=exportado.declaracao,
        comentario=exportado.comentario,
    )


def caminho_de_import(alvo: Path, de_onde: Path) -> str:
    """Especificador de import ES relativo, sempre com `./` ou `../` e `/`.

    `os.path.relpath` em vez de `Path.relative_to`: só ele sobe de nível, que é
    justamente o caso — o módulo compartilhado nunca está abaixo do recurso.
    """
    bruto = os.path.relpath(alvo.resolve(), de_onde.resolve()).replace("\\", "/")
    return bruto if bruto.startswith(".") else f"./{bruto}"
