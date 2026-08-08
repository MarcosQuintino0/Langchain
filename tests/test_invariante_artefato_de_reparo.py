"""O que "artefato atual" significa em cada estágio.

Princípio 2: `prompt_reparo = instrucao_fixa + artefato_atual + delta.violacoes`.
`tests/test_invariante_principio_2.py` guarda a fórmula; este arquivo guarda o **conteúdo do
segundo termo**, que tinha dois defeitos opostos:

* no mapeador ele era pequeno demais — só o manifesto, sem o inventário nem os
  schemas —, e uma violação sobre campo pedia correção num arquivo que o modelo
  não estava vendo;
* no executor ele era grande e cego — a concatenação da suíte cortada no
  caractere 60.000 —, e numa suíte grande o trecho reclamado caía depois do corte.

Os dois consertos apontam para o mesmo lugar: o artefato atual é o artefato, e o
recorte é dirigido pelas violações, nunca pela posição.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.contratos import ArquivoGerado, Delta, SaidaExecutor, Violacao
from orquestrador.llm.montagem import recortar_por_violacoes

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Bloco 1 — o bundle canônico do mapeador
# ---------------------------------------------------------------------------


def semear_artefato_do_mapeador(tmp_path: Path) -> dict[str, Path]:
    manifesto = tmp_path / "staging" / "_support" / "cobertura.json"
    manifesto.parent.mkdir(parents=True, exist_ok=True)
    manifesto.write_text('{"recurso": "pedidos", "MARCA": "manifesto"}\n', encoding="utf-8")

    inventario = tmp_path / "execucao" / "inventario.json"
    inventario.parent.mkdir(parents=True, exist_ok=True)
    inventario.write_text('{"recurso": "pedidos", "MARCA": "inventario"}\n', encoding="utf-8")

    dir_schemas = tmp_path / "execucao" / "schemas"
    (dir_schemas / "pedidos").mkdir(parents=True, exist_ok=True)
    (dir_schemas / "pedidos" / "entidade.schema.json").write_text(
        '{"MARCA": "schema-entidade"}\n', encoding="utf-8"
    )
    (dir_schemas / "pedidos" / "item.schema.json").write_text(
        '{"MARCA": "schema-item"}\n', encoding="utf-8"
    )
    return {"manifesto": manifesto, "inventario": inventario, "dir_schemas": dir_schemas}


def bundle(tmp_path: Path) -> str:
    caminhos = semear_artefato_do_mapeador(tmp_path)
    return agente_mapeador.artefato_em_disco(
        manifesto=caminhos["manifesto"],
        inventario=caminhos["inventario"],
        dir_schemas=caminhos["dir_schemas"],
        recurso="pedidos",
    )


def test_o_bundle_leva_os_tres_artefatos(tmp_path: Path):
    texto = bundle(tmp_path)

    assert "manifesto" in texto
    assert "inventario" in texto
    assert "schema-entidade" in texto
    assert "schema-item" in texto


def test_o_bundle_e_lido_do_disco_e_nao_da_saida_do_modelo(tmp_path: Path):
    caminhos = semear_artefato_do_mapeador(tmp_path)
    caminhos["manifesto"].write_text('{"MARCA": "o-que-esta-no-disco"}\n', encoding="utf-8")

    texto = agente_mapeador.artefato_em_disco(
        manifesto=caminhos["manifesto"],
        inventario=caminhos["inventario"],
        dir_schemas=caminhos["dir_schemas"],
        recurso="pedidos",
    )

    assert "o-que-esta-no-disco" in texto


def test_o_bundle_nao_carrega_historico_e_nao_cresce_por_repeticao(tmp_path: Path):
    # A defesa do princípio 2 aplicada ao próprio bundle: ele é função só do estado
    # do disco, então a terceira tentativa recebe exatamente o que a segunda receberia.
    primeiro = bundle(tmp_path)
    segundo = bundle(tmp_path)

    assert primeiro == segundo
    for proibido in ("tentativa", "violação", "violacao", "anterior"):
        assert proibido not in primeiro.lower()


def test_o_bundle_tem_ordem_fixa(tmp_path: Path):
    texto = bundle(tmp_path)
    posicoes = [
        texto.index("--- inventario.json ---"),
        texto.index("--- _support/cobertura.json ---"),
        texto.index("--- schemas/pedidos/entidade.schema.json ---"),
        texto.index("--- schemas/pedidos/item.schema.json ---"),
    ]
    # Ordem instável invalida cache de prompt e faz diff de log parecer mudança
    # de conteúdo.
    assert posicoes == sorted(posicoes)


def test_artefato_ausente_e_dito_e_nao_omitido(tmp_path: Path):
    caminhos = semear_artefato_do_mapeador(tmp_path)
    caminhos["inventario"].unlink()

    texto = agente_mapeador.artefato_em_disco(
        manifesto=caminhos["manifesto"],
        inventario=caminhos["inventario"],
        dir_schemas=caminhos["dir_schemas"],
        recurso="pedidos",
    )

    # Seção sumindo em silêncio faria o modelo concluir que o inventário não é
    # parte do artefato, e reemitir um que não bate com o manifesto.
    assert "--- inventario.json ---\n(ausente)" in texto


# ---------------------------------------------------------------------------
# Bloco 2 — o recorte dirigido pelas violações
# ---------------------------------------------------------------------------


def suite_grande() -> dict[str, str]:
    """Uma suíte que não cabe no limite: é onde o corte posicional falhava."""
    return {
        "crud.cy.js": "\n".join(f"// linha {numero} de crud" for numero in range(1, 1201)),
        "seguranca.cy.js": "\n".join(f"// linha {numero} de seguranca" for numero in range(1, 901)),
        "_support/api.js": "\n".join(f"// linha {numero} de api" for numero in range(1, 401)),
    }


def test_o_trecho_reclamado_entra_mesmo_em_suite_grande():
    arquivos = suite_grande()
    violacao = Violacao(
        codigo="QAAPI-025", mensagem="campo sem teste", arquivo="seguranca.cy.js", linha=880
    )

    texto = recortar_por_violacoes(arquivos, [violacao], limite=4_000)

    assert "// linha 880 de seguranca" in texto
    # Com o corte posicional o texto começava em crud.cy.js e acabava muito antes
    # da linha 880 do segundo arquivo.
    assert len(texto) <= 4_000 + len("// linha 880 de seguranca")


def test_o_recorte_traz_contexto_dos_dois_lados_e_nada_do_resto():
    arquivos = {"crud.cy.js": "\n".join(str(numero) for numero in range(1, 501))}
    violacao = Violacao(codigo="QAAPI-013", mensagem="x", arquivo="crud.cy.js", linha=250)

    texto = recortar_por_violacoes(arquivos, [violacao], contexto=3)

    for numero in range(247, 254):
        assert f"\t{numero}" in texto
    assert "\t246" not in texto
    assert "\t254" not in texto
    assert "linhas 247-253 de 500" in texto


def test_janelas_que_encostam_viram_uma_so():
    arquivos = {"crud.cy.js": "\n".join(str(numero) for numero in range(1, 101))}
    violacoes = [
        Violacao(codigo="QAAPI-013", mensagem="a", arquivo="crud.cy.js", linha=50),
        Violacao(codigo="QAAPI-013", mensagem="b", arquivo="crud.cy.js", linha=54),
    ]

    texto = recortar_por_violacoes(arquivos, violacoes, contexto=5)

    # Repetir o mesmo trecho é custo puro e ainda faz o modelo achar que são dois
    # lugares diferentes.
    assert texto.count("\t52\n") == 1
    assert "linhas 45-59 de 100" in texto


def test_violacao_sem_linha_traz_o_arquivo_inteiro():
    arquivos = {"crud.cy.js": "a\nb\nc\n", "outro.cy.js": "x\ny\n"}
    violacao = Violacao(codigo="QAAPI-002", mensagem="spec-base ausente", arquivo="crud.cy.js")

    texto = recortar_por_violacoes(arquivos, [violacao])

    assert "--- crud.cy.js (1-3 de 3) ---" in texto
    # A reclamação é sobre um arquivo; o outro não precisa vir junto.
    assert "--- outro.cy.js" not in texto


def test_violacao_sem_arquivo_traz_a_suite_e_o_indice():
    # Prettier reprovando o diretório não localiza nada. O que o modelo precisa
    # rever é a suíte inteira — e o índice garante que ele saiba o que existe
    # mesmo se algum arquivo for elidido por tamanho.
    arquivos = {"crud.cy.js": "a\nb\n", "outro.cy.js": "x\n"}
    violacao = Violacao(codigo="QAORQ-020", mensagem="mal formatado")

    texto = recortar_por_violacoes(arquivos, [violacao])

    assert "=== arquivos do artefato ===" in texto
    assert "- crud.cy.js (2 linha(s))" in texto
    assert "--- crud.cy.js" in texto
    assert "--- outro.cy.js" in texto


def test_o_indice_sobrevive_a_elisao_por_tamanho():
    arquivos = {f"{letra}.cy.js": "x" * 3_000 for letra in "abcdef"}
    violacao = Violacao(codigo="QAORQ-020", mensagem="mal formatado")

    texto = recortar_por_violacoes(arquivos, [violacao], limite=5_000)

    for letra in "abcdef":
        assert f"- {letra}.cy.js" in texto
    assert "omitido(s) por tamanho" in texto


def test_sem_violacao_o_artefato_vai_inteiro():
    # O primeiro reparo depois de um erro de schema não tem violação de arquivo;
    # ali o artefato atual é a suíte como ela está.
    arquivos = {"crud.cy.js": "a\nb\n", "outro.cy.js": "x\n"}
    texto = recortar_por_violacoes(arquivos, [])
    assert "--- crud.cy.js" in texto
    assert "--- outro.cy.js" in texto


def test_arquivo_da_violacao_casa_por_sufixo():
    # O `file` vem relativo à raiz do recurso no validador da skill e relativo ao
    # projeto no eslint. Casar só por igualdade perderia o segundo.
    arquivos = {"crud.cy.js": "\n".join(str(n) for n in range(1, 51))}
    violacao = Violacao(
        codigo="QAORQ-021",
        mensagem="x",
        arquivo="cypress/e2e/apis/pedidos/crud.cy.js",
        linha=25,
    )

    texto = recortar_por_violacoes(arquivos, [violacao], contexto=1)

    assert "linhas 24-26 de 50" in texto


def test_o_executor_le_o_staging_e_nao_a_saida_do_modelo(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "crud.cy.js").write_text("// o que está no disco\n", encoding="utf-8")
    saida = SaidaExecutor(
        recurso="pedidos",
        arquivos=[ArquivoGerado(caminho="crud.cy.js", conteudo="// o que o modelo disse\n")],
    )
    delta = Delta(
        estagio="gate_b",
        recurso="pedidos",
        violacoes=[Violacao(codigo="QAORQ-020", mensagem="mal formatado")],
        tentativa=1,
    )

    texto = agente_executor.artefato_em_disco(staging, saida, delta)

    # É o disco que o gate mediu; mandar de volta a string do modelo esconderia
    # qualquer diferença entre o que ele pediu e o que foi gravado.
    assert "o que está no disco" in texto
    assert "o que o modelo disse" not in texto
