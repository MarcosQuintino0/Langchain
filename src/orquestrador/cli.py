"""Entrypoint de linha de comando.

    orquestrador init                              escreve o config.toml do projeto
    orquestrador doctor                            diagnostica o ambiente
    orquestrador --dry-run --recurso pedidos       execução sem modelo
    orquestrador --recurso pedidos --config c.toml execução real

Só argumentos, montagem da execução e apresentação: os loops de controle vivem em
`pipeline.py`.

`init` e `doctor` moram aqui, e não em `ferramentas/`, porque o que eles produzem é
**apresentação**: um arquivo comentado para uma pessoa preencher e uma lista de
vereditos para uma pessoa ler. O I/O externo de que precisam — subprocesso,
extração de superfície, leitura de configuração — vem pronto dos adaptadores; o que
esta camada acrescenta é o texto que diz o que fazer quando cada item falha.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from orquestrador.analise_estatica import extrator_de_superficie
from orquestrador.config import Config
from orquestrador.contratos import EstadoDoRecurso, Recurso
from orquestrador.excecoes import (
    ErroDeConfiguracao,
    ErroDeFerramenta,
    ErroDeProvedor,
    ExecutavelAusente,
    ProjetoNaoPreparado,
)
from orquestrador.ferramentas.processo import executar
from orquestrador.ferramentas.publicacao import remover_criados
from orquestrador.observabilidade import manifesto_de_execucao, tabelas
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import (
    Registro,
    configurar_console,
    diretorio_de_execucao,
)
from orquestrador.pipeline import NAO_EXECUTADO, Pipeline, ResultadoDoRecurso
from orquestrador.raiz import (
    ARQUIVO_ENV,
    ARVORE_DE_FONTES,
    CONFIG_PADRAO,
    DIR_FIXTURES,
    DIR_PROMPTS_PADRAO,
    RAIZ_PROJETO,
)
from orquestrador.simulacao import Roteiros, preparar_sandbox

# Códigos de saída. `2` cobre tudo que é erro do operador ou indisponibilidade da
# ferramenta — configuração inválida, invocação impossível, comando que ainda não
# existe. O que importa é não ser 0: em CI, 0 é indistinguível de trabalho feito.
#
# `3` é o terceiro estado do recurso: tudo passou, o artefato foi publicado, e
# alguma coisa precisa de olho humano — hoje, schema do consumidor que não declara
# campo que existe no backend. Não é 0 porque um pipeline verde esconderia a
# revisão pendente, e não é 1 porque nenhum gate reprovou e não há nada para o
# modelo consertar.
SUCESSO = 0
FALHA_DE_GATE = 1
ERRO_DE_USO = 2
REQUER_REVISAO = 3
# Indisponibilidade do provedor de LLM tem código próprio porque a resposta do
# operador é outra: `2` pede para arrumar a configuração ou o ambiente, `4` pede
# para esperar e repetir. Num agendamento, é a diferença entre alertar alguém e
# reenfileirar sozinho.
ERRO_DE_PROVEDOR = 4

MARCA_DO_ESTADO: dict[EstadoDoRecurso, str] = {
    EstadoDoRecurso.APROVADO: "[green]OK[/green]",
    EstadoDoRecurso.REPROVADO: "[red]FALHOU[/red]",
    EstadoDoRecurso.REQUER_REVISAO: "[yellow]REVISAR[/yellow]",
}


def montar_recursos(config: Config, nomes: list[str]) -> list[Recurso]:
    return [
        Recurso(
            nome=nome,
            caminho_testes=config.caminhos.recurso(nome),
            raiz_schemas=config.caminhos.dir_schemas_abs,
            caminhos_backend=[config.caminhos.backend],
        )
        for nome in nomes
    ]


def inteiro_positivo(texto: str) -> int:
    """Tipo do argparse para limite que não faz sentido em zero nem negativo.

    Sem ele, `--max-tentativas 0` era aceito pelo parser e depois descartado por um
    `if` de truthiness — o pipeline rodava com o limite do arquivo, silenciosamente
    diferente do que a linha de comando pediu.
    """
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} não é um número inteiro") from None
    if valor < 1:
        raise argparse.ArgumentTypeError(f"precisa ser >= 1 (recebido {valor})")
    return valor


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        prog="orquestrador",
        description="Orquestrador multi-agente de testes de API (skill qa-api).",
        epilog=(
            "comandos: `orquestrador init` escreve o config.toml do projeto; "
            "`orquestrador doctor` diagnostica o ambiente. Sem comando, esta é a "
            "execução do pipeline."
        ),
    )
    analisador.add_argument("--config", type=Path, default=None, help="arquivo de configuração")
    analisador.add_argument(
        "--recurso",
        action="append",
        default=[],
        dest="recursos",
        help="nome do recurso (repetível). No dry-run, o padrão é todos os das fixtures.",
    )
    analisador.add_argument(
        "--dry-run",
        action="store_true",
        help="roda ponta a ponta sem chamar nenhum modelo, usando fixtures.",
    )
    analisador.add_argument(
        "--max-tentativas",
        type=inteiro_positivo,
        default=None,
        help="sobrescreve max_tentativas de todos os gates (inteiro >= 1).",
    )
    analisador.add_argument(
        "--rodar-cypress",
        action="store_true",
        help="executa o Cypress no Bloco 3 (por padrão é pulado).",
    )
    analisador.add_argument(
        "--auditor",
        action="store_true",
        help="RECUSADO enquanto o auditor semântico for stub: encerra com erro.",
    )
    analisador.add_argument(
        "--remover-reprovados",
        action="store_true",
        help=(
            "apaga o que ESTA ferramenta criou nos recursos que não terminaram "
            "aprovados. Nunca toca em arquivo preexistente nem em arquivo que "
            "mudou desde que o criamos."
        ),
    )
    return analisador.parse_args(argv)


def recusar_indisponiveis(args: argparse.Namespace, console: Console) -> int | None:
    """Recusa, antes de qualquer trabalho, as flags que hoje mentiriam.

    `--auditor` encerrava com código 0 imprimindo a descrição do stub. Código 0 é a
    frase "auditoria feita" na única linguagem que a CI lê; enquanto o auditor não
    existir, a resposta honesta é indisponibilidade.

    Devolve o código de saída quando recusa, ou `None` para seguir.
    """
    if args.auditor:
        console.print(
            "[red]--auditor está indisponível.[/red] O auditor semântico é um stub: ele "
            "devolveria um veredito vazio, e encerrar com código 0 faria a CI registrar "
            "uma auditoria que não aconteceu.\n"
            "Ele continua fora do loop quente; nada no pipeline depende dele."
        )
        return ERRO_DE_USO
    return None


def recusar_dry_run_sem_fixtures(console: Console) -> int | None:
    """Diz que o `--dry-run` precisa do checkout, em vez de morrer sem contexto.

    `fixtures/` fica **fora** do wheel de propósito: são dezenas de artefatos de
    desenvolvimento — roteiros de resposta, backend e projeto Cypress de mentira —
    que só existem para exercitar o pipeline daqui, e empacotá-los faria todo
    usuário baixar o material de teste do projeto. A consequência é que a instalação
    a partir do wheel não tem `--dry-run`, e o certo é dizer isso: sem esta recusa,
    a falha vinha de dentro de `preparar_sandbox`, como um erro de `copytree` sobre
    um caminho que ninguém pediu.

    Quem quer conferir a instalação usa `orquestrador doctor`, que é o comando feito
    para isso e não depende de fixture nenhuma.
    """
    roteiros = DIR_FIXTURES / "roteiros"
    if roteiros.is_dir():
        return None
    console.print(
        f"[red]--dry-run indisponível:[/red] fixtures não encontradas em {DIR_FIXTURES}.\n"
        "Elas são dado de desenvolvimento e não vão no pacote instalável. Para "
        "exercitar o pipeline sem gastar token, rode a partir de um checkout do "
        "repositório (ou aponte ORQUESTRADOR_RAIZ para um).\n"
        "Para conferir se esta instalação está sã, use `orquestrador doctor`."
    )
    return ERRO_DE_USO


def avisar_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Diz, no fim, o que ficou inválido em disco e por quê."""
    com_lixo = [resultado for resultado in resultados if resultado.arquivos_reprovados]
    if not com_lixo:
        return
    registro.aviso(
        "Artefatos deixados em estado REPROVADO (mantidos de propósito — é o que se "
        "inspeciona para entender a falha). Use --remover-reprovados para apagar o "
        "que foi criado por esta ferramenta:"
    )
    for resultado in com_lixo:
        codigos = ", ".join(resultado.codigos_remanescentes) or "(sem código)"
        onde = "publicado no projeto" if resultado.publicado else "em staging, não publicado"
        registro.info(f"  {resultado.recurso} [{codigos}] — {onde}")
        for caminho in resultado.arquivos_reprovados:
            registro.info(f"    {caminho}")


