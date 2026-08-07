"""Extração da superfície de módulos compartilhados do projeto de testes.

O buraco que isto fecha: o executor precisa acertar nome de export, forma dos
argumentos e profundidade do caminho relativo — três coisas que ninguém contava a
ele. O delta do Gate B ("import não resolve") não é acionável, então o loop de
reparo nunca convergiria.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orquestrador.agentes import executor as agente_executor
from orquestrador.contratos import Recurso
from orquestrador.excecoes import ProjetoNaoPreparado
from orquestrador.ferramentas import superficie as mod
from orquestrador.raiz import DIR_FIXTURES

FIXTURE_PROJETO = DIR_FIXTURES / "projeto-testes"


@pytest.fixture
def config_com_projeto(config_falso, tmp_path: Path):
    """Config apontando para uma cópia da fixture do projeto de testes."""
    projeto = tmp_path / "projeto-real"
    shutil.copytree(FIXTURE_PROJETO, projeto, dirs_exist_ok=True)
    caminhos = config_falso.caminhos.model_copy(update={"projeto_testes": projeto})
    return config_falso.model_copy(update={"caminhos": caminhos})


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------


def test_extrai_os_modulos_e_exports_da_fixture(config_com_projeto):
    superficie = mod.extrair(config_com_projeto)

    assert superficie.raiz == "cypress/support/api"
    caminhos = [modulo.caminho for modulo in superficie.modulos]
    assert caminhos == [
        "cypress/support/api/asserts.base.js",
        "cypress/support/api/client.js",
        "cypress/support/api/rotas.js",
    ]
    nomes = {
        exportado.nome
        for modulo in superficie.modulos
        for exportado in modulo.exports
    }
    assert nomes == {"statusExato", "semVazamentoInterno", "apiRequest", "RotasApi"}


def test_declaracao_de_funcao_sai_verbatim_e_sem_corpo(config_com_projeto):
    superficie = mod.extrair(config_com_projeto)
    client = next(m for m in superficie.modulos if m.caminho.endswith("client.js"))
    apiRequest = client.exports[0]

    # Verbatim: o texto é o do arquivo, não uma assinatura remontada.
    assert apiRequest.declaracao.endswith("export function apiRequest(opcoes)")
    # E sem o corpo, que não interessa a quem vai chamar.
    assert "cy.request" not in apiRequest.declaracao
    # O comentário de cima acompanha.
    assert "client compartilhado" in (apiRequest.comentario or "")


def test_objeto_congelado_sai_inteiro(config_com_projeto):
    # Num objeto de rotas as chaves são o que importa: cortar na primeira linha
    # entregaria `Object.freeze({` e nada mais.
    superficie = mod.extrair(config_com_projeto)
    rotas = next(m for m in superficie.modulos if m.caminho.endswith("rotas.js"))
    declaracao = rotas.exports[0].declaracao

    assert declaracao.startswith("export const RotasApi = Object.freeze({")
    assert 'colecao: "/pedidos"' in declaracao
    assert "porId: (id) => `/pedidos/${id}`" in declaracao


def test_caminho_de_import_e_calculado_dos_dois_lugares(config_com_projeto):
    superficie = mod.extrair(config_com_projeto)
    client = next(m for m in superficie.modulos if m.caminho.endswith("client.js"))

    # De um spec na raiz do recurso: cypress/e2e/apis/<recurso> -> cypress
    assert client.import_do_recurso == "../../../support/api/client.js"
    # De _support/, um nível mais fundo.
    assert client.import_do_support == "../../../../support/api/client.js"


def test_os_tres_layouts_da_skill_tem_caminho_proprio(config_com_projeto):
    # A skill descreve três posições para quem importa: `_support/`, spec na raiz do
    # recurso (layout plano) e spec em subpasta de sub-domínio (recurso composto).
    # O `_support/` não desce junto com o sub-domínio, então ele é o mais raso.
    superficie = mod.extrair(config_com_projeto)
    client = next(m for m in superficie.modulos if m.caminho.endswith("client.js"))

    assert client.import_do_recurso == "../../../support/api/client.js"
    assert client.import_do_subdominio == "../../../../support/api/client.js"
    assert client.import_do_support == "../../../../support/api/client.js"


def test_a_superficie_renderizada_mostra_os_tres_caminhos(config_com_projeto):
    texto = mod.extrair(config_com_projeto).render()
    assert "de `_support/`" in texto
    assert "spec na raiz do recurso" in texto
    assert "subpasta de sub-domínio" in texto


def test_barrel_file_aparece_em_vez_de_sumir(config_falso, tmp_path: Path):
    # Módulo que só re-exporta não tem nome declarado. Antes ele ficava com
    # `exports` vazio e era descartado: o executor não veria que existe.
    projeto = tmp_path / "com-barrel"
    api = projeto / "cypress" / "support" / "api"
    api.mkdir(parents=True)
    (api / "client.js").write_text("export function chamar(o) {}\n", encoding="utf-8")
    (api / "index.js").write_text('export * from "./client.js";\n', encoding="utf-8")
    caminhos = config_falso.caminhos.model_copy(update={"projeto_testes": projeto})

    superficie = mod.extrair(config_falso.model_copy(update={"caminhos": caminhos}))

    barrel = next(m for m in superficie.modulos if m.caminho.endswith("index.js"))
    assert [e.nome for e in barrel.exports] == ["*"]
    assert 'export * from "./client.js"' in barrel.exports[0].declaracao


def test_objeto_literal_simples_chega_inteiro_a_superficie(config_falso, tmp_path: Path):
    projeto = tmp_path / "literal"
    api = projeto / "cypress" / "support" / "api"
    api.mkdir(parents=True)
    (api / "rotas.js").write_text(
        'export const Rotas = {\n  colecao: "/pedidos",\n};\n', encoding="utf-8"
    )
    caminhos = config_falso.caminhos.model_copy(update={"projeto_testes": projeto})

    superficie = mod.extrair(config_falso.model_copy(update={"caminhos": caminhos}))

    declaracao = superficie.modulos[0].exports[0].declaracao
    assert declaracao != "export const Rotas ="
    assert 'colecao: "/pedidos"' in declaracao


def test_caminho_de_import_acompanha_dir_recursos(config_falso, tmp_path: Path):
    # Projeto com os specs um nível mais raso: a profundidade tem de mudar junto.
    projeto = tmp_path / "outro"
    (projeto / "testes" / "apis").mkdir(parents=True)
    (projeto / "suporte").mkdir(parents=True)
    (projeto / "suporte" / "client.js").write_text(
        "export function chamar(opcoes) {}\n", encoding="utf-8"
    )
    caminhos = config_falso.caminhos.model_copy(
        update={
            "projeto_testes": projeto,
            "dir_recursos": "testes/apis",
            "support_compartilhado": "suporte",
        }
    )
    superficie = mod.extrair(config_falso.model_copy(update={"caminhos": caminhos}))

    modulo = superficie.modulos[0]
    assert modulo.import_do_recurso == "../../../suporte/client.js"
    assert modulo.import_do_support == "../../../../suporte/client.js"


# ---------------------------------------------------------------------------
# Projeto não preparado
# ---------------------------------------------------------------------------


def test_sem_diretorio_falha_com_mensagem_acionavel(config_falso):
    with pytest.raises(ProjetoNaoPreparado) as erro:
        mod.extrair(config_falso)

    mensagem = str(erro.value)
    assert "preparar-projeto.md" in mensagem
    assert "support_compartilhado" in mensagem


def test_diretorio_sem_export_algum_falha(config_falso, tmp_path: Path):
    vazio = tmp_path / "vazio"
    (vazio / "cypress" / "support" / "api").mkdir(parents=True)
    (vazio / "cypress" / "support" / "api" / "notas.js").write_text(
        "// só comentário, nenhum export\nconst interno = 1;\n", encoding="utf-8"
    )
    caminhos = config_falso.caminhos.model_copy(update={"projeto_testes": vazio})

    with pytest.raises(ProjetoNaoPreparado, match="nenhum export"):
        mod.extrair(config_falso.model_copy(update={"caminhos": caminhos}))


# ---------------------------------------------------------------------------
# Formas de export que o projeto real usa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fonte", "nome", "esperado"),
    [
        (
            "export const f = (perfil) => {\n  return perfil;\n};\n",
            "f",
            "export const f = (perfil) =>",
        ),
        (
            "export const g = () =>\n  outra(1, 2);\n",
            "g",
            "export const g = () =>\n  outra(1, 2)",
        ),
        (
            "export const h = ({\n  metodo,\n  corpo,\n}) => {\n  return 1;\n};\n",
            "h",
            "export const h = ({\n  metodo,\n  corpo,\n}) =>",
        ),
        (
            "export async function carregar(id) {\n  return id;\n}\n",
            "carregar",
            "export async function carregar(id)",
        ),
        ("export class Cliente {\n  metodo() {}\n}\n", "Cliente", "export class Cliente"),
    ],
)
def test_recorta_as_formas_reais(fonte: str, nome: str, esperado: str):
    exports = mod.extrair_exports(fonte)
    assert [e.nome for e in exports] == [nome]
    assert exports[0].declaracao == esperado


def test_lista_de_nomes_e_alias():
    exports = mod.extrair_exports("export { alfa, beta as gama };\n")
    assert [e.nome for e in exports] == ["alfa", "gama"]


def test_string_com_chave_nao_confunde_o_recorte():
    fonte = 'export const rota = "/a/{id}/b";\n'
    assert mod.extrair_exports(fonte)[0].declaracao == 'export const rota = "/a/{id}/b"'


# ---------------------------------------------------------------------------
# Chegada ao prompt
# ---------------------------------------------------------------------------


def test_instrucao_do_executor_recebe_a_superficie(config_com_projeto):
    superficie = mod.extrair(config_com_projeto)
    recurso = Recurso(
        nome="pedidos", caminho_testes=config_com_projeto.caminhos.recurso("pedidos")
    )

    instrucao = agente_executor.instrucao_do_estagio(
        config_com_projeto, recurso, superficie
    )

    assert "{{superficie_do_projeto}}" not in instrucao
    assert "export function apiRequest(opcoes)" in instrucao
    assert "../../../../support/api/client.js" in instrucao
    assert "export const RotasApi = Object.freeze({" in instrucao


def test_a_superficie_nao_varia_entre_tentativas(config_com_projeto):
    # Ela entra pela instrução FIXA justamente por ser constante na execução: é o
    # que mantém a instrução idêntica entre tentativas e o cache de prompt viável.
    superficie = mod.extrair(config_com_projeto)
    recurso = Recurso(
        nome="pedidos", caminho_testes=config_com_projeto.caminhos.recurso("pedidos")
    )
    primeira = agente_executor.instrucao_do_estagio(config_com_projeto, recurso, superficie)
    segunda = agente_executor.instrucao_do_estagio(config_com_projeto, recurso, superficie)
    assert primeira == segunda
