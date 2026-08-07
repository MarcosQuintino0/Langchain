"""Execução de processos externos.

Regras que valem para todo subprocess deste projeto (aprendidas no Windows):

* sempre **lista de argumentos**, nunca `shell=True` com string montada — separador
  de caminho e aspas quebram de formas difíceis de diagnosticar;
* `stdout` e `stderr` capturados **separadamente** — o `validar-suite-gerada.mjs`
  escreve no stdout quando aprova e no stderr quando reprova, então quem lê só
  stdout enxerga reprovação como saída vazia;
* `encoding="utf-8"` explícito — a saída dos scripts tem acentuação;
* `.cmd`/`.bat` (npx, npm, prettier) resolvidos por `shutil.which` antes da chamada.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


from orquestrador.excecoes import ErroDeFerramenta, ExecutavelAusente

__all__ = [
    "ErroDeFerramenta",
    "ExecutavelAusente",
    "SaidaProcesso",
    "executar",
    "executar_node",
    "resolver_executavel",
]


@dataclass(frozen=True)
class SaidaProcesso:
    argv: list[str]
    codigo: int
    stdout: str
    stderr: str
    duracao_s: float
    cwd: str | None = None
    campos_extras: dict[str, str] = field(default_factory=dict)

    @property
    def texto(self) -> str:
        """stdout e stderr juntos, para log e para `saida_bruta` do gate."""
        partes = [parte for parte in (self.stdout.strip(), self.stderr.strip()) if parte]
        return "\n".join(partes)

    @property
    def comando(self) -> str:
        return " ".join(self.argv)


def resolver_executavel(nome: str) -> str:
    """Caminho completo do executável, tolerando .cmd/.bat/.exe do Windows."""
    caminho = Path(nome)
    if caminho.is_file():
        return str(caminho)
    encontrado = shutil.which(nome)
    if encontrado:
        return encontrado
    raise ExecutavelAusente(
        f'executável não encontrado no PATH: "{nome}". '
        "Instale-o ou ajuste [execucao] na configuração."
    )


def executar(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    timeout_s: int = 600,
    resolver: bool = True,
) -> SaidaProcesso:
    """Roda um comando e devolve stdout, stderr e código de saída."""
    if not argv:
        raise ErroDeFerramenta("comando vazio")
    comando = list(argv)
    if resolver:
        comando[0] = resolver_executavel(comando[0])

    inicio = time.perf_counter()
    try:
        concluido = subprocess.run(  # noqa: S603 - lista de argumentos, sem shell
            comando,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=False,
            check=False,
        )
    except FileNotFoundError as erro:
        raise ExecutavelAusente(f"não foi possível executar {comando[0]}: {erro}") from erro
    except subprocess.TimeoutExpired as erro:
        raise ErroDeFerramenta(
            f"tempo esgotado ({timeout_s}s) em: {' '.join(argv)}"
        ) from erro

    return SaidaProcesso(
        argv=argv,
        codigo=concluido.returncode,
        stdout=concluido.stdout or "",
        stderr=concluido.stderr or "",
        duracao_s=time.perf_counter() - inicio,
        cwd=str(cwd) if cwd else None,
    )


def executar_node(
    script: Path,
    argumentos: list[str],
    *,
    node: str = "node",
    cwd: Path | str | None = None,
    timeout_s: int = 600,
) -> SaidaProcesso:
    """Invoca um script `.mjs` da skill."""
    if not script.is_file():
        raise ErroDeFerramenta(f"script não encontrado: {script}")
    return executar(
        [node, str(script), *argumentos], cwd=cwd, timeout_s=timeout_s
    )
