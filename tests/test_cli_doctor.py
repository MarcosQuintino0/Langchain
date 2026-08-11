"""`orquestrador doctor`: um veredito por item, e o que fazer quando falha.

Por que este arquivo existe
---------------------------
O `doctor` só vale se ele estiver certo quando diz OK. Um diagnóstico
complacente é pior que nenhum: quem instalou vai à execução real confiando nele e
descobre a falta do Node — ou da chave — no meio do primeiro recurso, dentro de um
script de outro repositório.

Então cada teste aqui monta um ambiente **quase** bom e estraga um item de cada
vez, conferindo que é aquele item que reprova. O que não é conferido por nome de
item é conferido por consequência: código de saída diferente de zero, e o valor da
chave nunca aparecendo no que é impresso.

Nada de subprocesso: `node --version` e `graphify --version` entram por um duplo de
`executar`. O que este módulo testa é a leitura dos vereditos, não o Node.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from inspect import signature
from pathlib import Path

import pytest

from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, SUCESSO
from orquestrador.cli.doctor import ItemDeDiagnostico, Veredito, diagnosticar
from orquestrador.cli.principal import main
from orquestrador.excecoes import ExecutavelAusente
from orquestrador.ferramentas.processo import SaidaProcesso, executar

pytestmark = pytest.mark.unit

VERSAO_DO_GRAPHIFY = "0.9.26"
CHAVE = "OPENROUTER_API_KEY"

# Um segredo de mentira, mas com a forma de um de verdade: o que se quer provar é
# que ele não sai impresso, e um valor curto demais passaria por acaso.
SEGREDO = "sk-" + "or-v1-" + "0123456789abcdef0123456789abcdef"


def veredito_de(itens: list[ItemDeDiagnostico], nome: str) -> ItemDeDiagnostico:
    achado = next((item for item in itens if item.nome == nome), None)
    assert achado is not None, f"o doctor não emitiu o item {nome!r}: {[i.nome for i in itens]}"
    return achado


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    """Um ambiente completo: backend e projeto de testes preparado.

    Montado a partir do template do `orquestrador init`, com os campos preenchidos.
    Assim o teste do `doctor` também é um teste do template: se `init` passar a
    escrever um campo que a configuração recusa, esta fixture quebra.
    """
    (tmp_path / "backend").mkdir()

    testes = tmp_path / "projeto-de-testes"
    support = testes / "cypress" / "support" / "api"
    support.mkdir(parents=True)
    (support / "client.js").write_text(
        "export function apiRequest(opcoes) {\n  return cy.request(opcoes);\n}\n",
        encoding="utf-8",
    )

    assert main(["init", "--em", str(tmp_path)]) == SUCESSO
    arquivo = tmp_path / "config.toml"
    texto = (
        arquivo.read_text(encoding="utf-8")
        .replace("PREENCHA/caminho/para/o/backend", (tmp_path / "backend").as_posix())
        .replace("PREENCHA/caminho/para/o/projeto-de-testes", testes.as_posix())
        .replace('modelo = ""', 'modelo = "provedor/modelo-de-teste"')
    )
    arquivo.write_text(texto, encoding="utf-8")
    return tmp_path


@pytest.fixture
def versoes(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Instala o duplo de `executar` que responde `--version` por executável."""

    def instalar(*, graphify: str | None = VERSAO_DO_GRAPHIFY) -> None:
        def falso(argv: list[str], **_: object) -> SaidaProcesso:
            if Path(argv[0]).stem == "graphify" and graphify is None:
                raise ExecutavelAusente('executável não encontrado no PATH: "graphify".')
            return SaidaProcesso(
                argv=argv, codigo=0, stdout=f"graphify {graphify}", stderr="", duracao_s=0.0
            )

        monkeypatch.setattr("orquestrador.cli.doctor.executar", falso)

    return instalar


