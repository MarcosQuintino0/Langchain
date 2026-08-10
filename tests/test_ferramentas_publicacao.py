"""Escrita transacional: o item do plano que pode destruir trabalho de terceiro.

Todo teste aqui responde à mesma pergunta por um ângulo diferente: **o projeto do
consumidor sobrevive?** Uma tentativa ruim, uma queda no meio da substituição, um
arquivo que ele editou enquanto rodávamos, uma remoção automática mal calibrada —
em nenhum desses casos ele pode perder byte.

O que este arquivo NÃO cobre: se o artefato presta. Isso é dos gates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.aplicacao.pipeline import (
    NAO_EXECUTADO,
    Pipeline,
    ResultadoDaExecucaoDeTestes,
    ResultadoDoRecurso,
)
from orquestrador.cli import principal as modulo_cli
from orquestrador.cli.codigos_de_saida import FALHA_DE_GATE, REQUER_REVISAO, SUCESSO
from orquestrador.dominio.propriedade import Classificacao, DivergenciaDeSchema, EntradaDoDiario
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import EstadoDoRecurso, ResultadoGate
from orquestrador.excecoes import FalhaDeGate, FalhaDePublicacao
from orquestrador.ferramentas.publicacao import (
    Diario,
    criar_area,
    hash_do_arquivo,
    remover_criados,
)
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit

DO_CLIENTE = "// escrito à mão pelo dono do projeto\n"


@pytest.fixture
def pipeline(config_falso, tmp_path: Path) -> Pipeline:
    registro = Registro(
        tmp_path / "execucao.jsonl",
        Console(file=(tmp_path / "console.txt").open("w", encoding="utf-8"), width=200),
    )
    return Pipeline(
        config_falso,
        registro,
        dry_run=True,
        roteiros=None,
        dir_execucao=tmp_path / "execucoes" / "20260101-000000-1",
    )


@pytest.fixture
def recurso(config_falso) -> Recurso:
    caminho = config_falso.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(
        nome="pedidos",
        caminho_testes=caminho,
        raiz_schemas=config_falso.caminhos.dir_schemas_abs,
    )


def area_de(pipeline: Pipeline, recurso: Recurso, **extras):
    return criar_area(
        recurso=recurso.nome,
        destino_recurso=recurso.caminho_testes,
        destino_schemas=recurso.caminho_schemas,
        dir_execucao=pipeline.dir_execucao,
        **extras,
    )


# ---------------------------------------------------------------------------
# O staging fica onde a skill continua achando o que precisa
# ---------------------------------------------------------------------------


def test_o_staging_e_irmao_do_recurso_no_mesmo_nivel(pipeline, recurso):
    """A profundidade é o requisito, não a estética.

    Os specs importam os módulos compartilhados por caminho relativo
    (`../../../../support/api/client.js`) e o validador da skill resolve isso a
    partir do arquivo. Um staging em outra profundidade faz todo spec reprovar com
    `QAAPI-004`; um staging em outra árvore faz o `handlers.mjs` não achar o
    `.agents/config/qa-api/handlers.json` ao subir.
    """
    area = area_de(pipeline, recurso)

    assert area.dir_recurso.parent == recurso.caminho_testes.parent
    assert area.dir_recurso != recurso.caminho_testes
    # O ponto inicial esconde o staging do specPattern padrão do Cypress, então um
    # staging esquecido por uma execução morta não entra na suíte do consumidor.
    assert area.dir_recurso.name.startswith(".")


def test_o_staging_de_schemas_fica_fora_do_projeto(pipeline, recurso):
    # Pode ficar fora porque as duas ferramentas aceitam o diretório pronto
    # (`--schemas`); sem essa flag ele teria de morar no projeto também.
    area = area_de(pipeline, recurso)
    assert pipeline.dir_execucao in area.dir_schemas.parents


# ---------------------------------------------------------------------------
# Publicação só depois da aprovação
# ---------------------------------------------------------------------------


def test_escrever_no_staging_nao_toca_no_destino(pipeline, recurso):
    area = area_de(pipeline, recurso)

    area.escrever("crud.cy.js", "// nova suíte\n")

    assert (area.dir_recurso / "crud.cy.js").is_file()
    assert not (recurso.caminho_testes / "crud.cy.js").exists()


def test_gate_reprovado_deixa_o_projeto_intacto(pipeline, recurso, monkeypatch):
    """Publicação só acontece após aprovação — ponta a ponta, pelo `_rodar_recurso`."""
    preexistente = recurso.caminho_testes / "crud.cy.js"
    preexistente.write_text(DO_CLIENTE, encoding="utf-8")
    antes = sorted(p.name for p in recurso.caminho_testes.iterdir())

    def bloco1_falso(_recurso, area):
        area.escrever("crud.cy.js", "// gerado e reprovado\n")
        raise FalhaDeGate("gate_a reprovou", arquivos=[area.dir_recurso / "crud.cy.js"])

    monkeypatch.setattr(pipeline, "bloco1", bloco1_falso)
    monkeypatch.setattr(pipeline, "bloco_plano", lambda *_a, **_k: None)

    resultado = pipeline._rodar_recurso(recurso)

    assert resultado.estado is EstadoDoRecurso.REPROVADO
    assert resultado.publicado is False
    assert preexistente.read_text(encoding="utf-8") == DO_CLIENTE
    assert sorted(p.name for p in recurso.caminho_testes.iterdir()) == antes


def test_publicar_leva_tudo_de_uma_vez(pipeline, recurso):
    area = area_de(pipeline, recurso)
    area.escrever("crud.cy.js", "// suíte\n")
    area.escrever_schema("pedidos/entidade.schema.json", "{}\n")

    entradas = area.publicar()

    assert (recurso.caminho_testes / "crud.cy.js").read_text(encoding="utf-8") == "// suíte\n"
    assert (recurso.caminho_schemas / "pedidos" / "entidade.schema.json").is_file()
    assert {e.classificacao for e in entradas} == {Classificacao.CRIADO}


# ---------------------------------------------------------------------------
# Interrupção no meio da escrita
# ---------------------------------------------------------------------------


def test_interrupcao_no_meio_da_publicacao_preserva_byte_a_byte(pipeline, recurso, monkeypatch):
    """A garantia central da Etapa 2, no caminho mais perigoso que existe.

    Dois arquivos do consumidor no destino; a substituição do segundo explode. O
    primeiro já tinha sido trocado — e precisa voltar exatamente ao que era, não
    "a um conteúdo equivalente".
    """
    alvos = {}
    for nome in ("a.cy.js", "b.cy.js"):
        alvo = recurso.caminho_testes / nome
        alvo.write_text(f"// {nome} do cliente\n", encoding="utf-8")
        alvos[nome] = (alvo, alvo.read_bytes())

    area = area_de(pipeline, recurso)
    for nome in alvos:
        area.escrever(nome, f"// {nome} gerado\n")

    original = Path.replace
    chamadas = {"n": 0}

    def replace_que_falha(self: Path, destino):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise OSError("disco cheio no meio da publicação")
        return original(self, destino)

    monkeypatch.setattr(Path, "replace", replace_que_falha)

    with pytest.raises(FalhaDePublicacao, match="desfeita"):
        area.publicar()

    for nome, (alvo, bytes_originais) in alvos.items():
        assert alvo.read_bytes() == bytes_originais, f"{nome} não voltou byte a byte"
    # E nenhum temporário sobrou no diretório do consumidor.
    assert sorted(p.name for p in recurso.caminho_testes.iterdir()) == ["a.cy.js", "b.cy.js"]


def test_rollback_apaga_o_que_nao_existia_antes(pipeline, recurso, monkeypatch):
    # Desfazer a criação de um arquivo é sumir com ele, não restaurar um vazio:
    # deixar um arquivo de zero byte no projeto seria pior que não ter publicado.
    (recurso.caminho_testes / "a.cy.js").write_text(DO_CLIENTE, encoding="utf-8")
    area = area_de(pipeline, recurso)
    area.escrever("a.cy.js", "// gerado\n")
    area.escrever("b.cy.js", "// novo\n")

    original = Path.replace
    chamadas = {"n": 0}

    def replace_que_falha(self: Path, destino):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise OSError("falhou")
        return original(self, destino)

    monkeypatch.setattr(Path, "replace", replace_que_falha)

    with pytest.raises(FalhaDePublicacao):
        area.publicar()

    assert (recurso.caminho_testes / "a.cy.js").read_text(encoding="utf-8") == DO_CLIENTE
    assert not (recurso.caminho_testes / "b.cy.js").exists()


def test_destino_alterado_durante_a_execucao_aborta_sem_publicar(pipeline, recurso):
    # O artefato foi validado contra outro estado do projeto. Publicar por cima
    # apagaria a edição de alguém com um resultado que já não corresponde ao disco.
    alvo = recurso.caminho_testes / "crud.cy.js"
    alvo.write_text(DO_CLIENTE, encoding="utf-8")
    area = area_de(pipeline, recurso)
    area.escrever("crud.cy.js", "// gerado\n")

    alvo.write_text("// o dono editou enquanto rodávamos\n", encoding="utf-8")

    with pytest.raises(FalhaDePublicacao, match="mudaram durante a execução"):
        area.publicar()

    assert alvo.read_text(encoding="utf-8") == "// o dono editou enquanto rodávamos\n"


# ---------------------------------------------------------------------------
# Diário de propriedade
# ---------------------------------------------------------------------------


def test_o_diario_classifica_criado_modificado_e_preexistente(pipeline, recurso):
    modificado = recurso.caminho_testes / "antigo.cy.js"
    modificado.write_text(DO_CLIENTE, encoding="utf-8")
    schema_do_cliente = recurso.caminho_schemas / "pedidos" / "entidade.schema.json"
    schema_do_cliente.parent.mkdir(parents=True, exist_ok=True)
    schema_do_cliente.write_text('{"type": "object"}\n', encoding="utf-8")

    area = area_de(pipeline, recurso)
    area.escrever("novo.cy.js", "// novo\n")
    area.escrever("antigo.cy.js", "// regerado\n")
    area.preservar_schema("pedidos/entidade.schema.json")

    por_nome = {e.destino.name: e for e in area.publicar()}

    assert por_nome["novo.cy.js"].classificacao is Classificacao.CRIADO
    assert por_nome["novo.cy.js"].hash_anterior is None
    assert por_nome["antigo.cy.js"].classificacao is Classificacao.MODIFICADO
    assert por_nome["antigo.cy.js"].hash_anterior is not None
    assert por_nome["entidade.schema.json"].classificacao is Classificacao.PREEXISTENTE
    # Preservado é preservado: a publicação nem chega a escrever nele.
    assert schema_do_cliente.read_text(encoding="utf-8") == '{"type": "object"}\n'


def test_arquivo_que_criamos_ontem_continua_nosso_hoje(pipeline, recurso, tmp_path):
    # Sem a propagação, regenerar um spec nosso o rebaixaria a "modificado" e ele
    # ficaria intocável para sempre — a ferramenta perderia a capacidade de limpar
    # a própria sujeira.
    area = area_de(pipeline, recurso)
    area.escrever("crud.cy.js", "// primeira execução\n")
    diario = Diario(tmp_path / "diario.json")
    diario.registrar(area.publicar())

    segunda = area_de(pipeline, recurso, criados_antes=diario.criados())
    segunda.escrever("crud.cy.js", "// segunda execução\n")
    por_nome = {e.destino.name: e for e in segunda.publicar()}

    assert por_nome["crud.cy.js"].classificacao is Classificacao.CRIADO


def test_o_diario_poda_entrada_cujo_arquivo_sumiu(pipeline, recurso, tmp_path):
    # Sem a poda, cada sandbox de --dry-run deixa caminhos que nunca mais casam com
    # nada e o arquivo cresce para sempre. Entrada cujo destino sumiu não autoriza
    # remoção nenhuma, então perdê-la não perde informação útil.
    area = area_de(pipeline, recurso)
    area.escrever("some.cy.js", "// vai ser apagado à mão\n")
    diario = Diario(tmp_path / "diario.json")
    diario.registrar(area.publicar())
    (recurso.caminho_testes / "some.cy.js").unlink()

    outra = area_de(pipeline, recurso)
    outra.escrever("fica.cy.js", "// fica\n")
    atualizado = diario.registrar(outra.publicar())

    assert [e.destino.name for e in atualizado.entradas] == ["fica.cy.js"]


def test_diario_ilegivel_vira_diario_vazio(tmp_path):
    # A única coisa que o diário autoriza é remoção. Sem ele, a resposta certa é
    # não remover nada — nunca explodir e nunca chutar.
    caminho = tmp_path / "diario.json"
    caminho.write_text("{isto não é json", encoding="utf-8")
    assert Diario(caminho).criados() == frozenset()


# ---------------------------------------------------------------------------
# Remoção restrita ao que criamos
# ---------------------------------------------------------------------------


def test_remover_criados_nao_toca_em_preexistente_nem_em_modificado(pipeline, recurso):
    preexistente = recurso.caminho_schemas / "pedidos" / "entidade.schema.json"
    preexistente.parent.mkdir(parents=True, exist_ok=True)
    preexistente.write_text('{"type": "object"}\n', encoding="utf-8")
    do_cliente = recurso.caminho_testes / "antigo.cy.js"
    do_cliente.write_text(DO_CLIENTE, encoding="utf-8")

    area = area_de(pipeline, recurso)
    area.escrever("novo.cy.js", "// novo\n")
    area.escrever("antigo.cy.js", "// regerado\n")
    area.preservar_schema("pedidos/entidade.schema.json")
    entradas = area.publicar()

    removidos, recusados = remover_criados(entradas)

    assert removidos == [recurso.caminho_testes / "novo.cy.js"]
    assert set(recusados) == {do_cliente, preexistente}
    assert preexistente.is_file()
    assert do_cliente.is_file()


def test_remover_criados_recusa_o_que_mudou_depois_de_nos(pipeline, recurso):
    # Um spec que nasceu conosco e o desenvolvedor melhorou à mão deixa de ser
    # descartável no instante em que ele o salva.
    area = area_de(pipeline, recurso)
    area.escrever("crud.cy.js", "// gerado\n")
    entradas = area.publicar()
    alvo = recurso.caminho_testes / "crud.cy.js"
    alvo.write_text("// melhorado à mão\n", encoding="utf-8")

    removidos, recusados = remover_criados(entradas)

    assert removidos == []
    assert recusados == [alvo]
    assert alvo.read_text(encoding="utf-8") == "// melhorado à mão\n"


def test_a_cli_remove_so_o_criado_do_recurso_reprovado(tmp_path):
    nosso = tmp_path / "novo.cy.js"
    nosso.write_text("// nosso\n", encoding="utf-8")
    dele = tmp_path / "dele.cy.js"
    dele.write_text(DO_CLIENTE, encoding="utf-8")

    resultado = ResultadoDoRecurso(
        recurso="pedidos",
        estado=EstadoDoRecurso.REPROVADO,
        publicado=True,
        diario=[
            _entrada(nosso, Classificacao.CRIADO),
            _entrada(dele, Classificacao.MODIFICADO),
        ],
    )
    registro = Registro(
        tmp_path / "log.jsonl",
        Console(file=(tmp_path / "c.txt").open("w", encoding="utf-8"), width=200),
    )

    modulo_cli.remover_reprovados([resultado], registro)

    assert not nosso.exists()
    assert dele.read_text(encoding="utf-8") == DO_CLIENTE


def test_a_cli_nao_remove_nada_de_recurso_aprovado(tmp_path):
    nosso = tmp_path / "novo.cy.js"
    nosso.write_text("// nosso\n", encoding="utf-8")
    resultado = ResultadoDoRecurso(
        recurso="pedidos",
        estado=EstadoDoRecurso.APROVADO,
        publicado=True,
        diario=[_entrada(nosso, Classificacao.CRIADO)],
    )
    registro = Registro(
        tmp_path / "log.jsonl",
        Console(file=(tmp_path / "c.txt").open("w", encoding="utf-8"), width=200),
    )

    modulo_cli.remover_reprovados([resultado], registro)

    assert nosso.is_file()


def _entrada(caminho: Path, classificacao: Classificacao):
    return EntradaDoDiario(
        destino=caminho,
        classificacao=classificacao,
        hash_anterior=None if classificacao is Classificacao.CRIADO else "qualquer",
        hash_novo=hash_do_arquivo(caminho),
        recurso="pedidos",
    )


# ---------------------------------------------------------------------------
# Spec obsoleto de execução anterior
# ---------------------------------------------------------------------------


def blocos_falsos(pipeline: Pipeline, monkeypatch, specs: dict[str, str]) -> None:
    """Substitui os três blocos por escritas fixas no staging, todas aprovadas.

    O que está sob teste aqui é a publicação, não a geração: o roteiro dos blocos é
    exatamente "grave estes arquivos e aprove".
    """

    def bloco1(_recurso, area):
        area.escrever("_support/cobertura.json", "{}\n")
        return _saida_com_manifesto(), ResultadoGate.aprovado_por(), 1

    def bloco2(_recurso, _manifesto, area, _plano=None):
        for nome, conteudo in specs.items():
            area.escrever(nome, conteudo)
        return None, ResultadoGate.aprovado_por(), 1

    monkeypatch.setattr(pipeline, "bloco1", bloco1)
    monkeypatch.setattr(pipeline, "bloco_plano", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "bloco2", bloco2)
    monkeypatch.setattr(pipeline, "bloco3", lambda _r: _bloco3_falso())


def test_spec_obsoleto_do_diario_e_removido_na_publicacao(pipeline, recurso, monkeypatch):
    blocos_falsos(pipeline, monkeypatch, {"crud.cy.js": "// v1\n", "obsoleto.cy.js": "// some\n"})
    pipeline._rodar_recurso(recurso)
    assert (recurso.caminho_testes / "obsoleto.cy.js").is_file()

    # A execução seguinte gera só um spec. O outro consta no diário como criado por
    # nós e o hash não mudou desde então: é a única combinação que autoriza apagar.
    blocos_falsos(pipeline, monkeypatch, {"crud.cy.js": "// v2\n"})
    pipeline._rodar_recurso(recurso)

    assert (recurso.caminho_testes / "crud.cy.js").read_text(encoding="utf-8") == "// v2\n"
    assert not (recurso.caminho_testes / "obsoleto.cy.js").exists()


def test_spec_obsoleto_editado_pelo_dono_nao_e_removido(pipeline, recurso, monkeypatch):
    blocos_falsos(pipeline, monkeypatch, {"obsoleto.cy.js": "// some\n"})
    pipeline._rodar_recurso(recurso)

    obsoleto = recurso.caminho_testes / "obsoleto.cy.js"
    obsoleto.write_text("// o dono acrescentou um caso importante aqui\n", encoding="utf-8")

    blocos_falsos(pipeline, monkeypatch, {"crud.cy.js": "// v2\n"})
    pipeline._rodar_recurso(recurso)

    assert obsoleto.read_text(encoding="utf-8").startswith("// o dono acrescentou")


# ---------------------------------------------------------------------------
# O terceiro estado do recurso
# ---------------------------------------------------------------------------


def test_divergencia_de_schema_encerra_em_requer_revisao(pipeline, recurso, monkeypatch):
    def bloco1(_recurso, area):
        area.escrever("_support/cobertura.json", "{}\n")
        pipeline.persistencia.divergencias.append(
            DivergenciaDeSchema(
                recurso="pedidos",
                arquivo=recurso.caminho_schemas / "pedidos" / "entidade.schema.json",
                campos_ausentes=["total"],
            )
        )
        return _saida_com_manifesto(), ResultadoGate.aprovado_por(), 1

    blocos_falsos(pipeline, monkeypatch, {"crud.cy.js": "// suíte\n"})
    monkeypatch.setattr(pipeline, "bloco1", bloco1)

    resultado = pipeline._rodar_recurso(recurso)

    assert resultado.estado is EstadoDoRecurso.REQUER_REVISAO
    # Distinto dos outros dois nos dois sentidos: não é aprovado...
    assert resultado.sucesso is False
    # ...e não é reprovado — o artefato foi publicado, porque os gates aprovaram.
    assert resultado.publicado is True
    assert (recurso.caminho_testes / "crud.cy.js").is_file()
    # E o diff é legível por máquina, não só uma frase no console.
    artefato = pipeline.dir_execucao / "artefatos" / "pedidos" / "divergencias-de-schema.json"
    assert json.loads(artefato.read_text(encoding="utf-8"))[0]["campos_ausentes"] == ["total"]


@pytest.mark.parametrize(
    ("estados", "esperado"),
    [
        ([EstadoDoRecurso.APROVADO], SUCESSO),
        ([EstadoDoRecurso.APROVADO, EstadoDoRecurso.REQUER_REVISAO], REQUER_REVISAO),
        ([EstadoDoRecurso.REQUER_REVISAO, EstadoDoRecurso.REPROVADO], FALHA_DE_GATE),
    ],
)
def test_o_codigo_de_saida_distingue_os_tres_estados(estados, esperado):
    # Revisão pendente não pode sair 0: em CI, 0 é "nada a ver aqui".
    resultados = [ResultadoDoRecurso(recurso=f"r{i}", estado=e) for i, e in enumerate(estados)]
    assert modulo_cli.codigo_de_saida(resultados) == esperado


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _saida_com_manifesto():
    class Saida:
        manifesto = None

    return Saida()


def _bloco3_falso():
    return ResultadoDaExecucaoDeTestes(estado=NAO_EXECUTADO, contadores={})
