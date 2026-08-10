"""`orquestrador doctor` — o ambiente que uma execução real exige, item a item.

Fica com ~500 linhas de propósito. Ele muda por **um** motivo — o que uma
execução real precisa ter por perto —, e cerca de 60% do arquivo é o texto de
`conserto` de cada diagnóstico, que é editorial e coeso. Separar "as checagens"
de "a apresentação" separaria cada veredito da frase que diz o que fazer com ele,
que é a parte útil."""

from __future__ import annotations

import argparse
import json
import re
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
    return [presenca]


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
