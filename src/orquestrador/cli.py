"""Entrypoint de linha de comando.

    python -m orquestrador --dry-run --recurso pedidos
    python -m orquestrador --recurso pedidos --config config.toml

Só argumentos, montagem da execução e apresentação: os loops de controle vivem em
`pipeline.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from orquestrador.config import Config
from orquestrador.contratos import Recurso
from orquestrador.excecoes import ErroDeConfiguracao, ErroDeFerramenta
from orquestrador.observabilidade import tabelas
from orquestrador.observabilidade.registro import (
    Registro,
    configurar_console,
    diretorio_de_execucao,
)
from orquestrador.pipeline import NAO_EXECUTADO, Pipeline, ResultadoDoRecurso
from orquestrador.raiz import ARQUIVO_ENV, DIR_FIXTURES, RAIZ_PROJETO
from orquestrador.simulacao import Roteiros, preparar_sandbox

# Códigos de saída. `2` cobre tudo que é erro do operador ou indisponibilidade da
# ferramenta — configuração inválida, invocação impossível, comando que ainda não
# existe. O que importa é não ser 0: em CI, 0 é indistinguível de trabalho feito.
SUCESSO = 0
FALHA_DE_GATE = 1
ERRO_DE_USO = 2


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
        prog="python -m orquestrador",
        description="Orquestrador multi-agente de testes de API (skill qa-api).",
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
    # As duas abaixo continuam reconhecidas pelo argparse para não quebrar script
    # existente, e as duas recusam a execução. Ver `recusar_indisponiveis`.
    analisador.add_argument(
        "--auditor",
        action="store_true",
        help="RECUSADO enquanto o auditor semântico for stub: encerra com erro.",
    )
    analisador.add_argument(
        "--remover-reprovados",
        action="store_true",
        help=(
            "RECUSADO: sem diário de propriedade não há como saber o que é nosso. "
            "Os artefatos reprovados são MANTIDOS — é o que se inspeciona para "
            "entender a falha."
        ),
    )
    return analisador.parse_args(argv)


def recusar_indisponiveis(args: argparse.Namespace, console: Console) -> int | None:
    """Recusa, antes de qualquer trabalho, as flags que hoje mentiriam.

    `--auditor` encerrava com código 0 imprimindo a descrição do stub. Código 0 é a
    frase "auditoria feita" na única linguagem que a CI lê; enquanto o auditor não
    existir, a resposta honesta é indisponibilidade.

    `--remover-reprovados` chamava `unlink()` em toda a lista de artefatos
    reprovados, sem distinguir o que **nós** criamos do que já era do consumidor.
    Essa distinção não existe hoje — ela é o diário de propriedade da Etapa 2 — e
    apagar arquivo alheio é o único erro deste projeto que não tem volta.

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
    if args.remover_reprovados:
        console.print(
            "[red]--remover-reprovados está desabilitado.[/red] A remoção apagava toda a "
            "lista de artefatos reprovados sem distinguir arquivo criado por esta "
            "execução de arquivo preexistente do seu projeto.\n"
            "A flag volta quando existir o diário de propriedade (Etapa 2), que registra "
            "por arquivo se ele foi criado, modificado ou preexistente — aí a remoção fica "
            "restrita ao que criamos. Até lá, apague à mão o que a lista final apontar."
        )
        return ERRO_DE_USO
    return None


def avisar_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Diz, no fim, o que ficou inválido em disco e por quê."""
    com_lixo = [resultado for resultado in resultados if resultado.arquivos_reprovados]
    if not com_lixo:
        return
    registro.aviso(
        "Artefatos deixados em estado REPROVADO no projeto de testes "
        "(mantidos de propósito — a remoção automática está desabilitada; "
        "apague à mão o que não quiser guardar):"
    )
    for resultado in com_lixo:
        codigos = ", ".join(resultado.codigos_remanescentes) or "(sem código)"
        registro.info(f"  {resultado.recurso} [{codigos}]")
        for caminho in resultado.arquivos_reprovados:
            registro.info(f"    {caminho}")


def main(argv: list[str] | None = None) -> int:
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
        roteiros = Roteiros(DIR_FIXTURES / "roteiros")
        config = preparar_sandbox(config, dir_execucao / "sandbox")

    recursos_pedidos = args.recursos or (list(roteiros.recursos()) if roteiros else [])
    if not recursos_pedidos:
        console.print("[red]informe ao menos um --recurso.[/red]")
        return ERRO_DE_USO

    with Registro(dir_execucao / "execucao.jsonl", console) as registro:
        registro.info(f"log estruturado: {registro.caminho}")
        registro.evento(
            "execucao_iniciada",
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            config=str(config.origem),
            projeto_testes=config.caminhos.projeto_testes,
            backend=config.caminhos.backend,
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
        except (ErroDeFerramenta, ErroDeConfiguracao) as erro:
            registro.falha(f"erro de invocação do pipeline: {erro}")
            registro.evento("execucao_abortada", motivo=str(erro))
            return ERRO_DE_USO

        registro.titulo("Telemetria")
        console.print(tabelas.tabela_por_estagio(pipeline.telemetria))
        console.print(tabelas.tabela_por_recurso(pipeline.telemetria))
        console.print(tabelas.tabela_entrada_por_tentativa(pipeline.telemetria))
        # Só o mapeador tem tools; sem elas a tabela seria uma moldura vazia.
        if pipeline.telemetria.tools:
            console.print(tabelas.tabela_de_tools(pipeline.telemetria))
        registro.evento("telemetria", **pipeline.telemetria.resumo_para_log())

        registro.titulo("Resumo")
        for resultado in resultados:
            marca = "[green]OK[/green]" if resultado.sucesso else "[red]FALHOU[/red]"
            console.print(
                f"{marca} {resultado.recurso}: "
                f"mapeador {resultado.tentativas_mapeador} tentativa(s), "
                f"executor {resultado.tentativas_executor} tentativa(s)"
                + (f" — {resultado.motivo}" if resultado.motivo else "")
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
        registro.evento(
            "execucao_concluida",
            sucesso=interrupcao is None and all(resultado.sucesso for resultado in resultados),
            interrompida=interrupcao is not None,
            recursos_nao_executados=(interrupcao.recursos_nao_executados if interrupcao else []),
            resultados=[
                {
                    "recurso": resultado.recurso,
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
        # passou; aqui o pipeline não conseguiu emitir veredito nenhum.
        return ERRO_DE_USO
    return SUCESSO if all(r.sucesso for r in resultados) else FALHA_DE_GATE


if __name__ == "__main__":
    sys.exit(main())