def remover_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Apaga o que **esta ferramenta criou** nos recursos que não foram aprovados.

    A versão anterior desta função foi desligada porque chamava `unlink()` na lista
    inteira, sem distinguir arquivo nosso de arquivo do consumidor. O que a traz de
    volta é o diário: `remover_criados` recusa tudo que não é `criado` e tudo cujo
    hash mudou desde que o gravamos. Um spec que nasceu conosco e o desenvolvedor
    editou à mão deixa de ser descartável no instante em que ele o salva.

    O staging é caso à parte e é apagado inteiro: ele nasceu nesta execução, é
    nosso do primeiro ao último byte, e é o único diretório nosso que fica dentro
    do projeto de quem nos contratou.
    """
    for resultado in resultados:
        if resultado.sucesso:
            continue
        removidos, recusados = remover_criados(resultado.diario)
        if resultado.staging is not None and resultado.staging.is_dir():
            shutil.rmtree(resultado.staging, ignore_errors=True)
            registro.info(f"  {resultado.recurso}: staging removido ({resultado.staging})")
        for caminho in removidos:
            registro.info(f"  {resultado.recurso}: removido {caminho}")
        for caminho in recusados:
            registro.aviso(
                f"  {resultado.recurso}: MANTIDO {caminho} — não fomos nós que o "
                "criamos, ou ele mudou depois que o criamos"
            )


def escrever_manifesto(
    config: Config,
    registro: Registro,
    *,
    dir_execucao: Path,
    dry_run: bool,
    recursos: list[str],
    com_artefatos: bool,
) -> None:
    """Grava o `manifesto-execucao.json` e diz no log o que não deu para coletar.

    Chamado **duas** vezes: no início, para que uma execução que morra no meio
    ainda deixe o cabeçalho do chamado de suporte; e no fim, quando existem
    artefatos para hashear. Escrever só no fim faria o diagnóstico faltar
    exatamente nas execuções que mais precisam dele.

    O que falhou é dito no console porque `campos_ausentes` dentro de um JSON é
    exatamente o tipo de coisa que ninguém abre: quem vai precisar do commit do
    backend descobre no chamado, meses depois, que ele nunca foi coletado.
    """
    manifesto = manifesto_de_execucao.escrever(
        config,
        dir_execucao / manifesto_de_execucao.NOME_DO_MANIFESTO,
        run_id=dir_execucao.name,
        dry_run=dry_run,
        recursos=recursos,
        dir_artefatos=(dir_execucao / "artefatos") if com_artefatos else None,
    )
    ausentes: dict[str, str] = manifesto["campos_ausentes"]
    registro.evento(
        TipoDeEvento.MANIFESTO_DE_EXECUCAO,
        arquivo=dir_execucao / manifesto_de_execucao.NOME_DO_MANIFESTO,
        com_artefatos=com_artefatos,
        campos_ausentes=ausentes,
    )
    if ausentes:
        registro.aviso(
            "manifesto de execução incompleto (a execução segue): " + ", ".join(sorted(ausentes))
        )


# ---------------------------------------------------------------------------
# `orquestrador init`
# ---------------------------------------------------------------------------

# Marca dos campos de caminho que não têm padrão possível. Ela é um caminho
# inválido de propósito: `""` viraria `Path(".")` na resolução da configuração, e
# então o diretório do próprio `config.toml` passaria por "backend existe" — o valor
# não preenchido aprovaria justamente a checagem que existe para pegá-lo.
MARCA_DE_PREENCHIMENTO = "PREENCHA"

# `modelo` fica vazio, e não com a marca acima, porque os dois tipos de campo têm
# formas diferentes de "não preenchido". Um caminho falso é detectável e inofensivo;
# um identificador de modelo falso seria enviado ao provedor como se fosse real. E
# nenhum nome de modelo pode aparecer aqui (princípio 6): string vazia é o único
# valor que o `doctor` consegue distinguir de uma escolha deliberada.
MODELO_DE_CONFIG_DE_PROJETO = """# Configuração do orquestrador — gerada por `orquestrador init`.
#
# Caminhos relativos são resolvidos contra o diretório DESTE arquivo.
# Nenhum nome de modelo aparece em código: todos vêm daqui.
#
# Os campos marcados com PREENCHA (e os `modelo` vazios) não têm padrão possível.
# Rode `orquestrador doctor` depois de preencher: ele confere cada um e diz o que
# fazer em cada falha.

