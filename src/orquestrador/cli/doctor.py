"""`orquestrador doctor` — o ambiente que uma execução real exige, item a item.

Fica com ~500 linhas de propósito. Ele muda por **um** motivo — o que uma
execução real precisa ter por perto —, e cerca de 60% do arquivo é o texto de
`conserto` de cada diagnóstico, que é editorial e coeso. Separar "as checagens"
de "a apresentação" separaria cada veredito da frase que diz o que fazer com ele,
que é a parte útil."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from orquestrador.analise_estatica import extrator_de_superficie
from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, SUCESSO
from orquestrador.cli.init import MARCA_DE_PREENCHIMENTO
from orquestrador.config import Config
from orquestrador.excecoes import (
    ErroDeConfiguracao,
    ErroDeFerramenta,
    ExecutavelAusente,
    ProjetoNaoPreparado,
)
from orquestrador.ferramentas.processo import executar
from orquestrador.raiz import (
    ARVORE_DE_FONTES,
    CONFIG_PADRAO,
    DIR_PROMPTS_PADRAO,
    RAIZ_PROJETO,
)

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


def _diagnosticar_graphify(config: Config) -> ItemDeDiagnostico:
    """Presença e versão do extrator.

    Não há mais versão "fixada" para conferir. Enquanto quem chamava o Graphify era
    o `qa-reindex.mjs`, ele exigia igualdade exata com um `manifest.json` de outra
    skill, e o `doctor` reproduzia essa busca para não aprovar o que aquele script
    recusaria. Hoje o Graphify é dependência declarada deste pacote: quem fixa a
    versão é o `pyproject.toml`, e o resolvedor do instalador já a garantiu antes
    de o `doctor` existir. Procurar um manifesto externo só produziria o AVISO
    permanente de "manifesto não encontrado" em toda máquina de cliente.
    """
    resposta = _saida_de_versao(config.execucao.graphify, config)
    if isinstance(resposta, ItemDeDiagnostico):
        return ItemDeDiagnostico(
            "Graphify",
            Veredito.FALHOU,
            resposta.detalhe,
            "o Bloco 0 indexa o backend com ele; sem ele não há grafo. Ele vem junto "
            "com este pacote: reinstale com `pip install orquestrador-testes-api`, ou "
            "aponte [execucao].graphify para o executável.",
        )
    atual = _texto_da_versao(resposta)
    return ItemDeDiagnostico("Graphify", Veredito.OK, atual or resposta.strip()[:40])


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
            "Diagnostica o ambiente: Python, Graphify, prompts, projeto de testes, "
            "backend e chave do provedor. Cada item com veredito e o que fazer."
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
