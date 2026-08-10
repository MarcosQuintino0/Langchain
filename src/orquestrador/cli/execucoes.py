"""Apresentação dos relatórios locais de observabilidade; nenhuma coordenação."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from rich.console import Console

from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, SUCESSO
from orquestrador.ferramentas.retencao_de_execucoes import aplicar_limpeza, planejar_limpeza
from orquestrador.observabilidade.leitura import ler_execucao
from orquestrador.observabilidade.relatorios import (
    comparar_execucoes,
    historico,
    listar_execucoes,
    resumir_execucao,
)

__all__ = ["comando_execucoes"]


def _base_comum() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument(
        "--base",
        type=Path,
        default=Path(".execucoes"),
        help="diretório que contém as execuções (padrão: .execucoes)",
    )
    comum.add_argument("--json", action="store_true", help="imprime JSON para automação")
    return comum


def _analisador() -> argparse.ArgumentParser:
    analisador = argparse.ArgumentParser(
        prog="orquestrador execucoes",
        description="Consulta os JSONL locais sem reexecutar nem reescrever nada.",
    )
    sub = analisador.add_subparsers(dest="acao", required=True)
    comum = _base_comum()
    listar = sub.add_parser("listar", parents=[comum], help="lista execuções")
    listar.add_argument("--historico", action="store_true", help="agrega tendência real/dry-run")
    mostrar = sub.add_parser("mostrar", parents=[comum], help="mostra uma execução")
    mostrar.add_argument("run_id")
    validar = sub.add_parser("validar", parents=[comum], help="valida contratos e correlação")
    grupo = validar.add_mutually_exclusive_group(required=True)
    grupo.add_argument("run_id", nargs="?")
    grupo.add_argument("--todas", action="store_true")
    comparar = sub.add_parser("comparar", parents=[comum], help="compara duas execuções")
    comparar.add_argument("run_a")
    comparar.add_argument("run_b")
    limpar = sub.add_parser("limpar", parents=[comum], help="prévia de retenção segura")
    limpar.add_argument("--antes-de", type=int, required=True, metavar="DIAS")
    limpar.add_argument(
        "--aplicar",
        action="store_true",
        help="confirma a remoção; sem esta opção o comando só mostra a prévia",
    )
    return analisador


def _resolver(base: Path, run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("run_id deve ser somente o nome do diretório")
    raiz = base.resolve()
    alvo = (raiz / run_id).resolve()
    if alvo.parent != raiz or not alvo.is_dir():
        raise ValueError(f"execução não encontrada: {run_id}")
    return alvo


def _imprimir(console: Console, dados: Any, *, como_json: bool) -> None:
    if como_json:
        console.print_json(data=dados)
    elif isinstance(dados, list):
        for item in cast(list[dict[str, Any]], dados):
            console.print(
                f"{item['run_id']}  {item['terminal'] or 'SEM TERMINAL'}  "
                f"{item['duracao_s']:.3f}s  {item['tokens']} tokens  "
                f"{item['problemas']} problema(s)"
            )
    else:
        console.print_json(data=dados)


def comando_execucoes(argv: list[str], console: Console) -> int:
    args = _analisador().parse_args(argv)
    base: Path = args.base
    try:
        if args.acao == "listar":
            resumos = listar_execucoes(base)
            dados: Any = (
                historico(resumos) if args.historico else [item.para_json() for item in resumos]
            )
            _imprimir(console, dados, como_json=args.json)
            return SUCESSO
        if args.acao == "mostrar":
            resumo = resumir_execucao(_resolver(base, args.run_id)).para_json()
            _imprimir(console, resumo, como_json=args.json)
            return SUCESSO
        if args.acao == "comparar":
            comparacao = comparar_execucoes(
                resumir_execucao(_resolver(base, args.run_a)),
                resumir_execucao(_resolver(base, args.run_b)),
            )
            _imprimir(console, comparacao, como_json=args.json)
            return SUCESSO
        if args.acao == "limpar":
            plano = planejar_limpeza(base, antes_de_dias=args.antes_de)
            resultado = aplicar_limpeza(plano) if args.aplicar else None
            dados = {
                "modo": "aplicado" if args.aplicar else "previa",
                "candidatos": [str(item) for item in plano.candidatos],
                "removidos": [str(item) for item in resultado.removidos] if resultado else [],
                "recusados": [
                    {"caminho": str(item.caminho), "motivo": item.motivo}
                    for item in (resultado.recusados if resultado else plano.recusados)
                ],
            }
            _imprimir(console, dados, como_json=args.json)
            return SUCESSO

        alvos = (
            [Path(item.caminho) for item in listar_execucoes(base)]
            if args.todas
            else [_resolver(base, args.run_id)]
        )
        leituras = [ler_execucao(alvo) for alvo in alvos]
        problemas = [
            (leitura.caminho.parent.name, problema)
            for leitura in leituras
            for problema in leitura.problemas
            if problema.severidade == "erro"
        ]
        if args.json:
            console.print_json(
                data={
                    "validas": len(leituras) - len({run for run, _ in problemas}),
                    "problemas": [
                        {
                            "run_id": run,
                            "codigo": problema.codigo,
                            "linha": problema.linha,
                            "mensagem": problema.mensagem,
                        }
                        for run, problema in problemas
                    ],
                }
            )
        elif problemas:
            for run, problema in problemas:
                console.print(f"[red]{run}[/red] {problema.codigo}: {problema.mensagem}")
        else:
            console.print(f"{len(leituras)} execução(ões) válida(s)")
        return ERRO_DE_USO if problemas else SUCESSO
    except (OSError, ValueError) as erro:
        console.print(f"[red]erro:[/red] {erro}")
        return ERRO_DE_USO
