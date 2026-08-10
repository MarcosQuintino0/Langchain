"""Medição segura de artefatos persistidos: caminho, bytes e hash, nunca conteúdo."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import JsonValue

__all__ = ["medir_arquivos"]


def medir_arquivos(caminhos: list[Path]) -> list[dict[str, JsonValue]]:
    medidos: list[dict[str, JsonValue]] = []
    for caminho in caminhos:
        item: dict[str, JsonValue] = {"caminho": str(caminho)}
        try:
            bruto = caminho.read_bytes()
        except OSError as erro:
            item["erro_tipo"] = type(erro).__name__
        else:
            item["bytes"] = len(bruto)
            item["sha256"] = hashlib.sha256(bruto).hexdigest()
        medidos.append(item)
    return medidos
