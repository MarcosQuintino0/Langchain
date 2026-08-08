"""A1 e A3 — falha de um recurso não derruba a execução, e não deixa lixo mudo.

A1: `GraphRecursionError` herda de `RecursionError`. Sem conversão explícita ela
passa por cima dos `except` de `pipeline.py` e mata a execução inteira com
traceback, em vez de falhar o recurso e seguir para o próximo.

A3: o artefato reprovado na última tentativa **fica em disco** de propósito — é o
que se inspeciona para entender a falha, e apagar arquivo do usuário é pior que
deixar. O que não pode é o efeito ser silencioso.

A terceira seção é o oposto da falha isolada: as formas que este programa tinha de
terminar **bem** sem ter feito o trabalho — o Bloco 0 falhando aberto, o Cypress
que não roda e mesmo assim vira sucesso, as duas flags que anunciavam um efeito
que não entregavam.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from orquestrador import cli as modulo_cli
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.cli import avisar_reprovados
from orquestrador.contratos import EstadoDoRecurso, Recurso, ResultadoGate, Violacao
from orquestrador.excecoes import (
    ErroDeConfiguracao,
    ErroDeFerramenta,
    FalhaDaExecucaoDeTestes,
    FalhaDeEstagio,
    FalhaDeGate,
    GrafoNaoPreparado,
)
from orquestrador.ferramentas import processo
from orquestrador.ferramentas.graphify import ResultadoPreparacao
from orquestrador.ferramentas.processo import VARIAVEIS_DO_CYPRESS, SaidaProcesso
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.pipeline import (
    EXECUTADO,
    InterrupcaoDaExecucao,
    Pipeline,
    ResultadoDaExecucaoDeTestes,
    ResultadoDoRecurso,
)
from orquestrador.simulacao import ModeloSimulado, PassoDeTool, PassoDoRoteiro

# Roteiro patológico: o modelo só sabe pedir tool, nunca conclui. É o que um
# recurso grande demais provoca no mundo real.
SEM_FIM: list[PassoDoRoteiro] = [
    PassoDeTool(tipo="tool", nome="listar_diretorio", argumentos={"caminho": "."})
]


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


def preparacao(*, ok: bool = True) -> ResultadoPreparacao:
    """Veredito do Bloco 0 sem tocar no Graphify."""
    return ResultadoPreparacao(ok=ok, regenerou=False, graph=Path("graph.json"), detalhe="fixture")


def execucao_de_testes_falsa() -> ResultadoDaExecucaoDeTestes:
    return ResultadoDaExecucaoDeTestes(estado=EXECUTADO, contadores={})


def recurso_de(config, nome: str = "pedidos") -> Recurso:
    caminho = config.caminhos.recurso(nome)
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(nome=nome, caminho_testes=caminho, raiz_schemas=config.caminhos.dir_schemas_abs)


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

    def bloco1_falso(recurso: Recurso, _area):
        processados.append(recurso.nome)
        if recurso.nome == "explode":
            raise FalhaDeEstagio("estourou o limite de passos", arquivos=[])
        return _saida_qualquer(), ResultadoGate.aprovado_por(), 1

    monkeypatch.setattr(pipeline, "bloco0", lambda: preparacao(ok=True))
    monkeypatch.setattr(pipeline, "bloco1", bloco1_falso)
    monkeypatch.setattr(
        pipeline, "bloco2", lambda *_a, **_k: (None, ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(pipeline, "_publicar", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "bloco3", lambda _recurso: execucao_de_testes_falsa())

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


def test_ferramenta_indisponivel_interrompe_sem_perder_o_que_terminou(
    pipeline: Pipeline, monkeypatch
):
    # Falha de ferramenta NÃO é isolada como falha de recurso: o script que não
    # rodou aqui não vai rodar no próximo, e insistir queima token repetindo a mesma
    # falha. Mas ela também não pode subir crua — levaria junto o resultado dos
    # recursos que já tinham terminado.
    processados: list[str] = []

    def bloco1_falso(recurso: Recurso, _area):
        processados.append(recurso.nome)
        if recurso.nome == "quebra":
            raise ErroDeFerramenta("gate_b não pôde emitir veredito: saída não-JSON")
        return _saida_qualquer(), ResultadoGate.aprovado_por(), 1

    monkeypatch.setattr(pipeline, "bloco0", lambda: preparacao(ok=True))
    monkeypatch.setattr(pipeline, "bloco1", bloco1_falso)
    monkeypatch.setattr(
        pipeline, "bloco2", lambda *_a, **_k: (None, ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(pipeline, "_publicar", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "bloco3", lambda _r: execucao_de_testes_falsa())

    resultados = pipeline.rodar(
        [
            recurso_de(pipeline.config, "primeiro"),
            recurso_de(pipeline.config, "quebra"),
            recurso_de(pipeline.config, "terceiro"),
        ]
    )

    # O terceiro nem foi tentado...
    assert processados == ["primeiro", "quebra"]
    # ...o primeiro não se perdeu...
    assert [(r.recurso, r.sucesso) for r in resultados] == [("primeiro", True)]
    # ...e a interrupção diz onde parou e o que ficou sem rodar.
    assert pipeline.interrupcao is not None
    assert pipeline.interrupcao.recurso == "quebra"
    assert pipeline.interrupcao.recursos_nao_executados == ["terceiro"]
    assert "não pôde emitir veredito" in pipeline.interrupcao.motivo


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
            avaliar=lambda _artefato: ResultadoGate.reprovado_por(
                [Violacao(codigo="QAAPI-025", mensagem="campo sem teste")],
            ),
            texto_do_artefato=lambda artefato, _delta: str(artefato),
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

    def bloco1_falso(recurso: Recurso, _area):
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
        arquivos_reprovados=[Path("C:/projeto/pedidos/_support/cobertura.json")],
        codigos_remanescentes=["QAAPI-021", "QAAPI-025"],
    )

    avisar_reprovados([resultado], registro)
    registro.console.file.close()
    texto = saida.read_text(encoding="utf-8")

    assert "REPROVADO" in texto
    assert "cobertura.json" in texto
    assert "QAAPI-021" in texto
    # Com o diário de propriedade a flag voltou, e o aviso volta a apontá-la —
    # restrita ao que criamos. Ver tests/test_publicacao.py.
    assert "--remover-reprovados" in texto


def test_aviso_final_cala_quando_nao_ha_lixo(tmp_path: Path):
    saida = tmp_path / "console.txt"
    registro = Registro(tmp_path / "log.jsonl", console_de_arquivo(saida))
    avisar_reprovados(
        [ResultadoDoRecurso(recurso="pedidos", estado=EstadoDoRecurso.APROVADO)], registro
    )
    registro.console.file.close()
    assert saida.read_text(encoding="utf-8").strip() == ""


def test_o_aviso_sozinho_nao_apaga_nada(tmp_path: Path):
    alvo = tmp_path / "cobertura.json"
    alvo.write_text("{}", encoding="utf-8")
    registro = Registro(tmp_path / "log.jsonl", console_de_arquivo(tmp_path / "c.txt"))

    avisar_reprovados(
        [ResultadoDoRecurso(recurso="pedidos", arquivos_reprovados=[alvo])],
        registro,
    )

    assert alvo.is_file()


# ---------------------------------------------------------------------------
# O Bloco 0 falha fechado
# ---------------------------------------------------------------------------


def test_bloco0_reprovado_interrompe_antes_de_qualquer_modelo(pipeline: Pipeline, monkeypatch):
    # O grafo inválido não produz erro adiante: produz um mapeador explorando um
    # mapa errado. Por isso o teste não checa a mensagem — checa que nenhum modelo
    # chegou a ser pedido.
    monkeypatch.setattr(pipeline, "bloco0", lambda: preparacao(ok=False))
    monkeypatch.setattr(
        pipeline, "modelo", lambda *_a, **_k: pytest.fail("nenhum modelo pode ser criado")
    )
    monkeypatch.setattr(pipeline, "bloco1", lambda *_a: pytest.fail("o Bloco 1 não pode começar"))

    with pytest.raises(GrafoNaoPreparado) as erro:
        pipeline.rodar([recurso_de(pipeline.config)])

    assert "graph.json" in str(erro.value)


def test_bloco0_aprovado_segue_para_os_recursos(pipeline: Pipeline, monkeypatch):
    # O contrapeso do teste acima: o veredito é lido, não ignorado nos dois sentidos.
    monkeypatch.setattr(pipeline, "bloco0", lambda: preparacao(ok=True))
    monkeypatch.setattr(
        pipeline, "bloco1", lambda *_a: (_saida_qualquer(), ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(
        pipeline, "bloco2", lambda *_a, **_k: (None, ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(pipeline, "_publicar", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "bloco3", lambda _r: execucao_de_testes_falsa())

    assert [r.sucesso for r in pipeline.rodar([recurso_de(pipeline.config)])] == [True]


# ---------------------------------------------------------------------------
# O Bloco 3 não pode chamar de aprovado o que não rodou
# ---------------------------------------------------------------------------


def cypress_falso(pipeline: Pipeline, monkeypatch, *, codigo: int, escreve: bool):
    """Substitui o subprocesso do Cypress e o `qa-cobertura.mjs`.

    Devolve um espião com `relatorios` — o que o `qa-cobertura.mjs` recebeu, e é aí
    que se lê se o Bloco 3 mandou adiante um relatório desta execução, de outra ou
    nenhum — e `invocacoes`, os argumentos nomeados de cada subprocesso.
    """
    recebidos: list[Path | None] = []
    invocacoes: list[dict] = []

    def rodar(argv, **kwargs):
        invocacoes.append(kwargs)
        if escreve:
            # O relatório só aparece porque ESTE processo o escreveu.
            destino = Path(argv[-1])
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text('{"stats": {}}', encoding="utf-8")
        return SaidaProcesso(argv=argv, codigo=codigo, stdout="", stderr="", duracao_s=0.0)

    class CoberturaFalsa:
        def __init__(self, _config):
            pass

        def executar(self, _dir, *, report=None, out=None):
            recebidos.append(report)
            return SaidaProcesso(
                argv=["node"], codigo=0, stdout='{"lacunas": 0}', stderr="", duracao_s=0.0
            )

    monkeypatch.setattr(processo, "executar", rodar)
    monkeypatch.setattr("orquestrador.pipeline.Cobertura", CoberturaFalsa)
    pipeline.pular_cypress = False
    pipeline.config.execucao.cypress = ["cypress", "run", "{relatorio}"]
    return SimpleNamespace(relatorios=recebidos, invocacoes=invocacoes)


def test_cypress_com_codigo_diferente_de_zero_reprova_o_recurso(pipeline: Pipeline, monkeypatch):
    espiao = cypress_falso(pipeline, monkeypatch, codigo=1, escreve=True)

    with pytest.raises(FalhaDaExecucaoDeTestes) as erro:
        pipeline.bloco3(recurso_de(pipeline.config))

    assert "código 1" in str(erro.value)
    # E o relatório de cobertura nem chega a ser gerado a partir de uma suíte que
    # não passou.
    assert espiao.relatorios == []


def test_relatorio_de_outra_execucao_nao_e_aceito(pipeline: Pipeline, monkeypatch):
    # O Cypress sai 0 sem escrever nada — era exatamente assim que o report.json de
    # ontem passava por evidência de hoje.
    cypress_falso(pipeline, monkeypatch, codigo=0, escreve=False)
    velho = pipeline.dir_execucao / "cypress" / "pedidos" / "report.json"
    velho.parent.mkdir(parents=True, exist_ok=True)
    velho.write_text('{"stats": "de outra execução"}', encoding="utf-8")

    with pytest.raises(FalhaDaExecucaoDeTestes, match="não deixou relatório"):
        pipeline.bloco3(recurso_de(pipeline.config))

    assert not velho.exists(), "o caminho é apagado antes de rodar, não depois de aceito"


def test_cypress_bem_sucedido_entrega_o_relatorio_desta_execucao(pipeline: Pipeline, monkeypatch):
    espiao = cypress_falso(pipeline, monkeypatch, codigo=0, escreve=True)

    resultado = pipeline.bloco3(recurso_de(pipeline.config))

    assert resultado.estado == "EXECUTADO"
    assert espiao.relatorios == [pipeline.dir_execucao / "cypress" / "pedidos" / "report.json"]
    assert resultado.contadores == {"lacunas": 0}


def test_o_cypress_recebe_as_variaveis_do_runner(pipeline: Pipeline, monkeypatch):
    # O ambiente do subprocesso é allowlist, e CYPRESS_*/CI ficam fora da base para
    # não vazarem aos gates. Se o Bloco 3 esquecer de pedi-las, o cypress.config.js
    # do consumidor sobe sem configuração e a suíte falha longe da causa.
    espiao = cypress_falso(pipeline, monkeypatch, codigo=0, escreve=True)

    pipeline.bloco3(recurso_de(pipeline.config))

    assert [i.get("variaveis_extras") for i in espiao.invocacoes] == [VARIAVEIS_DO_CYPRESS]


def test_comando_sem_a_marca_do_relatorio_e_erro_de_configuracao(pipeline: Pipeline, monkeypatch):
    cypress_falso(pipeline, monkeypatch, codigo=0, escreve=True)
    pipeline.config.execucao.cypress = ["cypress", "run"]

    with pytest.raises(ErroDeConfiguracao, match=r"\{relatorio\}"):
        pipeline.bloco3(recurso_de(pipeline.config))


def test_sem_cypress_o_resultado_diz_que_nao_executou(pipeline: Pipeline, monkeypatch):
    espiao = cypress_falso(pipeline, monkeypatch, codigo=0, escreve=True)
    pipeline.pular_cypress = True

    resultado = pipeline.bloco3(recurso_de(pipeline.config))

    assert resultado.estado == "NAO_EXECUTADO"
    assert resultado.motivo
    # Nenhum relatório: a cobertura que sai daqui é de forma, não de runtime.
    assert espiao.relatorios == [None]


def test_recurso_que_nao_rodou_cypress_nao_finge_ter_rodado(pipeline: Pipeline, monkeypatch):
    # O estado precisa sobreviver até o resumo do recurso, que é onde alguém lê.
    monkeypatch.setattr(
        pipeline, "bloco1", lambda *_a: (_saida_qualquer(), ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(
        pipeline, "bloco2", lambda *_a, **_k: (None, ResultadoGate.aprovado_por(), 1)
    )
    monkeypatch.setattr(pipeline, "_publicar", lambda *_a, **_k: None)
    cypress_falso(pipeline, monkeypatch, codigo=0, escreve=True)
    pipeline.pular_cypress = True

    resultado = pipeline._rodar_recurso(recurso_de(pipeline.config))

    assert resultado.sucesso is True
    assert resultado.execucao_de_testes == "NAO_EXECUTADO"


def test_o_padrao_do_resultado_nunca_e_executado():
    # Recurso que falha antes do Bloco 3 nunca passa por lá; o valor default é o
    # que vai para o log e para o resumo.
    assert ResultadoDoRecurso(recurso="pedidos").execucao_de_testes == "NAO_EXECUTADO"


# ---------------------------------------------------------------------------
# A flag que a CLI recusa
# ---------------------------------------------------------------------------


def test_a_flag_continua_reconhecida_pelo_argparse():
    # Recusar é diferente de sumir: script existente que passa a flag precisa
    # receber a mensagem, não um "unrecognized arguments" do argparse.
    assert modulo_cli.parse_args(["--remover-reprovados"]).remover_reprovados is True
    assert modulo_cli.parse_args(["--auditor"]).auditor is True


def config_de_dry_run(tmp_path: Path) -> Path:
    """config.toml mínimo que atravessa `validar_caminhos` sem precisar de Node.

    Os `.mjs` são arquivos vazios: a validação confere existência, e nada nesta
    seção chega a invocá-los.
    """
    scripts = tmp_path / "skill" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for nome in ("validar-suite-gerada.mjs", "qa-cobertura.mjs", "qa-reindex.mjs"):
        (scripts / nome).write_text("", encoding="utf-8")
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(
        f"""
