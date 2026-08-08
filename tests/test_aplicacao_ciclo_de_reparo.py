"""Loop de reparo por delta — o coração da arquitetura.

Gera → persiste → avalia → (delta → repete), com limite de tentativas. Os testes
usam produtores e gates falsos: o que está sob teste é o laço, não os scripts.

Desde que o laço saiu do `Pipeline`, quase todos constroem só o `CicloDeReparo` —
sem staging, sem Cypress, sem diário. O último constrói o pipeline inteiro de
propósito: o que ele prova é a **ligação**, que o limite vindo da CLI chega até
aqui.

A última seção guarda o número que dá corda no laço. `max_tentativas` é a única
configuração que decide **quantas vezes** um estágio roda: em zero o loop não
executa nenhuma tentativa e o gate reprova sem nunca ter avaliado nada, e a CLI
tinha um caminho que aceitava esse zero em silêncio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orquestrador import cli as modulo_cli
from orquestrador.aplicacao.ciclo_de_reparo import CicloDeReparo
from orquestrador.aplicacao.pipeline import Pipeline
from orquestrador.config import ConfigGate
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, ResultadoGate, Violacao
from orquestrador.excecoes import ErroDeConfiguracao, FalhaDeGate
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


@pytest.fixture
def ciclo(config_falso, tmp_path: Path) -> CicloDeReparo:
    registro = Registro(tmp_path / "execucao.jsonl")
    return CicloDeReparo(config_falso, registro, Telemetria(registro))


def recurso_de(config) -> Recurso:
    return Recurso(
        nome="pedidos",
        caminho_testes=config.caminhos.recurso("pedidos"),
        raiz_schemas=config.caminhos.dir_schemas_abs,
    )


def reprovado(*codigos: str) -> ResultadoGate:
    return ResultadoGate.reprovado_por(
        [Violacao(codigo=codigo, mensagem="detalhe") for codigo in codigos]
    )


def test_aprova_de_primeira_nao_monta_delta(ciclo: CicloDeReparo, config_falso):
    recebidos: list[Delta | None] = []

    artefato, resultado, tentativas = ciclo.executar(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(config_falso),
        produzir=lambda numero, delta, atual: recebidos.append(delta) or "artefato",
        persistir=lambda _artefato: [],
        avaliar=lambda _artefato: ResultadoGate.aprovado_por(),
        texto_do_artefato=lambda artefato, _delta: str(artefato),
    )

    assert (artefato, tentativas, resultado.aprovado) == ("artefato", 1, True)
    assert recebidos == [None]


def test_reprova_uma_vez_e_repara_com_o_delta(ciclo: CicloDeReparo, config_falso):
    recebidos: list[tuple[Delta | None, str | None]] = []
    vereditos = [reprovado("QAAPI-021", "QAAPI-022"), ResultadoGate.aprovado_por()]

    def produzir(numero: int, delta: Delta | None, atual: str | None) -> str:
        recebidos.append((delta, atual))
        return f"artefato-{numero}"

    _artefato, _resultado, tentativas = ciclo.executar(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(config_falso),
        produzir=produzir,
        persistir=lambda _artefato: [],
        avaliar=lambda _artefato: vereditos.pop(0),
        texto_do_artefato=lambda artefato, _delta: f"texto de {artefato}",
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


def test_esgotar_as_tentativas_falha_com_os_codigos_remanescentes(
    ciclo: CicloDeReparo, config_falso
):
    # gate "b" está configurado com max_tentativas = 2 no config_falso.
    with pytest.raises(FalhaDeGate) as erro:
        ciclo.executar(
            estagio="executor",
            gate="b",
            recurso=recurso_de(config_falso),
            produzir=lambda numero, delta, atual: "artefato",
            persistir=lambda _artefato: [],
            avaliar=lambda _artefato: reprovado("QAAPI-025"),
            texto_do_artefato=lambda artefato, _delta: str(artefato),
        )
    assert "2 tentativa(s)" in str(erro.value)
    assert "QAAPI-025" in str(erro.value)


def test_o_artefato_e_persistido_antes_de_ser_avaliado(ciclo: CicloDeReparo, config_falso):
    # Princípio 1: o gate lê o disco, não o objeto em memória.
    ordem: list[str] = []
    ciclo.executar(
        estagio="executor",
        gate="b",
        recurso=recurso_de(config_falso),
        produzir=lambda numero, delta, atual: ordem.append("produzir") or "a",
        persistir=lambda _artefato: ordem.append("persistir") or [],
        avaliar=lambda _artefato: ordem.append("avaliar") or ResultadoGate.aprovado_por(),
        texto_do_artefato=lambda artefato, _delta: str(artefato),
    )
    assert ordem == ["produzir", "persistir", "avaliar"]


def test_cada_tentativa_recebe_apenas_o_delta_mais_recente(ciclo: CicloDeReparo, config_falso):
    deltas: list[Delta | None] = []
    vereditos = [reprovado("QAAPI-021"), reprovado("QAAPI-022"), ResultadoGate.aprovado_por()]

    ciclo.executar(
        estagio="mapeador",
        gate="a",
        recurso=recurso_de(config_falso),
        produzir=lambda numero, delta, atual: deltas.append(delta) or "a",
        persistir=lambda _artefato: [],
        avaliar=lambda _artefato: vereditos.pop(0),
        texto_do_artefato=lambda artefato, _delta: str(artefato),
    )

    reparos = [delta for delta in deltas[1:] if delta is not None]
    assert [delta.violacoes[0].codigo for delta in reparos] == ["QAAPI-021", "QAAPI-022"]
    # A terceira tentativa NÃO acumula as violações da primeira.
    assert len(reparos[1].violacoes) == 1


# ---------------------------------------------------------------------------
# O limite que dá corda no laço
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalido", [0, -1, 21])
def test_max_tentativas_fora_da_faixa_nao_constroi_o_gate(invalido: int):
    with pytest.raises(ValidationError):
        ConfigGate(max_tentativas=invalido)


def test_o_limite_nao_pode_ser_invalidado_depois_da_carga():
    # `validate_assignment`: o modelo já construído continua sendo validado. Era por
    # aqui que a CLI entrava, escrevendo direto no gate.
    gate = ConfigGate()
    with pytest.raises(ValidationError):
        gate.max_tentativas = 0
    assert gate.max_tentativas == 3


def test_com_max_tentativas_revalida_e_nao_muta_o_original(config_falso):
    novo = config_falso.com_max_tentativas(5)

    assert [gate.max_tentativas for gate in novo.gates.values()] == [5, 5]
    # A cópia é cópia: o original segue com o que veio do arquivo (3 e 2).
    assert [gate.max_tentativas for gate in config_falso.gates.values()] == [3, 2]
    # E o resto da configuração atravessa intacto.
    assert novo.gates["a"].flags == ["--so-manifesto"]
    assert novo.caminhos == config_falso.caminhos

    with pytest.raises(ErroDeConfiguracao):
        config_falso.com_max_tentativas(0)


@pytest.mark.parametrize("texto", ["0", "-1", "3.5", "tres"])
def test_a_cli_recusa_max_tentativas_que_nao_e_inteiro_positivo(texto: str):
    # `--max-tentativas 0` era aceito pelo parser e depois descartado por um `if` de
    # truthiness: o pipeline rodava com o limite do arquivo, diferente do pedido.
    with pytest.raises(SystemExit) as saida:
        modulo_cli.parse_args(["--max-tentativas", texto])
    assert saida.value.code != 0


def test_o_limite_da_cli_chega_ao_ciclo(config_falso, tmp_path: Path):
    config = config_falso.com_max_tentativas(
        modulo_cli.parse_args(["--max-tentativas", "1"]).max_tentativas
    )
    pipeline = Pipeline(
        config,
        Registro(tmp_path / "execucao.jsonl"),
        dry_run=True,
        roteiros=None,
        dir_execucao=tmp_path / "execucao",
    )

    with pytest.raises(FalhaDeGate, match=r"1 tentativa\(s\)"):
        pipeline.ciclo.executar(
            estagio="executor",
            gate="b",
            recurso=recurso_de(pipeline.config),
            produzir=lambda numero, delta, atual: "artefato",
            persistir=lambda _artefato: [],
            avaliar=lambda _artefato: reprovado("QAAPI-025"),
            texto_do_artefato=lambda artefato, _delta: str(artefato),
        )
