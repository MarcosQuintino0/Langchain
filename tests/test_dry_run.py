"""Critério de aceite: o pipeline roda ponta a ponta sem chamar nenhum modelo.

Este é um teste de integração de verdade — os scripts `.mjs` da skill são
invocados sobre arquivos escritos em disco. Precisa de Node.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from orquestrador import cli as modulo_cli
from orquestrador.raiz import DIR_FIXTURES, RAIZ_PROJETO
from orquestrador.simulacao import Roteiros

# A raiz do projeto vem do módulo único que a resolve (nada de Path(__file__) aqui).
RAIZ = RAIZ_PROJETO


def _skill_configurada() -> Path:
    """Onde a skill `qa-api` está, segundo o config.toml deste projeto.

    A skill vive em outro repositório; ler o caminho da configuração evita que o
    teste adivinhe uma posição relativa que não existe mais.
    """
    arquivo = RAIZ / "config.toml"
    if not arquivo.is_file():
        return Path("skill-nao-configurada")
    with arquivo.open("rb") as fluxo:
        bruto = tomllib.load(fluxo)
    caminho = Path(str(bruto.get("caminhos", {}).get("skill", "")))
    return caminho if caminho.is_absolute() else (RAIZ / caminho).resolve()


SKILL = _skill_configurada()

precisa_de_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node não está no PATH"
)
precisa_da_skill = pytest.mark.skipif(
    not (SKILL / "scripts" / "validar-suite-gerada.mjs").is_file(),
    reason=f"skill qa-api não encontrada em {SKILL}",
)


@pytest.fixture
def config_toml(tmp_path: Path) -> Path:
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(
        f"""
[caminhos]
skill = {str(SKILL)!r}
backend = {str(tmp_path / "backend-ignorado")!r}
projeto_testes = {str(tmp_path / "projeto-ignorado")!r}
dir_recursos = "cypress/e2e/apis"
graph = ".agents/state/qa-api/graphify-out/graph.json"
saida = {str(tmp_path / "execucoes")!r}

[estagios.mapeador]
modelo = "<placeholder: dry-run não chama modelo>"
[estagios.executor]
modelo = "<placeholder: dry-run não chama modelo>"

[gates.a]
flags = ["--so-manifesto"]
max_tentativas = 3
[gates.b]
flags = ["--exigir-campos"]
max_tentativas = 3
""",
        encoding="utf-8",
    )
    return arquivo


def ultima_execucao(base: Path) -> Path:
    return max(base.iterdir(), key=lambda caminho: caminho.stat().st_mtime)


@precisa_de_node
@precisa_da_skill
def test_dry_run_completo_com_reparo_nos_dois_gates(config_toml: Path, tmp_path: Path):
    codigo = modulo_cli.main(
        ["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)]
    )
    assert codigo == 0

    execucao = ultima_execucao(tmp_path / "execucoes")
    eventos = [
        json.loads(linha)
        for linha in (execucao / "execucao.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    gates = [evento for evento in eventos if evento["tipo"] == "gate"]

    # Cada gate reprovou uma vez e aprovou na tentativa seguinte.
    for nome in ("gate_a", "gate_b"):
        deste = [evento for evento in gates if evento["gate"] == nome]
        assert [evento["aprovado"] for evento in deste] == [False, True], nome
        assert deste[0]["violacoes"], f"{nome} precisa reprovar com violações reais"

    # O delta do Gate A trouxe a contabilidade das 12 categorias (QAAPI-021)…
    delta_a = next(
        evento for evento in eventos if evento["tipo"] == "delta" and evento["estagio"] == "gate_a"
    )
    assert "QAAPI-021" in delta_a["codigos"]

    # …e o do Gate B, a cobertura por campo e o spec-base ausente.
    delta_b = next(
        evento for evento in eventos if evento["tipo"] == "delta" and evento["estagio"] == "gate_b"
    )
    assert {"QAAPI-025", "QAAPI-002"} <= set(delta_b["codigos"])

    # Nenhum modelo foi chamado.
    chamadas = [evento for evento in eventos if evento["tipo"] == "chamada_llm"]
    assert chamadas and all(evento["simulado"] for evento in chamadas)

    # Telemetria por estágio no log.
    telemetria = next(evento for evento in eventos if evento["tipo"] == "telemetria")
    assert set(telemetria["por_estagio"]) == {"mapeador", "executor"}
    assert telemetria["total"]["entrada"] > 0


@precisa_de_node
@precisa_da_skill
def test_o_reparo_nao_cresce_o_contexto(config_toml: Path, tmp_path: Path):
    # A prova prática do princípio 2: a tentativa de reparo do mapeador entra com
    # MENOS tokens que a primeira, porque recebe só o artefato e as violações —
    # não a exploração inteira que a antecedeu.
    assert (
        modulo_cli.main(
            ["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)]
        )
        == 0
    )
    execucao = ultima_execucao(tmp_path / "execucoes")
    chamadas = [
        json.loads(linha)
        for linha in (execucao / "execucao.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(linha)["tipo"] == "chamada_llm"
    ]
    mapeador = [evento for evento in chamadas if evento["estagio"] == "mapeador"]
    assert mapeador[1]["uso"]["entrada"] < mapeador[0]["uso"]["entrada"]


@precisa_de_node
@precisa_da_skill
def test_artefatos_ficam_em_disco(config_toml: Path, tmp_path: Path):
    modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)])
    execucao = ultima_execucao(tmp_path / "execucoes")
    recurso = (
        execucao / "sandbox" / "projeto-testes" / "cypress" / "e2e" / "apis" / "pedidos"
    )

    manifesto = json.loads((recurso / "_support" / "cobertura.json").read_text("utf-8"))
    assert manifesto["recurso"] == "pedidos"
    # O manifesto reparado contabiliza as 12 categorias em todos os endpoints.
    for entrada in manifesto["endpoints"]:
        assert len(set(entrada["cats"]) | set(entrada["naoAplica"])) == 12

    for spec in ("crud.cy.js", "validacoes.cy.js", "seguranca.cy.js"):
        assert (recurso / spec).is_file()
    assert (execucao / "artefatos" / "pedidos" / "inventario.json").is_file()


def test_roteiros_repetem_o_ultimo_quando_a_tentativa_excede():
    roteiros = Roteiros(DIR_FIXTURES / "roteiros")
    assert list(roteiros.recursos()) == ["pedidos"]
    ultimo = roteiros.carregar("pedidos", "mapeador", 2)
    assert roteiros.carregar("pedidos", "mapeador", 9) == ultimo