[caminhos]
skill = {str(tmp_path / "skill")!r}
backend = {str(tmp_path / "backend")!r}
projeto_testes = {str(tmp_path / "projeto")!r}
saida = {str(tmp_path / "execucoes")!r}

[estagios.mapeador]
modelo = "<dry-run não chama modelo>"
[estagios.executor]
modelo = "<dry-run não chama modelo>"

[gates.a]
max_tentativas = 3
[gates.b]
max_tentativas = 3
""",
        encoding="utf-8",
    )
    return arquivo


def test_ferramenta_indisponivel_nao_apaga_o_resumo_do_que_terminou(
    tmp_path: Path, capsys, monkeypatch
):
    # A contraparte na CLI: os recursos concluídos aparecem, os que não rodaram são
    # anunciados, e o código é 2 — distinto do 1 de gate esgotado, porque aqui o
    # pipeline não chegou a emitir veredito nenhum.
    class PipelineInterrompido(Pipeline):
        def rodar(self, recursos: list[Recurso]) -> list[ResultadoDoRecurso]:
            self.interrupcao = InterrupcaoDaExecucao(
                motivo="gate_b não pôde emitir veredito: saída não-JSON",
                recurso="segundo",
                recursos_nao_executados=["terceiro"],
            )
            return [ResultadoDoRecurso(recurso="primeiro", estado=EstadoDoRecurso.APROVADO)]

    monkeypatch.setattr(modulo_cli, "Pipeline", PipelineInterrompido)

    codigo = modulo_cli.main(
        [
            "--dry-run",
            "--config",
            str(config_de_dry_run(tmp_path)),
            "--recurso",
            "primeiro",
            "--recurso",
            "segundo",
            "--recurso",
            "terceiro",
        ]
    )

    saida = capsys.readouterr().out
    assert codigo == 2
    assert "OK primeiro" in saida
    assert "INTERROMPIDA" in saida
    assert "não chegaram a rodar: terceiro" in saida


def test_auditor_nao_encerra_com_sucesso(capsys):
    # O comando imprimia a descrição do stub e saía com 0. Em CI, 0 é
    # indistinguível de auditoria feita.
    codigo = modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--auditor"])

    assert codigo != 0
    saida = capsys.readouterr().out
    assert "indisponível" in saida
    # Nenhum veredito, nem mesmo vazio.
    assert "íntegro" not in saida
    assert "revisar" not in saida
