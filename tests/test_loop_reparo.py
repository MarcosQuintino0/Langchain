"""Loop de reparo por delta — o coração da arquitetura.

Gera → persiste → avalia → (delta → repete), com limite de tentativas. Os testes
usam produtores e gates falsos: o que está sob teste é o laço, não os scripts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.contratos import Delta, ResultadoGate, Violacao
from orquestrador.excecoes import FalhaDeGate
from orquestrador.pipeline import Pipeline
from orquestrador.observabilidade.registro import Registro


@pytest.fixture
def pipeline(config_falso, tmp_path: Path) -> Pipeline:
    registro = Registro(tmp_path / "execucao.jsonl")
    return Pipeline(
        config_falso,
        registro,
        dry_run=True,
        roteiros=None,
        dir_execucao=tmp_path / "execucao",
    )


def recurso_de(config) -> object:
    from orquestrador.contratos import Recurso

    return Recurso(nome="pedidos", caminho_testes=config.caminhos.recurso("pedidos"))


def reprovado(*codigos: str) -> ResultadoGate:
    return ResultadoGate(
        aprovado=False,
        violacoes=[Violacao(codigo=codigo, mensagem="detalhe") for codigo in codigos],
    )


def test_aprova_de_primeira_nao_monta_delta(pipeline: Pipeline):
    recebidos: list[Delta | None] = []

    artefato, resultado, tentativas = pipeline._ciclo(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(pipeline.config),
        produzir=lambda numero, delta, atual: recebidos.append(delta) or "artefato",
        persistir=lambda _artefato: None,
        avaliar=lambda _artefato: ResultadoGate(aprovado=True),
        texto_do_artefato=lambda artefato: str(artefato),
    )

    assert (artefato, tentativas, resultado.aprovado) == ("artefato", 1, True)
    assert recebidos == [None]


def test_reprova_uma_vez_e_repara_com_o_delta(pipeline: Pipeline):
    recebidos: list[tuple[Delta | None, str | None]] = []
    vereditos = [reprovado("QAAPI-021", "QAAPI-022"), ResultadoGate(aprovado=True)]

    def produzir(numero: int, delta: Delta | None, atual: str | None) -> str:
        recebidos.append((delta, atual))
        return f"artefato-{numero}"

    _artefato, _resultado, tentativas = pipeline._ciclo(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(pipeline.config),
        produzir=produzir,
        persistir=lambda _artefato: None,
        avaliar=lambda _artefato: vereditos.pop(0),
        texto_do_artefato=lambda artefato: f"texto de {artefato}",
    )

    assert tentativas == 2
    assert recebidos[0] == (None, None)

    delta, artefato_atual = recebidos[1]
    assert delta is not None
    assert delta.estagio == "gate_a"
    assert delta.recurso == "pedidos"
    assert delta.tentativa == 1
    assert [v.codigo for v in delta.violacoes] == ["QAAPI-021", "QAAPI-022"]
    # O reparo recebe o artefato reprovado, não o histórico da conversa anterior.
    assert artefato_atual == "texto de artefato-1"


def test_esgotar_as_tentativas_falha_com_os_codigos_remanescentes(pipeline: Pipeline):
    # gate "b" está configurado com max_tentativas = 2 no config_falso.
    with pytest.raises(FalhaDeGate) as erro:
        pipeline._ciclo(
            estagio="executor",
            gate="b",
            recurso=recurso_de(pipeline.config),
            produzir=lambda numero, delta, atual: "artefato",
            persistir=lambda _artefato: None,
            avaliar=lambda _artefato: reprovado("QAAPI-025"),
            texto_do_artefato=lambda artefato: str(artefato),
        )
    assert "2 tentativa(s)" in str(erro.value)
    assert "QAAPI-025" in str(erro.value)


def test_o_artefato_e_persistido_antes_de_ser_avaliado(pipeline: Pipeline):
    # Princípio 1: o gate lê o disco, não o objeto em memória.
    ordem: list[str] = []
    pipeline._ciclo(
        estagio="executor",
        gate="b",
        recurso=recurso_de(pipeline.config),
        produzir=lambda numero, delta, atual: ordem.append("produzir") or "a",
        persistir=lambda _artefato: ordem.append("persistir"),
        avaliar=lambda _artefato: (
            ordem.append("avaliar") or ResultadoGate(aprovado=True)
        ),
        texto_do_artefato=str,
    )
    assert ordem == ["produzir", "persistir", "avaliar"]


def test_cada_tentativa_recebe_apenas_o_delta_mais_recente(pipeline: Pipeline):
    deltas: list[Delta | None] = []
    vereditos = [reprovado("QAAPI-021"), reprovado("QAAPI-022"), ResultadoGate(aprovado=True)]

    pipeline._ciclo(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(pipeline.config),
        produzir=lambda numero, delta, atual: deltas.append(delta) or "a",
        persistir=lambda _artefato: None,
        avaliar=lambda _artefato: vereditos.pop(0),
        texto_do_artefato=str,
    )

    assert [d.violacoes[0].codigo for d in deltas[1:]] == ["QAAPI-021", "QAAPI-022"]
    # A terceira tentativa NÃO acumula as violações da primeira.
    assert len(deltas[2].violacoes) == 1
