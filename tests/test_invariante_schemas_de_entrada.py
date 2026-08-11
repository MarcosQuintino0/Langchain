"""O mapeador emite os schemas de entrada e o Bloco 1 os grava fora do recurso.

Nenhum outro estágio pode fazer isso: o executor não lê o backend e escreve apenas
dentro do diretório do recurso, e os schemas moram em `cypress/fixtures/schemas/`.
Sem eles, todo endpoint de escrita reprova no Gate A com QAAPI-027 — de forma
determinística, não intermitente.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.aplicacao.persistencia import nomes_de_campos
from orquestrador.aplicacao.pipeline import Pipeline
from orquestrador.aplicacao.simulacao import ModeloSimulado, PassoFinal
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import ResultadoGate, Violacao
from orquestrador.excecoes import FalhaDeGate
from orquestrador.ferramentas.publicacao import AreaDeStaging, criar_area
from orquestrador.gates import gate_a
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Pedido",
    "type": "object",
    "required": ["situacao"],
    "properties": {"situacao": {"type": "string", "enum": ["ABERTO", "FECHADO"]}},
}

ARTEFATO = {
    "inventario": {
        "recurso": "pedidos",
        "endpoints": [
            {
                "metodo": "POST",
                "rota": "/pedidos",
                "handler": "PedidoController.criar",
                "arquivo": "src/controllers/PedidoController.java",
                "linha": 17,
            }
        ],
    },
    "manifesto": {
        "recurso": "pedidos",
        "endpoints": [{"endpoint": "POST /pedidos", "schemaEntrada": "entidade"}],
    },
    "schemas": [
        {
            "caminho": "pedidos/entidade.schema.json",
            "conteudo": json.dumps(SCHEMA, ensure_ascii=False, indent=2) + "\n",
        }
    ],
}


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
        dir_execucao=tmp_path / "execucao",
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


@pytest.fixture
def area(pipeline: Pipeline, recurso: Recurso) -> AreaDeStaging:
    """A área de staging do Bloco 1. Nada dela chega ao projeto sem publicação."""
    return criar_area(
        recurso=recurso.nome,
        destino_recurso=recurso.caminho_testes,
        destino_schemas=recurso.caminho_schemas,
        dir_execucao=pipeline.dir_execucao,
    )


def staged(area: AreaDeStaging) -> Path:
    return area.dir_schemas / "pedidos" / "entidade.schema.json"


NOTAS_DE_TESTE = """# Notas de descoberta — pedidos

## Endpoints do recurso
- POST /pedidos | handler PedidoController.criar | src/controllers/PedidoController.java:17

