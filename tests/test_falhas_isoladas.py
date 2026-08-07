"""A1 e A3 — falha de um recurso não derruba a execução, e não deixa lixo mudo.

A1: `GraphRecursionError` herda de `RecursionError`. Sem conversão explícita ela
passa por cima dos `except` de `pipeline.py` e mata a execução inteira com
traceback, em vez de falhar o recurso e seguir para o próximo.

A3: o artefato reprovado na última tentativa **fica em disco** de propósito — é o
que se inspeciona para entender a falha, e apagar arquivo do usuário é pior que
deixar. O que não pode é o efeito ser silencioso.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.contratos import Recurso, ResultadoGate, Violacao
from orquestrador.excecoes import FalhaDeEstagio, FalhaDeGate
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.cli import avisar_reprovados, remover_reprovados
from orquestrador.pipeline import Pipeline, ResultadoDoRecurso
from orquestrador.observabilidade.registro import Registro
from orquestrador.simulacao import ModeloSimulado

# Roteiro patológico: o modelo só sabe pedir tool, nunca conclui. É o que um
# recurso grande demais provoca no mundo real.
SEM_FIM = [{"tipo": "tool", "nome": "listar_diretorio", "argumentos": {"caminho": "."}}]


def console_de_arquivo(destino: Path, largura: int = 200) -> Console:
    """Console que escreve num arquivo UTF-8.

    Sem `encoding` explícito o arquivo herda a codepage do Windows (cp1252) e um
    simples "✓" derruba o teste — o mesmo motivo de `registro.configurar_console`.
    """
    return Console(file=destino.open("w", encoding="utf-8"), width=largura)


@pytest.fixture
def pipeline(config_falso, tmp_path: Path) -> Pipeline:
    return Pipeline(
        config_falso,
        Registro(tmp_path / "execucao.jsonl", console_de_arquivo(tmp_path / "saida.txt")),
        dry_run=True,
        roteiros=None,
        dir_execucao=tmp_path / "execucao",
    )


def recurso_de(config, nome: str = "pedidos") -> Recurso:
    caminho = config.caminhos.recurso(nome)
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome=nome, caminho_testes=caminho)


# ---------------------------------------------------------------------------
# A1
# ---------------------------------------------------------------------------


def test_estouro_do_limite_de_passos_vira_falha_de_estagio(config_falso):
    config_falso.estagio("mapeador").limite_passos = 4

    with pytest.raises(FalhaDeEstagio) as erro:
        agente_mapeador.executar(
            config_falso,
            recurso_de(config_falso),
            modelo=ModeloSimulado(passos=SEM_FIM),
            telemetria=Telemetria(),
        )

    mensagem = str(erro.value)
    assert "limite de 4 passos" in mensagem
    assert "pedidos" in mensagem
    # A mensagem precisa apontar as duas saídas possíveis.
    assert "limite_passos" in mensagem
    assert "escopo" in mensagem


def test_falta_de_passos_nao_gasta_as_tentativas_de_schema(config_falso):
    # O prebuilt encerra com uma frase de desculpa em vez de levantar. Se o
    # pipeline não a reconhecer, ele tenta parsear essa frase como JSON uma vez
    # por tentativa de schema e falha com um motivo que esconde a causa real.
    config_falso.estagio("mapeador").limite_passos = 4
    config_falso.estagio("mapeador").max_tentativas_schema = 3
    telemetria = Telemetria()

    with pytest.raises(FalhaDeEstagio, match="limite de 4 passos"):
        agente_mapeador.executar(
            config_falso,
            recurso_de(config_falso),
            modelo=ModeloSimulado(passos=SEM_FIM),
            telemetria=telemetria,
        )

    assert len(telemetria.chamadas) == 1, "desistiu na primeira, sem reparo inútil"


def test_falha_de_estagio_e_capturavel_pelo_pipeline():
    # É o ponto todo da correção: RecursionError (mãe de GraphRecursionError) não
    # é capturada por _rodar_recurso; FalhaDeEstagio é.
    assert not issubclass(FalhaDeEstagio, RecursionError)
    assert issubclass(FalhaDeEstagio, RuntimeError)


def test_recurso_que_falha_nao_derruba_os_seguintes(pipeline: Pipeline, monkeypatch):
    processados: list[str] = []

    def bloco1_falso(recurso: Recurso):
        processados.append(recurso.nome)
        if recurso.nome == "explode":
            raise FalhaDeEstagio("estourou o limite de passos", arquivos=[])
        return _saida_qualquer(), ResultadoGate(aprovado=True), 1

    monkeypatch.setattr(pipeline, "bloco0", lambda: None)
    monkeypatch.setattr(pipeline, "bloco1", bloco1_falso)
    monkeypatch.setattr(
        pipeline, "bloco2", lambda *_a, **_k: (None, ResultadoGate(aprovado=True), 1)
    )
    monkeypatch.setattr(pipeline, "bloco3", lambda _recurso: {})

    resultados = pipeline.rodar(
        [recurso_de(pipeline.config, "explode"), recurso_de(pipeline.config, "seguinte")]
    )

    # O segundo recurso foi processado mesmo com o primeiro falhando...
    assert processados == ["explode", "seguinte"]
    # ...e o resumo reporta o primeiro como falha e o segundo como sucesso.
    assert [(r.recurso, r.sucesso) for r in resultados] == [
        ("explode", False),
        ("seguinte", True),
    ]
    assert "limite de passos" in resultados[0].motivo


def _saida_qualquer():
    """Objeto mínimo com o `.manifesto` que `_rodar_recurso` repassa ao bloco 2."""

    class Saida:
        manifesto = None

    return Saida()


# ---------------------------------------------------------------------------
# A3
# ---------------------------------------------------------------------------


def test_falha_de_gate_carrega_os_arquivos_persistidos(pipeline: Pipeline, tmp_path: Path):
    escrito = tmp_path / "cobertura.json"
    escrito.write_text("{}", encoding="utf-8")

    with pytest.raises(FalhaDeGate) as erro:
        pipeline._ciclo(
            estagio="executor",
            gate="b",
            recurso=recurso_de(pipeline.config),
            produzir=lambda numero, delta, atual: "artefato",
            persistir=lambda _artefato: [escrito],
            avaliar=lambda _artefato: ResultadoGate(
                aprovado=False,
                violacoes=[Violacao(codigo="QAAPI-025", mensagem="campo sem teste")],
            ),
            texto_do_artefato=str,
        )

    assert erro.value.arquivos == [escrito]
    assert erro.value.codigos == ["QAAPI-025"]
    # O arquivo continua em disco: apagar artefato do usuário é pior que deixar.
    assert escrito.is_file()


def test_resultado_do_recurso_registra_o_que_ficou_reprovado(
    pipeline: Pipeline, monkeypatch, tmp_path: Path
):
    escrito = tmp_path / "crud.cy.js"
    escrito.write_text("// reprovado", encoding="utf-8")

    def bloco1_falso(recurso: Recurso):
        raise FalhaDeGate(
            "gate_a reprovou",
            arquivos=[escrito],
            violacoes=[Violacao(codigo="QAAPI-021", mensagem="cat faltando")],
        )

    monkeypatch.setattr(pipeline, "bloco1", bloco1_falso)
    resultado = pipeline._rodar_recurso(recurso_de(pipeline.config))

    assert resultado.sucesso is False
    assert resultado.arquivos_reprovados == [escrito]
    assert resultado.codigos_remanescentes == ["QAAPI-021"]


def test_aviso_final_lista_arquivos_e_codigos(tmp_path: Path):
    saida = tmp_path / "console.txt"
    registro = Registro(tmp_path / "log.jsonl", console_de_arquivo(saida))
    resultado = ResultadoDoRecurso(
        recurso="pedidos",
        sucesso=False,
        arquivos_reprovados=[Path("C:/projeto/pedidos/_support/cobertura.json")],
        codigos_remanescentes=["QAAPI-021", "QAAPI-025"],
    )

    avisar_reprovados([resultado], registro)
    registro.console.file.close()
    texto = saida.read_text(encoding="utf-8")

    assert "REPROVADO" in texto
    assert "cobertura.json" in texto
    assert "QAAPI-021" in texto
    assert "--remover-reprovados" in texto


def test_aviso_final_cala_quando_nao_ha_lixo(tmp_path: Path):
    saida = tmp_path / "console.txt"
    registro = Registro(tmp_path / "log.jsonl", console_de_arquivo(saida))
    avisar_reprovados([ResultadoDoRecurso(recurso="pedidos", sucesso=True)], registro)
    registro.console.file.close()
    assert saida.read_text(encoding="utf-8").strip() == ""


def test_remover_reprovados_so_apaga_quando_pedido(tmp_path: Path):
    alvo = tmp_path / "cobertura.json"
    alvo.write_text("{}", encoding="utf-8")
    registro = Registro(tmp_path / "log.jsonl", console_de_arquivo(tmp_path / "c.txt"))
    resultado = ResultadoDoRecurso(
        recurso="pedidos", sucesso=False, arquivos_reprovados=[alvo]
    )

    avisar_reprovados([resultado], registro)
    assert alvo.is_file()  # o aviso, sozinho, não apaga nada

    remover_reprovados([resultado], registro)
    assert not alvo.exists()