[caminhos]
# A skill `qa-api` mora em OUTRO repositório e é apenas consumida, nunca modificada.
# Caminho absoluto: os dois projetos são independentes e não têm posição relativa
# garantida.
skill = "PREENCHA/caminho/para/skills/qa-api"
# scripts = ".../skills/qa-api/scripts"   # derivado de `skill` quando omitido

# O backend a mapear e o projeto Cypress que recebe os testes gerados.
backend = "PREENCHA/caminho/para/o/backend"
projeto_testes = "PREENCHA/caminho/para/o/projeto-de-testes"

# Relativos ao projeto de testes. Os padrões abaixo são a convenção da skill;
# troque-os se o seu projeto usa outro layout.
dir_recursos = "cypress/e2e/apis"
dir_schemas = "cypress/fixtures/schemas"
support_compartilhado = "cypress/support/api"
graph = ".agents/state/qa-api/graphify-out/graph.json"

# Relativo a este diretório: logs, artefatos e sandbox de cada execução.
saida = ".execucoes"

[skill]
# Hash dos `.mjs` que este orquestrador invoca. Vazio desliga a trava — que é o
# certo até você conferir o contrato pela primeira vez. `orquestrador doctor`
# imprime o hash atual para você colar aqui.
impressao_esperada = ""

[openrouter]
base_url = "https://openrouter.ai/api/v1"
# A chave vem SEMPRE do ambiente, nunca deste arquivo. Crie um `.env` ao lado dele.
api_key_env = "OPENROUTER_API_KEY"
timeout_s = 180.0
max_retries = 2

# Identificador do modelo no provedor, na forma que ele espera.
# O mapeador tem o julgamento mais difícil e o menor volume de saída: pede o modelo
# mais capaz. O executor tem o volume de tokens e trabalho mecânico se o gabarito
# for bom: aceita um modelo mais barato, porque o Gate B pega os erros de forma
# determinística.
[estagios.mapeador]
modelo = ""
temperatura = 0.0
modo_estruturado = "prompt"
max_tentativas_schema = 3
limite_passos = 60

[estagios.executor]
modelo = ""
temperatura = 0.0
modo_estruturado = "prompt"
max_tentativas_schema = 3

[gates.a]
flags = ["--so-manifesto"]
max_tentativas = 3

[gates.b]
flags = ["--exigir-campos"]
max_tentativas = 3
exigir_cobertura = true

[execucao]
node = "node"
graphify = "graphify"
timeout_s = 600

# Prettier e ESLint do Gate B. Lista vazia desliga a etapa; ligue apontando para o
# toolchain do projeto consumidor. O diretório do recurso entra como último argumento.
#   prettier = ["npx", "--no-install", "prettier", "--check"]
#   eslint   = ["npx", "--no-install", "eslint", "--format", "json"]
prettier = []
eslint = []
exigir_formatadores = false

# Bloco 3, só com --rodar-cypress. O comando PRECISA conter a marca {relatorio} no
# argumento que diz ao repórter onde escrever o JSON.
#   cypress = ["npx", "--no-install", "cypress", "run",
#              "--reporter", "json", "--reporter-options", "output={relatorio}"]
cypress = []