## Rotas dinâmicas não resolvidas
- nenhuma
"""

# O menor dossiê que valida: o Gate A está monkeypatchado nestes testes, então a
# completude da checklist não importa — só a forma.
DOSSIE_DE_TESTE = {"recurso": "pedidos"}


def modelo_por_estagio(artefato: dict):
    """A fábrica que o pipeline consulta: notas na exploração, fatia nas fatias.

    Espelha o contrato real do Bloco 1 fatiado — o `pipeline.modelo` é chamado
    com `mapeador` para a exploração e `mapeador-<fatia>` para cada fatia.
    """

    def modelo(estagio: str, _recurso: str, _tentativa: int) -> ModeloSimulado:
        if estagio == "mapeador":
            return ModeloSimulado(passos=[PassoFinal(tipo="final", conteudo=NOTAS_DE_TESTE)])
        por_fatia = {
            "mapeador-manifesto": artefato["manifesto"],
            "mapeador-schemas": {"schemas": artefato["schemas"]},
            "mapeador-dossie": DOSSIE_DE_TESTE,
        }
        return ModeloSimulado(passos=[PassoFinal(tipo="final", artefato=por_fatia[estagio])])

    return modelo


def preparar(pipeline: Pipeline, monkeypatch, veredito: ResultadoGate) -> None:
    """Modelo de fixture no lugar do OpenRouter e um Gate A com veredito fixo."""
    monkeypatch.setattr(pipeline, "modelo", modelo_por_estagio(ARTEFATO))
    monkeypatch.setattr(gate_a, "executar", lambda *_a, **_k: veredito)


def test_bloco1_grava_o_schema_no_staging_e_nao_no_projeto(pipeline, recurso, area, monkeypatch):
    preparar(pipeline, monkeypatch, ResultadoGate.aprovado_por())

    saida, _resultado, tentativas = pipeline.bloco1(recurso, area)

    assert tentativas == 1
    assert staged(area).is_file(), "o schema precisa sair do diretório do recurso"
    assert json.loads(staged(area).read_text(encoding="utf-8")) == SCHEMA
    assert (area.dir_recurso / "_support" / "cobertura.json").is_file()
    assert saida.schemas[0].caminho == "pedidos/entidade.schema.json"
    # E nada disso encostou no projeto do consumidor.
    assert not recurso.manifesto_path.exists()
    assert not (recurso.caminho_schemas / "pedidos" / "entidade.schema.json").exists()


def test_a_publicacao_leva_manifesto_e_schema_ao_projeto(pipeline, recurso, area, monkeypatch):
    preparar(pipeline, monkeypatch, ResultadoGate.aprovado_por())
    pipeline.bloco1(recurso, area)

    area.publicar()

    assert recurso.manifesto_path.is_file()
    emitido = recurso.caminho_schemas / "pedidos" / "entidade.schema.json"
    assert json.loads(emitido.read_text(encoding="utf-8")) == SCHEMA


def test_o_schema_entra_na_conta_do_que_ficou_reprovado(pipeline, recurso, area, monkeypatch):
    # A3: ele fica em disco no projeto do usuário, como o manifesto — então precisa
    # ser anunciado junto, e não sumir da lista por ter sido escrito noutra raiz.
    preparar(
        pipeline,
        monkeypatch,
        ResultadoGate.reprovado_por([Violacao(codigo="QAAPI-021", mensagem="cat faltando")]),
    )

    with pytest.raises(FalhaDeGate) as erro:
        pipeline.bloco1(recurso, area)

    assert erro.value.arquivos == [
        area.dir_recurso / "_support" / "cobertura.json",
        staged(area),
    ]


def test_o_evento_artefatos_registra_o_schema(pipeline, recurso, area, monkeypatch, tmp_path):
    preparar(pipeline, monkeypatch, ResultadoGate.aprovado_por())
    pipeline.bloco1(recurso, area)
    pipeline.registro.fechar()

    eventos = [
        json.loads(linha)
        for linha in (tmp_path / "execucao.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    artefatos = next(
        evento
        for evento in eventos
        if evento["tipo"] == "artefatos" and evento["dados"]["estagio"] == "mapeador"
    )
    assert any(
        "entidade.schema.json" in arquivo["caminho"] for arquivo in artefatos["dados"]["arquivos"]
    )


def test_recurso_sem_raiz_de_schemas_diz_o_que_falta(config_falso):
    recurso = Recurso(nome="pedidos", caminho_testes=config_falso.caminhos.recurso("pedidos"))
    with pytest.raises(ValueError, match="raiz de schemas"):
        _ = recurso.caminho_schemas


# ---------------------------------------------------------------------------
# O schema do consumidor é dele
# ---------------------------------------------------------------------------
#
# No desenho da skill o schema já existe no projeto do cliente e o AJV valida
# respostas com ele. A autoridade dele como denominador vem de não ter sido escrito
# por quem vai ser medido — então o mapeador não pode passar por cima.

DO_CLIENTE = '{"type": "object", "properties": {"situacao": {"type": "string"}}}\n'


def caminho_do_schema(recurso: Recurso) -> Path:
    return recurso.caminho_schemas / "pedidos" / "entidade.schema.json"


def semear_schema_do_cliente(recurso: Recurso) -> Path:
    alvo = caminho_do_schema(recurso)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(DO_CLIENTE, encoding="utf-8")
    return alvo


def artefato_com_schema(esquema: dict) -> dict:
    conteudo = json.dumps(esquema, ensure_ascii=False, indent=2) + "\n"
    return {
        **ARTEFATO,
        "schemas": [{"caminho": "pedidos/entidade.schema.json", "conteudo": conteudo}],
    }


def test_schema_preexistente_do_cliente_nao_e_sobrescrito(pipeline, recurso, area, monkeypatch):
    alvo = semear_schema_do_cliente(recurso)
    preparar(pipeline, monkeypatch, ResultadoGate.aprovado_por())

    pipeline.bloco1(recurso, area)
    area.publicar()

    assert alvo.read_text(encoding="utf-8") == DO_CLIENTE
    # E a cópia no staging é a do cliente, não a do modelo: é ela o denominador
    # que o gate mede.
    assert staged(area).read_text(encoding="utf-8") == DO_CLIENTE


def test_schema_preservado_fica_fora_da_conta_de_reprovado(pipeline, recurso, area, monkeypatch):
    # `--remover-reprovados` percorre esta lista. Arquivo do cliente que nem
    # chegamos a escrever não pode estar nela.
    semear_schema_do_cliente(recurso)
    preparar(
        pipeline,
        monkeypatch,
        ResultadoGate.reprovado_por([Violacao(codigo="QAAPI-021", mensagem="cat faltando")]),
    )

    with pytest.raises(FalhaDeGate) as erro:
        pipeline.bloco1(recurso, area)

    assert erro.value.arquivos == [area.dir_recurso / "_support" / "cobertura.json"]


def test_divergencia_com_o_schema_preservado_e_registrada(pipeline, recurso, area, monkeypatch):
    semear_schema_do_cliente(recurso)
    com_campo_a_mais = {
        "type": "object",
        "properties": {"situacao": {"type": "string"}, "total": {"type": "number"}},
    }
    monkeypatch.setattr(
        pipeline, "modelo", modelo_por_estagio(artefato_com_schema(com_campo_a_mais))
    )
    monkeypatch.setattr(gate_a, "executar", lambda *_a, **_k: ResultadoGate.aprovado_por())

    pipeline.persistencia.divergencias = []
    pipeline.bloco1(recurso, area)

    assert [d.campos_ausentes for d in pipeline.persistencia.divergencias] == [["total"]], (
        "campo achado no backend e ausente do schema do cliente sai do denominador "
        "sem deixar rastro — precisa virar diff, não só aviso"
    )


def test_schema_desta_execucao_e_reescrito_no_reparo(pipeline, recurso, area, monkeypatch):
    # Sem isto o loop do Gate A não converge: o mapeador corrigiria o schema e a
    # correção seria descartada por parecer arquivo alheio.
    corrigido = {
        "type": "object",
        "properties": {"situacao": {"type": "string"}, "total": {"type": "number"}},
    }

    def modelo(estagio: str, recurso_nome: str, tentativa: int) -> ModeloSimulado:
        esquema = corrigido if tentativa > 1 else SCHEMA
        return modelo_por_estagio(artefato_com_schema(esquema))(estagio, recurso_nome, tentativa)

    vereditos = iter(
        [
            ResultadoGate.reprovado_por([Violacao(codigo="QAAPI-021", mensagem="cat faltando")]),
            ResultadoGate.aprovado_por(),
        ]
    )
    monkeypatch.setattr(pipeline, "modelo", modelo)
    monkeypatch.setattr(gate_a, "executar", lambda *_a, **_k: next(vereditos))

    _saida, _resultado, tentativas = pipeline.bloco1(recurso, area)

    assert tentativas == 2
    assert json.loads(staged(area).read_text(encoding="utf-8")) == corrigido


# ---------------------------------------------------------------------------
# Varredura de campos
# ---------------------------------------------------------------------------


def test_nomes_de_campos_atravessa_o_envelope_da_resposta():
    # O schema do consumidor costuma validar {code, entity}, com a entidade aninhada.
    envelope = {
        "type": "object",
        "properties": {
            "code": {"type": "integer"},
            "entity": {"type": "object", "properties": {"sku": {"type": "string"}}},
        },
    }
    assert nomes_de_campos(envelope) == {"code", "entity", "sku"}


def test_nomes_de_campos_desce_em_lista():
    lista = {"type": "array", "items": {"properties": {"sku": {"type": "string"}}}}
    assert nomes_de_campos(lista) == {"sku"}


def test_nomes_de_campos_tolera_schema_sem_properties():
    assert nomes_de_campos({"type": "string"}) == set()
    assert nomes_de_campos("nem é objeto") == set()