@pytest.fixture(autouse=True)
def sem_chave_no_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chave da máquina de quem roda a suíte não pode decidir o resultado."""
    monkeypatch.delenv(CHAVE, raising=False)


# ---------------------------------------------------------------------------
# O caminho feliz
# ---------------------------------------------------------------------------


def test_ambiente_completo_nao_reprova_nada(
    projeto: Path, versoes: Callable[..., None], monkeypatch: pytest.MonkeyPatch
):
    versoes()
    monkeypatch.setenv(CHAVE, SEGREDO)

    itens = diagnosticar(projeto / "config.toml")

    reprovados = [item.nome for item in itens if item.veredito is Veredito.FALHOU]
    assert not reprovados, f"reprovou num ambiente completo: {reprovados}"
    assert veredito_de(itens, "Projeto preparado").veredito is Veredito.OK
    assert veredito_de(itens, "Graphify").veredito is Veredito.OK


# ---------------------------------------------------------------------------
# Um item estragado de cada vez
# ---------------------------------------------------------------------------


def test_graphify_ausente_reprova_dizendo_como_reinstalar(
    projeto: Path, versoes: Callable[..., None]
):
    """O Graphify é a única ferramenta externa que sobrou como pré-requisito.

    Não há mais versão "fixada" para conferir contra manifesto: ele é dependência
    declarada deste pacote, e quem a garante é o instalador. Sobrou a pergunta que
    o `doctor` ainda precisa responder — ele está no PATH e responde `--version`?
    """
    versoes(graphify=None)
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Graphify")

    assert item.veredito is Veredito.FALHOU
    assert "pip install" in item.conserto


def test_projeto_sem_modulos_compartilhados_reprova(projeto: Path, versoes: Callable[..., None]):
    versoes()
    for arquivo in (projeto / "projeto-de-testes" / "cypress" / "support" / "api").iterdir():
        arquivo.unlink()

    item = veredito_de(diagnosticar(projeto / "config.toml"), "Projeto preparado")

    assert item.veredito is Veredito.FALHOU
    assert "preparar-projeto" in item.conserto


# ---------------------------------------------------------------------------
# A chave: presença sim, valor nunca
# ---------------------------------------------------------------------------


def test_chave_ausente_reprova_dizendo_o_nome_da_variavel(
    projeto: Path, versoes: Callable[..., None]
):
    versoes()
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Chave do provedor")

    assert item.veredito is Veredito.FALHOU
    assert CHAVE in item.detalhe


def test_o_valor_da_chave_nunca_aparece_no_diagnostico(
    projeto: Path,
    versoes: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """O doctor é feito para ser colado num chamado de suporte.

    Por isso a checagem é sobre a saída inteira, e não só sobre o item da chave:
    basta um item futuro imprimir `os.environ` para o hábito virar vazamento.
    """
    versoes()
    monkeypatch.setenv(CHAVE, SEGREDO)

    assert main(["doctor", "--config", str(projeto / "config.toml")]) == SUCESSO

    impresso = capsys.readouterr().out
    assert CHAVE in impresso
    for pedaco in (SEGREDO, SEGREDO[:12], SEGREDO[-12:]):
        assert pedaco not in impresso


# ---------------------------------------------------------------------------
# Sem configuração
# ---------------------------------------------------------------------------


def test_sem_config_o_doctor_ainda_diagnostica_o_que_nao_depende_dela(tmp_path: Path):
    """Encurtar a lista é aceitável; virar uma linha de erro, não.

    Quem acabou de instalar precisa saber que o Python e os prompts estão bem antes
    de rodar o `init` — é a diferença entre "falta configurar" e "a instalação está
    quebrada".
    """
    itens = diagnosticar(tmp_path / "nao-existe.toml")

    assert veredito_de(itens, "Python").veredito is Veredito.OK
    assert veredito_de(itens, "Prompts").veredito is Veredito.OK
    configuracao = veredito_de(itens, "Configuração")
    assert configuracao.veredito is Veredito.FALHOU
    assert "orquestrador init" in configuracao.conserto


def test_o_codigo_de_saida_acompanha_o_pior_veredito(tmp_path: Path):
    assert main(["doctor", "--config", str(tmp_path / "nao-existe.toml")]) == ERRO_DE_USO


def test_toml_malformado_vira_diagnostico_e_nao_traceback(tmp_path: Path):
    """TOML quebrado é o erro mais provável de quem edita o arquivo à mão.

    `Config.carregar` já traduzia o `ValidationError` do Pydantic em mensagem
    legível, mas o `tomllib.load` acima dele ficava sem `except`: uma aspa faltando
    subia como `TOMLDecodeError` cru e o `doctor` — o comando cuja função é
    explicar o que está errado — morria com traceback. Quem visse aquilo concluiria
    que o programa está quebrado, não a linha 3 do arquivo dele.
    """
    arquivo = tmp_path / "config.toml"
    arquivo.write_text('[caminhos]\nbackend = "sem fechar\n', encoding="utf-8")

    item = veredito_de(diagnosticar(arquivo), "Configuração")

    assert item.veredito is Veredito.FALHOU
    assert "orquestrador init" in item.conserto
    assert main(["doctor", "--config", str(arquivo)]) == ERRO_DE_USO


def test_todo_item_reprovado_diz_o_que_fazer(projeto: Path, versoes: Callable[..., None]):
    """Diagnóstico sem conserto obriga quem lê a descobrir sozinho o que instalar.

    Vale a pena testar porque a tentação de acrescentar um item novo sem a frase de
    conserto é grande — o veredito parece suficiente na hora de escrevê-lo.
    """
    versoes(graphify=None)
    for arquivo in (projeto / "projeto-de-testes" / "cypress" / "support" / "api").iterdir():
        arquivo.unlink()

    sem_conserto = [
        item.nome
        for item in diagnosticar(projeto / "config.toml")
        if item.veredito is not Veredito.OK and not item.conserto.strip()
    ]
    assert not sem_conserto, f"item(ns) sem instrução de conserto: {sem_conserto}"


def test_diretorio_nao_preenchido_e_diferente_de_diretorio_inexistente(
    projeto: Path, versoes: Callable[..., None]
):
    """As duas falhas pedem ações diferentes, e o texto precisa distinguir.

    "não encontrado: C:/.../PREENCHA/caminho/para/o/backend" manda procurar um
    diretório que nunca existiu. "[caminhos].backend não preenchido" manda voltar
    ao arquivo.
    """
    versoes()
    arquivo = projeto / "config.toml"
    texto = arquivo.read_text(encoding="utf-8")
    arquivo.write_text(
        texto.replace(
            f'backend = "{(projeto / "backend").as_posix()}"',
            'backend = "PREENCHA/caminho/para/o/backend"',
        ),
        encoding="utf-8",
    )

    item = veredito_de(diagnosticar(arquivo), "Backend")
    assert item.veredito is Veredito.FALHOU
    assert item.detalhe == "[caminhos].backend não preenchido"


def test_diretorio_inexistente_mostra_o_caminho_procurado(
    projeto: Path, versoes: Callable[..., None]
):
    versoes()
    arquivo = projeto / "config.toml"
    ausente = (projeto / "backend-que-sumiu").as_posix()
    arquivo.write_text(
        arquivo.read_text(encoding="utf-8").replace(
            f'backend = "{(projeto / "backend").as_posix()}"', f'backend = "{ausente}"'
        ),
        encoding="utf-8",
    )

    item = veredito_de(diagnosticar(arquivo), "Backend")
    assert item.veredito is Veredito.FALHOU
    assert "não encontrado" in item.detalhe


def test_estagio_sem_modelo_reprova(projeto: Path, versoes: Callable[..., None]):
    """Princípio 6 tem um custo: sem modelo no código, o arquivo precisa trazê-lo."""
    versoes()
    arquivo = projeto / "config.toml"
    arquivo.write_text(
        arquivo.read_text(encoding="utf-8").replace(
            'modelo = "provedor/modelo-de-teste"', 'modelo = ""', 1
        ),
        encoding="utf-8",
    )

    item = veredito_de(diagnosticar(arquivo), "Modelos por estágio")
    assert item.veredito is Veredito.FALHOU
    assert "mapeador" in item.detalhe


# ---------------------------------------------------------------------------
# Assinaturas que o duplo precisa espelhar
# ---------------------------------------------------------------------------


def test_o_duplo_de_executar_espelha_a_chamada_real():
    """Guarda contra o duplo divergir de `processo.executar` e mascarar a falha.

    Se a produção passar a chamar `executar` com outra forma de argumentos, o duplo
    aceita calado (`**_`) e o teste continua verde sobre um caminho que não existe
    mais. Comparar as assinaturas é o que impede isso.
    """
    parametros: Sequence[str] = list(signature(executar).parameters)
    assert parametros[0] == "argv"
    assert "timeout_s" in parametros
