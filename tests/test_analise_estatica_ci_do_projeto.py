"""Detecção de CI: onde procurar, até onde subir e o que conta como marcador."""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.analise_estatica.ci_do_projeto import (
    PLATAFORMAS,
    Geracao,
    detectar,
    plataformas_presentes,
)

pytestmark = pytest.mark.unit


def test_reconhece_cada_plataforma_pelo_marcador():
    assert [p.chave for p in plataformas_presentes({".github/workflows"})] == ["github"]
    assert [p.chave for p in plataformas_presentes({".gitlab-ci.yml"})] == ["gitlab"]
    assert [p.chave for p in plataformas_presentes({"Jenkinsfile"})] == ["jenkins"]
    assert [p.chave for p in plataformas_presentes({".circleci/config.yml"})] == ["circleci"]


def test_projeto_sem_marcador_nao_tem_ci():
    assert plataformas_presentes({"package.json", "cypress.config.js"}) == ()


def test_sobe_do_subprojeto_ate_a_raiz_do_repositorio(tmp_path: Path):
    # O arranjo comum: monorepo com o Cypress numa subpasta e o .github lá em cima.
    # Procurar só na raiz do projeto de testes responderia "não tem CI" para a
    # maioria dos casos em que tem.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    projeto = tmp_path / "apps" / "testes-de-api"
    projeto.mkdir(parents=True)

    achado = detectar(projeto)

    assert [p.chave for p in achado.plataformas] == ["github"]
    assert achado.raiz == tmp_path.resolve()


def test_a_subida_para_no_repositorio(tmp_path: Path):
    # O `.github` do diretório ACIMA do repositório é de outro projeto. Escrever
    # nele seria depositar arquivo num repositório que não é o do cliente.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    repositorio = tmp_path / "repo"
    (repositorio / ".git").mkdir(parents=True)

    assert not detectar(repositorio).tem_ci


def test_projeto_sem_ci_nenhuma(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    assert not detectar(tmp_path).tem_ci


def test_plataforma_que_nao_e_yaml_nao_gera_e_diz_por_que():
    jenkins = plataformas_presentes({"Jenkinsfile"})[0]
    assert jenkins.geracao is Geracao.NAO_GERA
    assert "Groovy" in jenkins.motivo


def test_so_o_github_descobre_arquivo_novo_sozinho():
    # É a diferença que decide onde escrevemos: no projeto do cliente, ou na nossa
    # execução com a linha de inclusão no relatório.
    todos = {marcador for plataforma in PLATAFORMAS for marcador in plataforma.marcadores}
    achadas = plataformas_presentes(todos)

    assert [p.chave for p in achadas if p.geracao is Geracao.NO_PROJETO] == ["github"]
    assert len(achadas) == len(PLATAFORMAS), "todo marcador da tabela precisa se reconhecer"
