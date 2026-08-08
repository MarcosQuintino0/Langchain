"""Parser de `export` — a matriz de formas que o extrator precisa acertar.

Cada linha aqui é uma forma que aparece em projeto real. As que já passavam antes
da correção do discriminador do `{` estão incluídas de propósito: são elas que
impedem a correção de quebrar o que funcionava.

O defeito que motivou o módulo: todo `{` de nível zero era tratado como corpo de
função, então `export const Rotas = { … }` saía como `export const Rotas =`. Isso
**parece informação** — quem lê inventa as chaves achando que está seguindo a
superfície, e não há sinal de que falta algo.
"""

from __future__ import annotations

import pytest

from orquestrador.analise_estatica.exports_javascript import (
    MARCA_DE_TRUNCAMENTO,
    MAX_DECLARACAO,
    extrair_exports,
    nomes_exportados,
)

OBJETO_LITERAL = 'export const Rotas = {\n  colecao: "/x",\n  porId: (id) => `/x/${id}`,\n};\n'
CONGELADO = 'export const Rotas = Object.freeze({\n  colecao: "/x",\n});\n'


# ---------------------------------------------------------------------------
# A matriz
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rotulo", "fonte", "nome", "declaracao"),
    [
        # -- formas que já funcionavam: a rede que protege a correção -----------
        (
            "função com default de objeto no parâmetro",
            "export function criar(payload, opcoes = {}) {\n  return 1;\n}\n",
            "criar",
            "export function criar(payload, opcoes = {})",
        ),
        (
            "arrow de expressão",
            "export const criar = (p) => apiRequest(p);\n",
            "criar",
            "export const criar = (p) => apiRequest(p)",
        ),
        (
            "arrow com corpo — corta antes do corpo",
            "export const criar = (p) => {\n  return p;\n};\n",
            "criar",
            "export const criar = (p) =>",
        ),
        (
            "Object.freeze — literal inteiro",
            CONGELADO,
            "Rotas",
            'export const Rotas = Object.freeze({\n  colecao: "/x",\n})',
        ),
        (
            "classe",
            "export class Cliente {\n  metodo() {}\n}\n",
            "Cliente",
            "export class Cliente",
        ),
        (
            "const escalar",
            "export const LIMITE = 120;\n",
            "LIMITE",
            "export const LIMITE = 120",
        ),
        (
            "async function",
            "export async function carregar(id) {\n  return id;\n}\n",
            "carregar",
            "export async function carregar(id)",
        ),
        # -- as duas linhas vermelhas da tabela ----------------------------------
        (
            "objeto literal simples — preserva as chaves",
            OBJETO_LITERAL,
            "Rotas",
            'export const Rotas = {\n  colecao: "/x",\n  porId: (id) => `/x/${id}`,\n}',
        ),
        (
            "re-export nomeado — preserva a origem",
            'export { apiRequest } from "./client.js";\n',
            "apiRequest",
            'export { apiRequest } from "./client.js"',
        ),
    ],
)
def test_matriz_de_formas(rotulo: str, fonte: str, nome: str, declaracao: str):
    exports = extrair_exports(fonte)
    assert [e.nome for e in exports] == [nome], rotulo
    assert exports[0].declaracao == declaracao, rotulo


def test_objeto_literal_nao_perde_as_chaves():
    # O caso que o desenho anterior quebrava em silêncio.
    declaracao = extrair_exports(OBJETO_LITERAL)[0].declaracao
    assert declaracao != "export const Rotas ="
    assert 'colecao: "/x"' in declaracao
    assert "porId: (id) => `/x/${id}`" in declaracao


def test_re_export_preserva_o_from():
    declaracao = extrair_exports('export { apiRequest } from "./client.js";\n')[0].declaracao
    assert 'from "./client.js"' in declaracao


# ---------------------------------------------------------------------------
# Re-export estrela: aparece em vez de sumir
# ---------------------------------------------------------------------------


def test_export_estrela_aparece_na_superficie():
    # Barrel file: sem isto, o módulo ficava com `exports` vazio e era descartado —
    # o executor não veria que ele existe nem para onde aponta.
    exports = extrair_exports('export * from "./client.js";\n')
    assert [e.nome for e in exports] == ["*"]
    assert exports[0].declaracao == 'export * from "./client.js"'


def test_export_estrela_com_alias_usa_o_alias():
    exports = extrair_exports('export * as Client from "./client.js";\n')
    assert [e.nome for e in exports] == ["Client"]


# ---------------------------------------------------------------------------
# Multi-linha e casos de borda
# ---------------------------------------------------------------------------


def test_lista_de_nomes_em_varias_linhas():
    fonte = 'export {\n  alfa,\n  beta as gama,\n} from "./x.js";\n'
    exports = extrair_exports(fonte)
    assert [e.nome for e in exports] == ["alfa", "gama"]
    assert exports[0].declaracao.endswith('} from "./x.js"')


def test_objeto_sem_ponto_e_virgula_encerra_no_fim_do_literal():
    fonte = "export const A = {\n  x: 1,\n}\n\nconst outro = 2;\n"
    assert extrair_exports(fonte)[0].declaracao == "export const A = {\n  x: 1,\n}"


def test_default_com_objeto():
    exports = extrair_exports("export default { a: 1 };\n")
    assert [e.nome for e in exports] == ["default"]
    assert exports[0].declaracao == "export default { a: 1 }"


def test_parametros_destruturados_em_varias_linhas():
    fonte = "export const h = ({\n  metodo,\n  corpo,\n}) => {\n  return 1;\n};\n"
    assert extrair_exports(fonte)[0].declaracao == (
        "export const h = ({\n  metodo,\n  corpo,\n}) =>"
    )


def test_chave_dentro_de_string_nao_confunde():
    assert extrair_exports('export const rota = "/a/{id}/b";\n')[0].declaracao == (
        'export const rota = "/a/{id}/b"'
    )


def test_comentario_de_bloco_entre_assinatura_e_corpo():
    fonte = "export function f(a) /* nota */ {\n  return a;\n}\n"
    assert extrair_exports(fonte)[0].declaracao == "export function f(a) /* nota */"


def test_comentario_acima_acompanha_o_export():
    fonte = "// Fonte única das rotas.\n// Segunda linha.\nexport const R = 1;\n"
    assert extrair_exports(fonte)[0].comentario == ("// Fonte única das rotas.\n// Segunda linha.")


def test_jsdoc_acima_acompanha_o_export():
    fonte = "/**\n * Client HTTP.\n */\nexport function apiRequest(o) {}\n"
    assert "Client HTTP" in extrair_exports(fonte)[0].comentario


def test_declaracao_longa_leva_marcador_de_truncamento():
    corpo = "\n".join(f'  chave{n}: "valor bem longo para encher a conta",' for n in range(80))
    exports = extrair_exports(f"export const Grande = {{\n{corpo}\n}};\n")
    declaracao = exports[0].declaracao
    assert declaracao.endswith(MARCA_DE_TRUNCAMENTO)
    assert len(declaracao) <= MAX_DECLARACAO + len(MARCA_DE_TRUNCAMENTO)


def test_nenhuma_declaracao_sai_vazia():
    # `export` solto não descreve nada e não pode virar entrada da superfície.
    assert extrair_exports("export\n") == []
    assert extrair_exports("exportar(algo);\n") == []


def test_linha_que_apenas_menciona_export_nao_conta():
    assert extrair_exports('const texto = "export const X = 1";\n') == []


def test_nomes_exportados_ignora_texto_sem_export():
    assert nomes_exportados("const x = 1") == []
