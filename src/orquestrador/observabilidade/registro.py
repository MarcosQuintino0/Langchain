"""Log estruturado (JSONL) + console.

Requisito: deve ser possível reconstruir o que aconteceu sem reexecutar. Cada
evento carrega estágio, recurso, tentativa, tokens, veredito do gate e códigos de
violação.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from orquestrador.contratos import dados_para_log


def configurar_console() -> None:
    """Força UTF-8 na saída padrão.

    O console do Windows costuma vir em cp1252, e aí um simples "✓" derruba a
    execução inteira com UnicodeEncodeError. `errors="replace"` garante que
    nenhuma mensagem de log consiga matar o pipeline.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass


class Registro:
    """Escreve eventos em JSONL e ecoa o essencial no console."""

    def __init__(self, caminho: Path, console: Console | None = None) -> None:
        self.caminho = caminho
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._fluxo = self.caminho.open("a", encoding="utf-8")
        self.console = console or Console()
        self.inicio = time.perf_counter()

    # -- eventos ------------------------------------------------------------

    def evento(self, tipo: str, **campos: Any) -> None:
        linha = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "t_s": round(time.perf_counter() - self.inicio, 3),
            "tipo": tipo,
            **{chave: dados_para_log(valor) for chave, valor in campos.items()},
        }
        self._fluxo.write(json.dumps(linha, ensure_ascii=False) + "\n")
        self._fluxo.flush()

    # -- console ------------------------------------------------------------

    def titulo(self, texto: str) -> None:
        self.console.rule(f"[bold]{texto}")

    def info(self, texto: str) -> None:
        self.console.print(texto)

    def ok(self, texto: str) -> None:
        self.console.print(f"[green]✓[/green] {texto}")

    def falha(self, texto: str) -> None:
        self.console.print(f"[red]✗[/red] {texto}")

    def aviso(self, texto: str) -> None:
        self.console.print(f"[yellow]![/yellow] {texto}")

    def fechar(self) -> None:
        if not self._fluxo.closed:
            self._fluxo.close()

    def __enter__(self) -> "Registro":
        return self

    def __exit__(self, *_excecao: object) -> None:
        self.fechar()


def diretorio_de_execucao(base: Path) -> Path:
    """`<base>/<AAAAMMDD-HHMMSS>-<pid>` — uma pasta por execução."""
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = base / f"{marca}-{os.getpid()}"
    destino.mkdir(parents=True, exist_ok=True)
    return destino
