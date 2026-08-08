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
from typing import Any

import pytest

from orquestrador.config import Config
from orquestrador.dominio.manifesto import CATS
from orquestrador.ferramentas.processo import SaidaProcesso

# Os três markers de `[tool.pytest.ini_options] markers`. A definição de cada um
# está lá; aqui vale só a exigência de que exista exatamente um por teste.
MARKERS_DE_CLASSE = frozenset({"unit", "integration", "e2e"})


def pytest_addoption(parser: pytest.Parser) -> None:
    """`--mostrar-golden=<arquivo>`: imprime a saída atual e **não grava nada**.

    É deliberadamente "mostrar" e não "atualizar". Um `--update-goldens` transforma
    "por que isto mudou?" num reflexo de teclado; obrigar a copiar a saída do
    terminal para o arquivo mantém a pergunta no caminho. Ver
    `tests/goldens/README.md`.
    """
    parser.addoption(
        "--mostrar-golden",
        default="",
        metavar="ARQUIVO",
        help="imprime o conteúdo atual do golden indicado, sem gravá-lo.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Todo teste declara exatamente um marker de classe, ou a coleta falha.

    Hook e não teste, por duas razões. A exigência passa a valer também em
    `pytest tests/test_x.py`, então um teste novo sem marker reprova no primeiro
    `pytest` de quem o escreveu — e não numa linha vermelha da CI três dias depois,
    quando o contexto já se perdeu. E a falha é de coleta, o que impede a suíte de
    rodar num estado em que `-m unit` mente sobre o que cobre.

    **Exatamente um**, e não "pelo menos um": dois markers de classe fazem o mesmo
    teste ser contado duas vezes pelo job de integração, e
    `.github/scripts/checar_pulos.py` passaria a somar errado. Cuidado com módulo
    misto — o pytest **soma** `pytestmark` com o decorator da função.
    """
    sem_classe = sorted(
        item.nodeid
        for item in items
        if len({marca.name for marca in item.iter_markers()} & MARKERS_DE_CLASSE) != 1
    )
    if sem_classe:
        raise pytest.UsageError(
            "teste(s) sem exatamente um marker de classe:\n  "
            + "\n  ".join(sem_classe[:20])
            + (f"\n  … e mais {len(sem_classe) - 20}" if len(sem_classe) > 20 else "")
            + "\n\nEscolha um: `unit` (roda com o venv e nada mais), `integration` "
            "(precisa de Node, dos .mjs da skill, do uv ou de outro executável) ou "
            "`e2e` (o pipeline inteiro). Módulo homogêneo declara "
            "`pytestmark = pytest.mark.unit` uma vez; módulo misto usa decorator "
            "por função, nunca os dois — eles se somam."
        )


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
def manifesto_minimo() -> Callable[..., dict[str, Any]]:
    """Fábrica do menor `_support/cobertura.json` que passa na validação estrutural.

    Um endpoint com CAT-01 declarada e as outras onze justificadas — a forma que
    `test_dominio_manifesto`, `test_dominio_artefatos` e `test_dominio_recurso`
    precisam para exercitar coisas diferentes sobre o mesmo alvo. Fixture, e não
    função importável: `from conftest import` só resolve porque o rootdir entra no
    `sys.path`, e há teste proibindo.
    """

    def construir(**extra: Any) -> dict[str, Any]:
        return {
            "recurso": "pedidos",
            "endpoints": [
                {
                    "endpoint": "GET /pedidos",
                    "cats": ["CAT-01"],
                    "naoAplica": dict.fromkeys(CATS[1:], "justificativa suficientemente longa"),
                }
            ],
            **extra,
        }

    return construir


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
