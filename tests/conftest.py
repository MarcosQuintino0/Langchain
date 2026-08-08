"""Fixtures compartilhadas.

Sem `sys.path.insert`: o projeto é instalável (`pip install -e ".[dev]"`), então
`orquestrador` é importável de qualquer diretório de trabalho.

Tudo que sai daqui sai como **fixture**, nunca como função importável. `conftest`
é um arquivo que o pytest injeta, não um módulo de biblioteca: `from conftest
import x` só funciona porque o rootdir entra no `sys.path`, quebra quando o
diretório de testes ganha um nível, e faz o ferramental de import (ruff, mypy,
IDE) resolver um módulo que não existe como pacote. Precisa compartilhar um
helper? Devolva-o de uma fixture.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from orquestrador.config import Config
from orquestrador.ferramentas.processo import SaidaProcesso


@pytest.fixture
def config_falso(tmp_path: Path) -> Config:
    """Config mínima e coerente, apontando para diretórios temporários."""
    for nome in ("skill/scripts", "backend", "projeto/cypress/e2e/apis"):
        (tmp_path / nome).mkdir(parents=True, exist_ok=True)
    return Config.model_validate(
        {
            "caminhos": {
                "skill": str(tmp_path / "skill"),
                "backend": str(tmp_path / "backend"),
                "projeto_testes": str(tmp_path / "projeto"),
                "saida": str(tmp_path / "saida"),
            },
            "estagios": {
                "mapeador": {"modelo": "fake/mapeador"},
                "executor": {"modelo": "fake/executor"},
            },
            "gates": {
                "a": {"flags": ["--so-manifesto"], "max_tentativas": 3},
                "b": {"flags": [], "max_tentativas": 2},
            },
        }
    )


@pytest.fixture
def saida_de_processo() -> Callable[..., SaidaProcesso]:
    """Fábrica de `SaidaProcesso` para exercitar parsing sem Node instalado."""

    def construir(*, codigo: int = 0, stdout: str = "", stderr: str = "") -> SaidaProcesso:
        return SaidaProcesso(
            argv=["node", "validar-suite-gerada.mjs", "recurso", "--json"],
            codigo=codigo,
            stdout=stdout,
            stderr=stderr,
            duracao_s=0.01,
        )

    return construir
