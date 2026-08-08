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

from rich.console import Console

from orquestrador.agentes import auditor as agente_auditor
from orquestrador.config import Config, ErroDeConfiguracao
from orquestrador.contratos import Recurso
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.observabilidade.registro import (
    Registro,
    configurar_console,
    diretorio_de_execucao,
)
from orquestrador.pipeline import Pipeline, ResultadoDoRecurso
from orquestrador.raiz import ARQUIVO_ENV, DIR_FIXTURES, RAIZ_PROJETO
from orquestrador.simulacao import Roteiros, preparar_sandbox


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
        type=int,
        default=None,
        help="sobrescreve max_tentativas de todos os gates.",
    )
    analisador.add_argument(
        "--rodar-cypress",
        action="store_true",
        help="executa o Cypress no Bloco 3 (por padrão é pulado).",
    )
    analisador.add_argument(
        "--auditor",
        action="store_true",
        help="mostra o veredito do auditor semântico (stub da Fase 1) e sai.",
    )
    analisador.add_argument(
        "--remover-reprovados",
        action="store_true",
        help=(
            "apaga, ao final, os artefatos que ficaram em estado reprovado. "
            "Por padrão eles são MANTIDOS: é o que se inspeciona para entender a falha."
        ),
    )
    return analisador.parse_args(argv)


def remover_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Apaga os artefatos reprovados. Só é chamada com --remover-reprovados."""
    for resultado in resultados:
        for caminho in resultado.arquivos_reprovados:
            try:
                Path(caminho).unlink(missing_ok=True)
                registro.info(f"    removido: {caminho}")
            except OSError as erro:
                registro.aviso(f"    não foi possível remover {caminho}: {erro}")
        if resultado.arquivos_reprovados:
            registro.evento(
                "artefatos_removidos",
                recurso=resultado.recurso,
                arquivos=[str(c) for c in resultado.arquivos_reprovados],
            )


def avisar_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Diz, no fim, o que ficou inválido em disco e por quê."""
    com_lixo = [resultado for resultado in resultados if resultado.arquivos_reprovados]
    if not com_lixo:
        return
    registro.aviso(
        "Artefatos deixados em estado REPROVADO no projeto de testes "
        "(mantidos de propósito — use --remover-reprovados para apagá-los):"
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

    try:
        config = Config.carregar(args.config)
    except ErroDeConfiguracao as erro:
        console.print(f"[red]configuração inválida:[/red] {erro}")
        return 2

    if not args.dry_run:
        from dotenv import load_dotenv

        load_dotenv(ARQUIVO_ENV)

    if args.max_tentativas:
        for gate in config.gates.values():
            gate.max_tentativas = args.max_tentativas

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
        return 2

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

        if args.auditor:
            recurso = montar_recursos(config, recursos_pedidos)[0]
            registro.info(
                agente_auditor.descrever(
                    config.caminhos.backend,
                    recurso.manifesto_path,
                    sorted(recurso.caminho_testes.glob("**/*.cy.js")),
                )
            )
            return 0

        try:
            config.validar_caminhos(exigir_backend=True)
        except ErroDeConfiguracao as erro:
            registro.falha(str(erro))
            return 2

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
            return 2

        registro.titulo("Telemetria")
        console.print(pipeline.telemetria.tabela_por_estagio())
        console.print(pipeline.telemetria.tabela_por_recurso())
        console.print(pipeline.telemetria.tabela_entrada_por_tentativa())
        # Só o mapeador tem tools; sem elas a tabela seria uma moldura vazia.
        if pipeline.telemetria.tools:
            console.print(pipeline.telemetria.tabela_de_tools())
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
        avisar_reprovados(resultados, registro)
        if args.remover_reprovados:
            remover_reprovados(resultados, registro)
        registro.evento(
            "execucao_concluida",
            sucesso=all(resultado.sucesso for resultado in resultados),
            resultados=[
                {
                    "recurso": resultado.recurso,
                    "sucesso": resultado.sucesso,
                    "tentativas_mapeador": resultado.tentativas_mapeador,
                    "tentativas_executor": resultado.tentativas_executor,
                    "motivo": resultado.motivo,
                }
                for resultado in resultados
            ],
        )
        registro.info(f"artefatos e log desta execução: {dir_execucao}")

    return 0 if all(resultado.sucesso for resultado in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