max_bytes_arquivo = 2000000
max_resultados_busca = 40
"""


def comando_init(argv: list[str], console: Console) -> int:
    analisador = argparse.ArgumentParser(
        prog="orquestrador init",
        description=(
            "Escreve um config.toml de projeto, comentado, com os campos que "
            "precisam ser preenchidos."
        ),
    )
    analisador.add_argument(
        "--em",
        type=Path,
        default=None,
        help="diretório onde escrever (padrão: o diretório atual).",
    )
    analisador.add_argument(
        "--forcar",
        action="store_true",
        help="sobrescreve um config.toml existente.",
    )
    args = analisador.parse_args(argv)

    destino = (args.em or Path.cwd()).expanduser().resolve()
    if not destino.is_dir():
        console.print(f"[red]diretório não encontrado:[/red] {destino}")
        return ERRO_DE_USO

    arquivo = destino / "config.toml"
    if arquivo.exists() and not args.forcar:
        # Recusar é o padrão porque este arquivo é do usuário: ele tem caminhos,
        # escolha de modelo e a impressão da skill conferida à mão. Sobrescrever em
        # silêncio apagaria trabalho que não temos como recuperar.
        console.print(
            f"[red]já existe:[/red] {arquivo}\n"
            "Não sobrescrevo configuração existente. Use --forcar se for isso mesmo, "
            "ou --em para escrever em outro diretório."
        )
        return ERRO_DE_USO

    arquivo.write_text(MODELO_DE_CONFIG_DE_PROJETO, encoding="utf-8")
    console.print(f"[green]escrito:[/green] {escape(str(arquivo))}")
    # `escape`: o texto cita blocos do TOML, e o Rich leria "[estagios.*]" como tag
    # de estilo e o engoliria — o passo 1 sairia mandando preencher "os dois .modelo".
    console.print(
        escape(
            "\nPróximos passos:\n"
            f"  1. preencha os campos marcados com {MARCA_DE_PREENCHIMENTO} e os dois "
            "[estagios.*].modelo;\n"
            "  2. crie um .env ao lado com a chave do provedor "
            f"(OPENROUTER_API_KEY=...) — ela nunca vai para o {arquivo.name};\n"
            "  3. rode `orquestrador doctor` e conserte o que ele apontar."
        )
    )
    return SUCESSO


# ---------------------------------------------------------------------------
# `orquestrador doctor`
# ---------------------------------------------------------------------------

# Node 24 é o piso do README e o que a skill assume. Graphify é comparado com a
# versão FIXADA no manifesto, e a igualdade é exata de propósito: é a mesma
# comparação que o `qa-reindex.mjs` faz antes de reindexar. Divergir aqui e passar
# significaria descobrir a incompatibilidade no Bloco 0, com o erro vindo de dentro
# de um script de outro repositório.
NODE_MINIMO = (24, 0, 0)

# Os prompts sem os quais não há estágio de LLM. O auditor está fora porque é stub e
# a flag que o invocaria é recusada.
PROMPTS_EXIGIDOS = ("mapeador", "executor")

_VERSAO = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


class Veredito(StrEnum):
    OK = "OK"
    AVISO = "AVISO"
    FALHOU = "FALHOU"


MARCA_DO_VEREDITO: dict[Veredito, str] = {
    Veredito.OK: "[green]OK[/green]",
    Veredito.AVISO: "[yellow]AVISO[/yellow]",
    Veredito.FALHOU: "[red]FALHOU[/red]",
}


@dataclass(frozen=True)
class ItemDeDiagnostico:
    """Um veredito e o que fazer quando ele não é OK.

    `conserto` não é opcional na prática: um diagnóstico que diz "Node ausente" e
    para aí obriga quem lê a descobrir sozinho o que instalar. O único caso em que
    ele fica vazio é o `OK`, onde não há nada a fazer.
    """

    nome: str
    veredito: Veredito
    detalhe: str
    conserto: str = ""


def _versao(texto: str) -> tuple[int, int, int] | None:
    """Primeira versão semântica do texto, como tripla comparável."""
    achado = _VERSAO.search(texto)
    if achado is None:
        return None
    partes = achado.group(0).split("-")[0].split("+")[0].split(".")
    return (int(partes[0]), int(partes[1]), int(partes[2]))


def _texto_da_versao(texto: str) -> str | None:
    achado = _VERSAO.search(texto)
    return achado.group(0) if achado else None


def _diagnosticar_python() -> ItemDeDiagnostico:
    atual = sys.version_info
    detalhe = f"{atual.major}.{atual.minor}.{atual.micro} em {sys.executable}"
    if (atual.major, atual.minor) < (3, 12):
        return ItemDeDiagnostico(
            "Python",
            Veredito.FALHOU,
            detalhe,
            "o pacote declara requires-python >= 3.12. Instale-o num ambiente 3.12+.",
        )
    return ItemDeDiagnostico("Python", Veredito.OK, detalhe)


def _diagnosticar_instalacao() -> ItemDeDiagnostico:
    """De onde este orquestrador está rodando — checkout ou pacote instalado.

    Não é um veredito de saúde: é o contexto que explica todos os outros. Um
    `--dry-run` que não acha fixtures e um `config.toml` procurado no diretório
    atual são consequências desta linha, não defeitos independentes.
    """
    if ARVORE_DE_FONTES is not None:
        return ItemDeDiagnostico(
            "Instalação",
            Veredito.OK,
            f"rodando do checkout em {ARVORE_DE_FONTES}",
        )
    return ItemDeDiagnostico(
        "Instalação",
        Veredito.OK,
        f"pacote instalado; raiz do projeto = diretório atual ({RAIZ_PROJETO})",
    )


def _diagnosticar_prompts() -> ItemDeDiagnostico:
    """Os prompts do padrão — os que o wheel precisava conter e não continha."""
    origem = "empacotados no wheel" if ARVORE_DE_FONTES is None else "do checkout"
    if not DIR_PROMPTS_PADRAO.is_dir():
        return ItemDeDiagnostico(
            "Prompts",
            Veredito.FALHOU,
            f"diretório não encontrado: {DIR_PROMPTS_PADRAO} ({origem})",
            "instalação incompleta: reinstale o pacote. Sem prompt não há estágio de LLM.",
        )
    faltando = [
        nome for nome in PROMPTS_EXIGIDOS if not (DIR_PROMPTS_PADRAO / f"{nome}.md").is_file()
    ]
    if faltando:
        return ItemDeDiagnostico(
            "Prompts",
            Veredito.FALHOU,
            f"{DIR_PROMPTS_PADRAO} ({origem}) sem: {', '.join(f'{n}.md' for n in faltando)}",
            "reinstale o pacote, ou aponte [caminhos].prompts para o diretório correto.",
        )
    return ItemDeDiagnostico("Prompts", Veredito.OK, f"{DIR_PROMPTS_PADRAO} ({origem})")


def _diagnosticar_config(caminho: Path | None) -> tuple[ItemDeDiagnostico, Config | None]:
    alvo = caminho or CONFIG_PADRAO
    try:
        config = Config.carregar(caminho)
    except ErroDeConfiguracao as erro:
        return (
            ItemDeDiagnostico(
                "Configuração",
                Veredito.FALHOU,
                str(erro).splitlines()[0],
                f"rode `orquestrador init` para escrever um {alvo.name} comentado, "
                "ou aponte --config para o arquivo certo.",
            ),
            None,
        )
    return ItemDeDiagnostico("Configuração", Veredito.OK, str(config.origem)), config


def _nao_preenchido(caminho: Path) -> bool:
    return MARCA_DE_PREENCHIMENTO in caminho.as_posix()


def _diagnosticar_diretorio(
    nome: str, caminho: Path, *, campo: str, para_que: str
) -> ItemDeDiagnostico:
    if _nao_preenchido(caminho):
        return ItemDeDiagnostico(
            nome,
            Veredito.FALHOU,
            f"{campo} não preenchido",
            f"aponte {campo} para {para_que}.",
        )
    if not caminho.is_dir():
        return ItemDeDiagnostico(
            nome,
            Veredito.FALHOU,
            f"não encontrado: {caminho}",
            f"aponte {campo} para {para_que}.",
        )
    return ItemDeDiagnostico(nome, Veredito.OK, str(caminho))


def _diagnosticar_skill(config: Config) -> list[ItemDeDiagnostico]:
    presenca = _diagnosticar_diretorio(
        "Skill qa-api",
        config.caminhos.skill,
        campo="[caminhos].skill",
        para_que="o checkout do repositório da skill qa-api",
    )
    if presenca.veredito is not Veredito.OK:
        return [presenca]

    try:
        ausentes = [
            nome
            for nome in ("validar-suite-gerada.mjs", "qa-cobertura.mjs", "qa-reindex.mjs")
            if not config.caminhos.script(nome).is_file()
        ]
    except ErroDeConfiguracao as erro:
        return [ItemDeDiagnostico("Skill qa-api", Veredito.FALHOU, str(erro).splitlines()[0])]
    if ausentes:
        return [
            ItemDeDiagnostico(
                "Skill qa-api",
                Veredito.FALHOU,
                f"scripts ausentes em {config.caminhos.scripts}: {', '.join(ausentes)}",
                "confira se [caminhos].skill aponta para a raiz da skill (não para "
                "scripts/) e se o checkout dela está completo.",
            )
        ]
    return [presenca, _diagnosticar_impressao(config)]


def _diagnosticar_impressao(config: Config) -> ItemDeDiagnostico:
    """A trava de contrato com a skill: hash dos `.mjs` que invocamos."""
    try:
        atual = config.impressao_da_skill()
    except OSError as erro:
        return ItemDeDiagnostico(
            "Impressão da skill",
            Veredito.FALHOU,
            f"não consegui ler os scripts: {erro}",
            "confira permissão de leitura em [caminhos].skill.",
        )
    if not config.skill.impressao_esperada:
        return ItemDeDiagnostico(
            "Impressão da skill",
            Veredito.AVISO,
            f"verificação desligada; a impressão atual é {atual}",
            "confira o contrato da skill uma vez e cole "
            f'[skill].impressao_esperada = "{atual}" na configuração. Vazio é o certo '
            "só enquanto você edita a skill e o consumidor ao mesmo tempo.",
        )
    if atual != config.skill.impressao_esperada:
        return ItemDeDiagnostico(
            "Impressão da skill",
            Veredito.FALHOU,
            f"esperada {config.skill.impressao_esperada}, atual {atual}",
            "a skill mudou desde a última conferência. Reveja o contrato — argumentos, "
            "códigos de saída, forma do JSON e os códigos QAAPI- — e só então atualize "
            "[skill].impressao_esperada.",
        )
    return ItemDeDiagnostico("Impressão da skill", Veredito.OK, atual)


def _saida_de_versao(executavel: str, config: Config) -> str | ItemDeDiagnostico:
    """Roda `<executavel> --version`, ou devolve o item de falha já formado."""
    try:
        saida = executar([executavel, "--version"], timeout_s=config.execucao.timeout_s)
    except ExecutavelAusente:
        return ItemDeDiagnostico(executavel, Veredito.FALHOU, "não encontrado no PATH")
    except ErroDeFerramenta as erro:
        return ItemDeDiagnostico(executavel, Veredito.FALHOU, str(erro))
    if saida.codigo != 0:
        return ItemDeDiagnostico(
            executavel, Veredito.FALHOU, f"`--version` saiu com código {saida.codigo}"
        )
    return saida.texto


def _diagnosticar_node(config: Config) -> ItemDeDiagnostico:
    resposta = _saida_de_versao(config.execucao.node, config)
    if isinstance(resposta, ItemDeDiagnostico):
        return ItemDeDiagnostico(
            "Node",
            Veredito.FALHOU,
            resposta.detalhe,
            "instale o Node 24+ e deixe-o no PATH, ou aponte [execucao].node para o "
            "binário. Os gates são scripts .mjs — sem Node não há reprovação "
            "determinística, e o pipeline recusa rodar.",
        )
    versao = _versao(resposta)
    if versao is None:
        return ItemDeDiagnostico(
            "Node",
            Veredito.AVISO,
            f"versão não reconhecida na saída: {resposta.strip()[:60]}",
            "confira manualmente se é 24 ou mais novo.",
        )
    if versao < NODE_MINIMO:
        return ItemDeDiagnostico(
            "Node",
            Veredito.FALHOU,
            f"v{'.'.join(str(p) for p in versao)}",
            f"a skill assume Node {NODE_MINIMO[0]}+. Atualize o Node.",
        )
    return ItemDeDiagnostico("Node", Veredito.OK, f"v{'.'.join(str(p) for p in versao)}")


def _manifesto_do_graphify(config: Config) -> Path | None:
    """O mesmo manifesto que o `qa-reindex.mjs` vai consultar, na mesma ordem.

    Procurar em outro lugar seria pior que não procurar: o `doctor` aprovaria uma
    versão que a skill vai recusar, ou reprovaria uma que ela aceitaria. A ordem
    abaixo é a de `manifestCandidates` no `qa-reindex.mjs`, com o projeto de testes
    como raiz e a skill como último recurso (`<skills>/graphify/manifest.json`).
    """
    projeto = config.caminhos.projeto_testes
    candidatos = [
        projeto / ".agents" / "skills" / "graphify" / "manifest.json",
        projeto / "skills" / "graphify" / "manifest.json",
        config.caminhos.skill.parent / "graphify" / "manifest.json",
    ]
    return next((caminho for caminho in candidatos if caminho.is_file()), None)


def _ler_manifesto(manifesto: Path) -> dict[str, Any] | str:
    """Conteúdo do manifesto do Graphify, ou a explicação de por que não deu."""
    try:
        bruto: Any = json.loads(manifesto.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        return f"manifesto ilegível ({manifesto}): {erro}"
    if not isinstance(bruto, dict):
        return f"manifesto não é um objeto JSON ({manifesto})"
    return cast(dict[str, Any], bruto)


def _diagnosticar_graphify(config: Config) -> ItemDeDiagnostico:
    manifesto = _manifesto_do_graphify(config)
    fixada: str | None = None
    instalacao = ""
    if manifesto is not None:
        dados = _ler_manifesto(manifesto)
        if isinstance(dados, str):
            return ItemDeDiagnostico("Graphify", Veredito.FALHOU, dados)
        versao: Any = dados.get("version")
        fixada = versao if isinstance(versao, str) else None
        receita: Any = dados.get("install")
        if isinstance(receita, dict):
            passos = cast(dict[str, Any], receita)
            sugerida: Any = passos.get("uv") or passos.get("pipx") or passos.get("pip")
            instalacao = f" Instale com: {sugerida}" if isinstance(sugerida, str) else ""

    resposta = _saida_de_versao(config.execucao.graphify, config)
    if isinstance(resposta, ItemDeDiagnostico):
        alvo = f" Versão fixada: {fixada}." if fixada else ""
        return ItemDeDiagnostico(
            "Graphify",
            Veredito.FALHOU,
            resposta.detalhe,
            f"o Bloco 0 indexa o backend com ele; sem ele não há grafo.{alvo}{instalacao}",
        )

    atual = _texto_da_versao(resposta)
    if fixada is None:
        return ItemDeDiagnostico(
            "Graphify",
            Veredito.AVISO,
            f"{atual or resposta.strip()[:40]}; manifesto do Graphify não encontrado",
            "sem o manifesto não dá para conferir a versão fixada. Ele é procurado em "
            "<projeto_testes>/.agents/skills/graphify/, <projeto_testes>/skills/graphify/ "
            "e ao lado da skill qa-api.",
        )
    if atual != fixada:
        return ItemDeDiagnostico(
            "Graphify",
            Veredito.FALHOU,
            f"fixada {fixada} ({manifesto}), instalada {atual or resposta.strip()[:40]}",
            "o qa-reindex.mjs compara as duas por igualdade exata e recusa reindexar "
            f"quando divergem.{instalacao}",
        )
    return ItemDeDiagnostico("Graphify", Veredito.OK, f"{atual} (fixada em {manifesto})")


def _diagnosticar_projeto_de_testes(config: Config) -> list[ItemDeDiagnostico]:
    presenca = _diagnosticar_diretorio(
        "Projeto de testes",
        config.caminhos.projeto_testes,
        campo="[caminhos].projeto_testes",
        para_que="o projeto Cypress que vai receber os testes gerados",
    )
    if presenca.veredito is not Veredito.OK:
        return [presenca]

    try:
        superficie = extrator_de_superficie.extrair(config)
    except ProjetoNaoPreparado as erro:
        return [
            presenca,
            ItemDeDiagnostico(
                "Projeto preparado",
                Veredito.FALHOU,
                str(erro).splitlines()[0],
                "o orquestrador gera testes, não prepara o projeto. Ponha os módulos "
                f"compartilhados em {config.caminhos.support_compartilhado} (client, "
                "rotas, auth, asserts base) — veja references/preparar-projeto.md da "
                "skill, ou a arquitetura-base em assets/cypress-api-base/.",
            ),
        ]
    return [
        presenca,
        ItemDeDiagnostico(
            "Projeto preparado",
            Veredito.OK,
            f"{len(superficie.modulos)} módulo(s) compartilhado(s), "
            f"{superficie.total_de_exports} export(s) em "
            f"{config.caminhos.support_compartilhado}",
        ),
    ]


def _diagnosticar_chave(config: Config) -> ItemDeDiagnostico:
    """Presença da chave do provedor — nunca o valor dela.

    O `doctor` existe para ser colado num chamado de suporte. Imprimir prefixo,
    sufixo ou tamanho da chave transformaria esse hábito num vazamento; o que
    interessa aqui é só se ela está no ambiente.
    """
    try:
        config.openrouter.chave()
    except ErroDeConfiguracao:
        return ItemDeDiagnostico(
            "Chave do provedor",
            Veredito.FALHOU,
            f"{config.openrouter.api_key_env} não definida",
            f"crie um .env ao lado do config.toml com {config.openrouter.api_key_env}=..., "
            "ou exporte a variável. Para rodar sem modelo nenhum, use --dry-run.",
        )
    return ItemDeDiagnostico(
        "Chave do provedor", Veredito.OK, f"{config.openrouter.api_key_env} definida"
    )


def _diagnosticar_modelos(config: Config) -> ItemDeDiagnostico:
    vazios = sorted(nome for nome, estagio in config.estagios.items() if not estagio.modelo.strip())
    if vazios:
        return ItemDeDiagnostico(
            "Modelos por estágio",
            Veredito.FALHOU,
            f"sem modelo: {', '.join(vazios)}",
            "preencha [estagios.<nome>].modelo com o identificador do modelo no "
            "provedor. Nenhum nome de modelo vem do código.",
        )
    escolhidos = ", ".join(f"{nome}={estagio.modelo}" for nome, estagio in config.estagios.items())
    return ItemDeDiagnostico("Modelos por estágio", Veredito.OK, escolhidos)


def diagnosticar(caminho_da_config: Path | None) -> list[ItemDeDiagnostico]:
    """Roda todos os diagnósticos, na ordem em que fazem sentido para quem lê.

    O que não depende da configuração vem primeiro, de propósito: se o `config.toml`
    não carrega, ainda é útil saber que o Python e os prompts estão bem — a lista
    encurta, mas não vira uma linha só de erro.
    """
    itens = [_diagnosticar_python(), _diagnosticar_instalacao(), _diagnosticar_prompts()]
    item_config, config = _diagnosticar_config(caminho_da_config)
    itens.append(item_config)
    if config is None:
        return itens

    if config.origem is not None:
        # A mensagem de `chave()` manda criar o `.env` ao lado da config, então é ali
        # que o doctor procura — não na raiz do checkout, que num pacote instalado
        # nem existe. `load_dotenv` não sobrescreve o que já está no ambiente.
        load_dotenv(config.origem.parent / ".env")

    itens.append(_diagnosticar_modelos(config))
    itens.extend(_diagnosticar_skill(config))
    itens.append(_diagnosticar_node(config))
    itens.append(_diagnosticar_graphify(config))
    itens.extend(_diagnosticar_projeto_de_testes(config))
    itens.append(
        _diagnosticar_diretorio(
            "Backend",
            config.caminhos.backend,
            campo="[caminhos].backend",
            para_que="o código-fonte do backend que o Bloco 0 vai indexar",
        )
    )
    itens.append(_diagnosticar_chave(config))
    return itens


def comando_doctor(argv: list[str], console: Console) -> int:
    analisador = argparse.ArgumentParser(
        prog="orquestrador doctor",
        description=(
            "Diagnostica o ambiente: Python, Node, Graphify, skill, projeto de "
            "testes e chave do provedor. Cada item com veredito e o que fazer."
        ),
    )
    analisador.add_argument("--config", type=Path, default=None, help="arquivo de configuração")
    args = analisador.parse_args(argv)

    itens = diagnosticar(args.config)

    tabela = Table(title="orquestrador doctor", show_lines=False)
    tabela.add_column("item")
    tabela.add_column("veredito")
    tabela.add_column("detalhe", overflow="fold")
    for item in itens:
        tabela.add_row(item.nome, MARCA_DO_VEREDITO[item.veredito], escape(item.detalhe))
    console.print(tabela)

    pendentes = [item for item in itens if item.veredito is not Veredito.OK and item.conserto]
    for item in pendentes:
        console.print(f"\n{MARCA_DO_VEREDITO[item.veredito]} [bold]{item.nome}[/bold]")
        console.print(f"  {escape(item.detalhe)}")
        console.print(f"  → {escape(item.conserto)}")

    if any(item.veredito is Veredito.FALHOU for item in itens):
        console.print(
            "\n[red]ambiente incompleto[/red] — os itens acima impedem uma execução real."
        )
        return ERRO_DE_USO
    console.print("\n[green]ambiente pronto.[/green]")
    return SUCESSO


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------

# Subcomandos em vez de `add_subparsers` porque a forma histórica da CLI é
# `orquestrador --dry-run --recurso x`, sem verbo. Um subparser obrigaria a
# inventar um `run` e quebrar toda invocação existente; um opcional torna
# ambíguo o argumento posicional. O despacho por primeira palavra preserva as
# duas formas sem ambiguidade: `init` e `doctor` não são valores de flag nenhuma.
COMANDOS = ("init", "doctor")


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if argumentos and argumentos[0] in COMANDOS:
        configurar_console()
        console = Console()
        if argumentos[0] == "init":
            return comando_init(argumentos[1:], console)
        return comando_doctor(argumentos[1:], console)
    return executar_pipeline(argumentos)


def executar_pipeline(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configurar_console()
    console = Console()

    if (recusa := recusar_indisponiveis(args, console)) is not None:
        return recusa

    try:
        config = Config.carregar(args.config)
    except ErroDeConfiguracao as erro:
        console.print(f"[red]configuração inválida:[/red] {erro}")
        return ERRO_DE_USO

    if not args.dry_run:
        load_dotenv(ARQUIVO_ENV)

    if args.max_tentativas is not None:
        # Cópia revalidada, não mutação: o valor da linha de comando atravessa o
        # mesmo tipo que o arquivo atravessou. `is not None` porque `0` é um pedido
        # explícito e inválido, não ausência de pedido — o parser já o recusou, e
        # este `if` não pode reintroduzir a diferença.
        try:
            config = config.com_max_tentativas(args.max_tentativas)
        except ErroDeConfiguracao as erro:
            console.print(f"[red]configuração inválida:[/red] {erro}")
            return ERRO_DE_USO

    dir_execucao = diretorio_de_execucao(
        config.caminhos.saida
        if config.caminhos.saida.is_absolute()
        else RAIZ_PROJETO / config.caminhos.saida
    )

    roteiros: Roteiros | None = None
    if args.dry_run:
        if (recusa := recusar_dry_run_sem_fixtures(console)) is not None:
            return recusa
        roteiros = Roteiros(DIR_FIXTURES / "roteiros")
        config = preparar_sandbox(config, dir_execucao / "sandbox")

    recursos_pedidos = args.recursos or (list(roteiros.recursos()) if roteiros else [])
    if not recursos_pedidos:
        console.print("[red]informe ao menos um --recurso.[/red]")
        return ERRO_DE_USO

    with Registro(dir_execucao / "execucao.jsonl", console) as registro:
        registro.info(f"log estruturado: {registro.caminho}")
        registro.evento(
            TipoDeEvento.EXECUCAO_INICIADA,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            config=str(config.origem),
            projeto_testes=config.caminhos.projeto_testes,
            backend=config.caminhos.backend,
        )
        escrever_manifesto(
            config,
            registro,
            dir_execucao=dir_execucao,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            com_artefatos=False,
        )

        try:
            config.validar_caminhos(exigir_backend=True)
        except ErroDeConfiguracao as erro:
            registro.falha(str(erro))
            return ERRO_DE_USO

        pipeline = Pipeline(
            config,
            registro,
            dry_run=args.dry_run,
            roteiros=roteiros,
            dir_execucao=dir_execucao,
            pular_cypress=not args.rodar_cypress,
        )

        try:
            resultados = pipeline.rodar(montar_recursos(config, recursos_pedidos))
        except ErroDeProvedor as erro:
            # Antes de `ErroDeFerramenta`, de quem herda: a ordem dos `except` é o
            # que separa "arrume o ambiente" de "espere e repita".
            registro.falha(str(erro))
            registro.evento(
                TipoDeEvento.EXECUCAO_ABORTADA,
                motivo=str(erro),
                categoria=erro.categoria.value,
                retentavel=erro.categoria.retentavel,
            )
            return ERRO_DE_PROVEDOR
        except (ErroDeFerramenta, ErroDeConfiguracao) as erro:
            registro.falha(f"erro de invocação do pipeline: {erro}")
            registro.evento(TipoDeEvento.EXECUCAO_ABORTADA, motivo=str(erro))
            return ERRO_DE_USO

        registro.titulo("Telemetria")
        console.print(tabelas.tabela_por_estagio(pipeline.telemetria))
        console.print(tabelas.tabela_por_recurso(pipeline.telemetria))
        console.print(tabelas.tabela_entrada_por_tentativa(pipeline.telemetria))
        # Só o mapeador tem tools; sem elas a tabela seria uma moldura vazia.
        if pipeline.telemetria.tools:
            console.print(tabelas.tabela_de_tools(pipeline.telemetria))
        registro.evento(TipoDeEvento.TELEMETRIA, **pipeline.telemetria.resumo_para_log())

        registro.titulo("Resumo")
        for resultado in resultados:
            marca = MARCA_DO_ESTADO[resultado.estado]
            console.print(
                f"{marca} {resultado.recurso}: "
                f"mapeador {resultado.tentativas_mapeador} tentativa(s), "
                f"executor {resultado.tentativas_executor} tentativa(s)"
                + (f" — {resultado.motivo}" if resultado.motivo else "")
            )
            # O terceiro estado precisa dizer o que revisar, e onde. Sem isto ele
            # seria só uma palavra diferente de OK, e quem lê trataria como falha.
            for divergencia in resultado.divergencias:
                console.print(f"     [yellow]{escape(divergencia.render())}[/yellow]")
                console.print(
                    "     o schema do seu projeto tem precedência e NÃO foi alterado: "
                    "declare os campos nele ou confirme que a ausência é intencional"
                )
            # Dito recurso a recurso, e não uma vez no rodapé: é a diferença entre
            # "os testes passaram" e "os testes não rodaram", e ela precisa estar
            # onde alguém lê o veredito daquele recurso.
            if resultado.execucao_de_testes == NAO_EXECUTADO:
                # `escape`: o motivo cita blocos do TOML, e o Rich leria
                # "[execucao]" como tag de estilo e o engoliria.
                console.print(
                    f"     [yellow]testes NÃO EXECUTADOS[/yellow] "
                    f"({escape(resultado.motivo_da_execucao_de_testes)}) — "
                    "a cobertura acima é estática, não é prova de runtime"
                )
        avisar_reprovados(resultados, registro)
        if args.remover_reprovados:
            registro.titulo("Remoção do que esta ferramenta criou")
            remover_reprovados(resultados, registro)
        interrupcao = pipeline.interrupcao
        if interrupcao is not None:
            # O resumo acima é dos recursos que terminaram. Sem esta linha ele
            # pareceria a execução inteira, e os recursos que nunca rodaram sumiriam
            # sem deixar rastro na tela.
            console.print(
                f"[red]EXECUÇÃO INTERROMPIDA[/red] em {interrupcao.recurso}: "
                f"{escape(interrupcao.motivo)}"
            )
            if interrupcao.recursos_nao_executados:
                console.print(
                    "     não chegaram a rodar: " + ", ".join(interrupcao.recursos_nao_executados)
                )
        escrever_manifesto(
            config,
            registro,
            dir_execucao=dir_execucao,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            com_artefatos=True,
        )
        registro.evento(
            TipoDeEvento.EXECUCAO_CONCLUIDA,
            sucesso=interrupcao is None and all(resultado.sucesso for resultado in resultados),
            interrompida=interrupcao is not None,
            recursos_nao_executados=(interrupcao.recursos_nao_executados if interrupcao else []),
            resultados=[
                {
                    "recurso": resultado.recurso,
                    "estado": resultado.estado.value,
                    "sucesso": resultado.sucesso,
                    "tentativas_mapeador": resultado.tentativas_mapeador,
                    "tentativas_executor": resultado.tentativas_executor,
                    "execucao_de_testes": resultado.execucao_de_testes,
                    "motivo": resultado.motivo,
                }
                for resultado in resultados
            ],
        )
        registro.info(f"artefatos e log desta execução: {dir_execucao}")

    if pipeline.interrupcao is not None:
        # Distinto do 1 de gate esgotado: ali o pipeline funcionou e o artefato não
        # passou; aqui o pipeline não conseguiu emitir veredito nenhum. E provedor é
        # distinto de ferramenta porque a resposta do operador é outra — esperar e
        # repetir, em vez de arrumar o ambiente.
        if pipeline.interrupcao.categoria_do_provedor is not None:
            return ERRO_DE_PROVEDOR
        return ERRO_DE_USO
    return codigo_de_saida(resultados)


def codigo_de_saida(resultados: list[ResultadoDoRecurso]) -> int:
    """O pior desfecho manda, e revisão pendente nunca vira 0.

    A ordem é reprovado > requer revisão > aprovado. Um recurso reprovado e outro
    pedindo revisão saem como reprovação: o código de saída é um número só, e o
    número precisa apontar para a coisa mais grave que aconteceu.
    """
    estados = {resultado.estado for resultado in resultados}
    if EstadoDoRecurso.REPROVADO in estados:
        return FALHA_DE_GATE
    if EstadoDoRecurso.REQUER_REVISAO in estados:
        return REQUER_REVISAO
    return SUCESSO


if __name__ == "__main__":
    sys.exit(main())
