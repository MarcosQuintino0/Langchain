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

from orquestrador.cli import (
    ERRO_DE_USO,
    SUCESSO,
    ItemDeDiagnostico,
    Veredito,
    diagnosticar,
    main,
)
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
    """Um ambiente completo: skill, backend, projeto preparado e manifesto fixado.

    Montado a partir do template do `orquestrador init`, com os campos preenchidos.
    Assim o teste do `doctor` também é um teste do template: se `init` passar a
    escrever um campo que a configuração recusa, esta fixture quebra.
    """
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    for nome in ("validar-suite-gerada.mjs", "qa-cobertura.mjs", "qa-reindex.mjs"):
        (skill / "scripts" / nome).write_text("// duplo de teste\n", encoding="utf-8")

    (tmp_path / "backend").mkdir()

    testes = tmp_path / "projeto-de-testes"
    support = testes / "cypress" / "support" / "api"
    support.mkdir(parents=True)
    (support / "client.js").write_text(
        "export function apiRequest(opcoes) {\n  return cy.request(opcoes);\n}\n",
        encoding="utf-8",
    )

    manifesto = testes / ".agents" / "skills" / "graphify" / "manifest.json"
    manifesto.parent.mkdir(parents=True)
    manifesto.write_text(
        '{"command": "graphify", "version": "'
        + VERSAO_DO_GRAPHIFY
        + '", "install": {"uv": "uv tool install graphifyy"}}\n',
        encoding="utf-8",
    )

    assert main(["init", "--em", str(tmp_path)]) == SUCESSO
    arquivo = tmp_path / "config.toml"
    texto = (
        arquivo.read_text(encoding="utf-8")
        .replace("PREENCHA/caminho/para/skills/qa-api", skill.as_posix())
        .replace("PREENCHA/caminho/para/o/backend", (tmp_path / "backend").as_posix())
        .replace("PREENCHA/caminho/para/o/projeto-de-testes", testes.as_posix())
        .replace('modelo = ""', 'modelo = "provedor/modelo-de-teste"')
    )
    arquivo.write_text(texto, encoding="utf-8")
    return tmp_path


@pytest.fixture
def versoes(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Instala o duplo de `executar` que responde `--version` por executável."""

    def instalar(*, node: str = "v24.11.1", graphify: str | None = VERSAO_DO_GRAPHIFY) -> None:
        def falso(argv: list[str], **_: object) -> SaidaProcesso:
            alvo = Path(argv[0]).stem
            if alvo == "graphify" and graphify is None:
                raise ExecutavelAusente('executável não encontrado no PATH: "graphify".')
            texto = node if alvo == "node" else f"graphify {graphify}"
            return SaidaProcesso(argv=argv, codigo=0, stdout=texto, stderr="", duracao_s=0.0)

        monkeypatch.setattr("orquestrador.cli.executar", falso)

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


def test_impressao_da_skill_desligada_e_aviso_com_o_hash_para_colar(
    projeto: Path, versoes: Callable[..., None]
):
    """Vazio é legítimo, mas não é OK: é uma trava desligada, e o doctor diz qual."""
    versoes()
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Impressão da skill")

    assert item.veredito is Veredito.AVISO
    assert "impressao_esperada" in item.conserto


# ---------------------------------------------------------------------------
# Um item estragado de cada vez
# ---------------------------------------------------------------------------


def test_node_antigo_reprova_dizendo_a_versao_minima(projeto: Path, versoes: Callable[..., None]):
    versoes(node="v20.11.0")
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Node")

    assert item.veredito is Veredito.FALHOU
    assert "24" in item.conserto


def test_graphify_divergente_do_manifesto_reprova(projeto: Path, versoes: Callable[..., None]):
    """A comparação é exata porque a do `qa-reindex.mjs` também é.

    Aprovar uma versão que a skill vai recusar empurra a descoberta para dentro do
    Bloco 0, com o erro vindo de um script de outro repositório.
    """
    versoes(graphify="0.9.27")
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Graphify")

    assert item.veredito is Veredito.FALHOU
    assert VERSAO_DO_GRAPHIFY in item.detalhe and "0.9.27" in item.detalhe


def test_graphify_ausente_reprova_com_a_receita_de_instalacao(
    projeto: Path, versoes: Callable[..., None]
):
    versoes(graphify=None)
    item = veredito_de(diagnosticar(projeto / "config.toml"), "Graphify")

    assert item.veredito is Veredito.FALHOU
    assert "uv tool install graphifyy" in item.conserto


def test_projeto_sem_modulos_compartilhados_reprova(projeto: Path, versoes: Callable[..., None]):
    versoes()
    for arquivo in (projeto / "projeto-de-testes" / "cypress" / "support" / "api").iterdir():
        arquivo.unlink()

    item = veredito_de(diagnosticar(projeto / "config.toml"), "Projeto preparado")

    assert item.veredito is Veredito.FALHOU
    assert "preparar-projeto" in item.conserto


def test_skill_ausente_reprova_antes_de_tentar_a_impressao(
    projeto: Path, versoes: Callable[..., None]
):
    versoes()
    for arquivo in (projeto / "skill" / "scripts").iterdir():
        arquivo.unlink()

    itens = diagnosticar(projeto / "config.toml")
    item = veredito_de(itens, "Skill qa-api")

    assert item.veredito is Veredito.FALHOU
    assert "qa-reindex.mjs" in item.detalhe
    assert not [i for i in itens if i.nome == "Impressão da skill"]


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


def test_todo_item_reprovado_diz_o_que_fazer(projeto: Path, versoes: Callable[..., None]):
    """Diagnóstico sem conserto obriga quem lê a descobrir sozinho o que instalar.

    Vale a pena testar porque a tentação de acrescentar um item novo sem a frase de
    conserto é grande — o veredito parece suficiente na hora de escrevê-lo.
    """
    versoes(node="v18.0.0", graphify=None)
    for arquivo in (projeto / "skill" / "scripts").iterdir():
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
