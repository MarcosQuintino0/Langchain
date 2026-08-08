"""Log estruturado (JSONL) + console.

Requisito: deve ser possível reconstruir o que aconteceu sem reexecutar. Cada
evento carrega estágio, recurso, tentativa, tokens, veredito do gate e códigos de
violação.

O **nome** de cada evento não mora aqui: mora em `eventos.py`, que é o dono do
vocabulário. Este módulo é dono do arquivo, do console e da forma da linha —
incluindo o `schema_version`, que é o que mantém log de ontem legível depois de
uma mudança de formato.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from rich.console import Console
from rich.markup import escape

from orquestrador.contratos import dados_para_log
from orquestrador.observabilidade.eventos import ESQUEMA_DOS_EVENTOS, TipoDeEvento


class RegistradorDeEventos(Protocol):
    """A fatia de `Registro` que quem só emite evento precisa enxergar.

    `Telemetria` e `GeradorEstruturado` recebem um registrador opcional e chamam
    exatamente um método nele. Declarar o parâmetro como `Registro` arrastaria
    console, arquivo aberto e ciclo de vida para dentro de módulos que não são donos
    de nenhum dos três; declarar como `Any` — que era o que havia — apagava até a
    existência de `evento` do verificador, e um erro de digitação no nome do método
    só apareceria em execução, dentro de um `if` que quase nunca roda em teste.
    """

    def evento(self, tipo: TipoDeEvento | str, **campos: Any) -> None: ...


def configurar_console() -> None:
    """Força UTF-8 na saída padrão.

    O console do Windows costuma vir em cp1252, e aí um simples "✓" derruba a
    execução inteira com UnicodeEncodeError. `errors="replace"` garante que
    nenhuma mensagem de log consiga matar o pipeline.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            # `sys.stdout` é declarado `TextIO`, e `reconfigure` só existe no
            # `TextIOWrapper` concreto — que é o que está lá quando se roda num
            # terminal, e não está sob `pytest` ou redirecionamento. Um `isinstance`
            # não substituiria o `except`: os casos que interessam são o `OSError` do
            # fluxo não-reconfigurável e o `ValueError` do fluxo fechado, que
            # acontecem no `TextIOWrapper` de verdade.
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
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

    def evento(self, tipo: TipoDeEvento | str, **campos: Any) -> None:
        """Escreve uma linha do JSONL. Nunca levanta por causa do tipo do evento.

        A união com `str` é **fase de transição**, não relaxamento: `agentes/` e
        `llm/` também emitem, e trocar a assinatura dos dois numa mudança que
        ninguém pediu ali é o tipo de conflito que o working tree compartilhado
        transforma em retrabalho. Enquanto durar, quem garante que nenhuma string
        solta entra é `tests/test_observabilidade_eventos.py`, que varre a AST de `src/` e exige
        que todo primeiro argumento de `.evento(...)` seja membro de
        `TipoDeEvento` ou literal com valor de um membro.

        A checagem é estática, e não uma conversão que levanta aqui, de propósito:
        `staging_mantido` é emitido dentro de um `finally` que quase nunca roda, e
        um `ValueError` ali derrubaria a execução escondendo o erro original —
        observabilidade não decide fluxo, e muito menos o encerra. A varredura por
        AST pega o mesmo defeito antes de rodar, inclusive nos ramos raros.
        """
        linha = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "t_s": round(time.perf_counter() - self.inicio, 3),
            "schema_version": ESQUEMA_DOS_EVENTOS,
            "tipo": str(tipo),
            **{chave: dados_para_log(valor) for chave, valor in campos.items()},
        }
        self._fluxo.write(json.dumps(linha, ensure_ascii=False) + "\n")
        self._fluxo.flush()

    # -- console ------------------------------------------------------------
    #
    # O texto que chega aqui vem de mensagem de erro, de saída de script e de
    # violação de gate — e quase sempre tem colchete. O Rich os lê como markup e
    # engole o que parecer um estilo: "[caminhos]" some, "[QAAPI-025]" sobrevive.
    # Escapar o texto e manter só as tags que este módulo escreve resolve os dois.

    def titulo(self, texto: str) -> None:
        self.console.rule(f"[bold]{escape(texto)}")

    def info(self, texto: str) -> None:
        self.console.print(escape(texto))

    def ok(self, texto: str) -> None:
        self.console.print(f"[green]✓[/green] {escape(texto)}")

    def falha(self, texto: str) -> None:
        self.console.print(f"[red]✗[/red] {escape(texto)}")

    def aviso(self, texto: str) -> None:
        self.console.print(f"[yellow]![/yellow] {escape(texto)}")

    def fechar(self) -> None:
        if not self._fluxo.closed:
            self._fluxo.close()

    def __enter__(self) -> Registro:
        return self

    def __exit__(self, *_excecao: object) -> None:
        self.fechar()


def diretorio_de_execucao(base: Path) -> Path:
    """`<base>/<AAAAMMDD-HHMMSS>-<pid>` — uma pasta por execução."""
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = base / f"{marca}-{os.getpid()}"
    destino.mkdir(parents=True, exist_ok=True)
    return destino
