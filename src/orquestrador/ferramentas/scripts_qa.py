"""Wrappers dos scripts `.mjs` da skill `qa-api`.

Estes scripts somam ~5.000 linhas e são o auditor determinístico do pipeline. A
lógica deles **não** é reimplementada aqui: o Python invoca, captura os dois fluxos
de saída e parseia o JSON.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.config import Config
from orquestrador.ferramentas.processo import SaidaProcesso, executar_node


class Validador:
    """`validar-suite-gerada.mjs <diretorio-do-recurso> [opcoes] --json`.

    Códigos de saída: 0 = válido, 1 = inválido, 2 = erro de uso.
    Fluxo: stdout quando aprova, stderr quando reprova — os dois são capturados.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.script = config.caminhos.script("validar-suite-gerada.mjs")

    def executar(self, recurso: Path, flags: list[str] | None = None) -> SaidaProcesso:
        argumentos = [str(recurso), *(flags or [])]
        if "--json" not in argumentos:
            argumentos.append("--json")
        return executar_node(
            self.script,
            argumentos,
            node=self.config.execucao.node,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )


class Cobertura:
    """`qa-cobertura.mjs <dirDeSpecs> [--report <r>] [--out <o>] [--json]`.

    É relatório, não gate: sai com código 0 mesmo quando não consegue gerar (a
    exceção é o uso com dois diretórios, que sai 1). Quem chama precisa olhar o
    stderr, não só o código de saída.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.script = config.caminhos.script("qa-cobertura.mjs")

    def executar(
        self,
        dir_specs: Path,
        *,
        report: Path | None = None,
        out: Path | None = None,
        json: bool = True,
    ) -> SaidaProcesso:
        argumentos = [str(dir_specs)]
        if report:
            argumentos += ["--report", str(report)]
        if out:
            argumentos += ["--out", str(out)]
        if json:
            argumentos.append("--json")
        return executar_node(
            self.script,
            argumentos,
            node=self.config.execucao.node,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )
