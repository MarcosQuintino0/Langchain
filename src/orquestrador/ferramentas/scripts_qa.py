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

    `schemas` é o que permite validar um diretório de staging: sem ele o
    `campos/schema.mjs` **sobe** a partir do diretório recebido procurando
    `cypress/fixtures/schemas`, e acharia o do projeto do consumidor — isto é, o
    denominador do artefato que ainda não foi publicado. A flag só existe na forma
    com sinal de igual (`--schemas=<dir>`); o parser da skill não aceita o valor
    como argumento seguinte.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.script = config.caminhos.script("validar-suite-gerada.mjs")

    def executar(
        self,
        recurso: Path,
        flags: list[str] | None = None,
        *,
        schemas: Path | None = None,
    ) -> SaidaProcesso:
        argumentos = [str(recurso), *(flags or [])]
        if schemas is not None and not any(a.startswith("--schemas=") for a in argumentos):
            argumentos.append(f"--schemas={schemas}")
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
        schemas: Path | None = None,
        json: bool = True,
    ) -> SaidaProcesso:
        argumentos = [str(dir_specs)]
        if report:
            argumentos += ["--report", str(report)]
        if out:
            argumentos += ["--out", str(out)]
        # Mesmo motivo do `--schemas` do Validador: sem ele o denominador de campos
        # é procurado subindo a partir de `dir_specs`, o que aponta para o projeto
        # publicado em vez do staging que está sendo medido.
        if schemas is not None:
            argumentos += ["--schemas", str(schemas)]
        if json:
            argumentos.append("--json")
        return executar_node(
            self.script,
            argumentos,
            node=self.config.execucao.node,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )
