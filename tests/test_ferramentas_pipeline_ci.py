"""Geração da pipeline: as três recusas valem mais que a escrita.

Não gerar sem CI, não sobrescrever e não editar arquivo alheio são o que separa
"entregou uma pipeline" de "mexeu no repositório de quem nos contratou".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.analise_estatica.ci_do_projeto import CiDoProjeto, detectar
from orquestrador.ferramentas.pipeline_ci import escritas_no_projeto, gerar

pytestmark = pytest.mark.unit

SPECS = ["cypress/e2e/apis/customers/**/*.cy.js"]


@pytest.fixture
def repositorio(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_projeto_sem_ci_nao_recebe_nada(repositorio: Path):
    # Quem não tem integração contínua não pediu uma, e um .yml depositado num
    # repositório que nunca rodou nada é palpite, não entrega.
    assert gerar(detectar(repositorio), specs=SPECS, dir_execucao=repositorio) == []


def test_github_recebe_arquivo_novo_no_projeto(repositorio: Path):
    (repositorio / ".github" / "workflows").mkdir(parents=True)

    resultados = gerar(detectar(repositorio), specs=SPECS, dir_execucao=repositorio / "exec")

    alvo = repositorio / ".github" / "workflows" / "testes-de-api.yml"
    assert alvo.is_file()
    assert escritas_no_projeto(resultados) == [alvo]
    conteudo = alvo.read_text(encoding="utf-8")
    assert 'npx cypress run --spec "cypress/e2e/apis/customers/**/*.cy.js"' in conteudo
    # Teto de tempo: job pendurado consome minuto de quem paga.
    assert "timeout-minutes:" in conteudo
    # Segredo por variável CYPRESS_, nunca literal.
    assert "secrets.CYPRESS_API_URL" in conteudo


def test_arquivo_existente_nunca_e_sobrescrito(repositorio: Path):
    # Nem o nosso, de uma execução anterior: o dono pode tê-lo ajustado, e a
    # versão dele vale mais que a nossa.
    workflows = repositorio / ".github" / "workflows"
    workflows.mkdir(parents=True)
    alvo = workflows / "testes-de-api.yml"
    alvo.write_text("# ajustado à mão pelo dono do projeto\n", encoding="utf-8")

    resultados = gerar(detectar(repositorio), specs=SPECS, dir_execucao=repositorio / "exec")

    assert alvo.read_text(encoding="utf-8") == "# ajustado à mão pelo dono do projeto\n"
    assert resultados[0].escrita is None
    assert "já existe e não foi tocado" in resultados[0].motivo


def test_gitlab_recebe_sugestao_fora_do_projeto_e_a_linha_de_inclusao(repositorio: Path):
    # Editar o .gitlab-ci.yml do cliente é a mesma classe de erro que sobrescrever
    # um spec dele. A sugestão fica na NOSSA execução, e o relatório diz o que fazer.
    original = repositorio / ".gitlab-ci.yml"
    original.write_text("stages: [build]\n", encoding="utf-8")
    execucao = repositorio / "exec"

    resultados = gerar(detectar(repositorio), specs=SPECS, dir_execucao=execucao)

    assert original.read_text(encoding="utf-8") == "stages: [build]\n"
    assert resultados[0].escrita == execucao / "testes-de-api.gitlab-ci.yml"
    assert resultados[0].escrita.is_file()
    assert escritas_no_projeto(resultados) == [], "sugestão não entra no diário"
    assert "include:" in resultados[0].render()


def test_jenkins_nao_gera_e_o_relatorio_diz_por_que(repositorio: Path):
    (repositorio / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")

    resultados = gerar(detectar(repositorio), specs=SPECS, dir_execucao=repositorio / "exec")

    assert resultados[0].escrita is None
    assert "Groovy" in resultados[0].render()


def test_sem_spec_publicado_nao_gera(repositorio: Path):
    # Pipeline que não aponta para teste nenhum passa em verde sem rodar nada, que
    # é o pior resultado possível: verde mentindo.
    (repositorio / ".github" / "workflows").mkdir(parents=True)

    assert gerar(detectar(repositorio), specs=[], dir_execucao=repositorio / "exec") == []


def test_sem_raiz_detectada_nao_gera(repositorio: Path):
    assert gerar(CiDoProjeto(plataformas=()), specs=SPECS, dir_execucao=repositorio) == []
