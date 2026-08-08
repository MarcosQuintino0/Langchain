"""Gate B — verificação da implementação (determinístico).

Prettier + ESLint + `validar-suite-gerada.mjs <recurso> --json`.

O validador reprova por código: `QAAPI-025` (campo do schema sem teste `@campo`),
`QAAPI-026` (exceção que fecha categoria por desconhecimento), `QAAPI-027`
(endpoint de escrita sem superfície de entrada declarada, com `--exigir-campos`),
`QAAPI-032` (profundidade incoerente com o registro de handlers), entre outros.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.config import Config
from orquestrador.contratos import Manifesto, Recurso, ResultadoGate, Violacao
from orquestrador.ferramentas.processo import ExecutavelAusente, executar as rodar_processo
from orquestrador.ferramentas.scripts_qa import Validador
from orquestrador.gates import cobertura as gate_cobertura
from orquestrador.gates.parser import resultado_do_validador, violacoes_do_eslint

NOME = "gate_b"

# Saída de formatador é verbosa; o delta só precisa do bastante para localizar.
LIMITE_DE_SAIDA = 4000


def executar(
    config: Config,
    recurso: Recurso,
    *,
    manifesto: Manifesto | None = None,
    out_cobertura: Path | None = None,
) -> ResultadoGate:
    partes = [
        _formatador(config, "prettier", "QAORQ-020", config.execucao.prettier, recurso),
        _formatador(config, "eslint", "QAORQ-021", config.execucao.eslint, recurso),
        _validador(config, recurso),
        # A terceira checagem responde por "planejei e não entreguei", que o
        # validador da skill não cobre. Ver gates/cobertura.py.
        gate_cobertura.executar(
            config, recurso, manifesto=manifesto, gate=NOME, out=out_cobertura
        ),
    ]
    return ResultadoGate.combinar([parte for parte in partes if parte], gate=NOME)


def _validador(config: Config, recurso: Recurso) -> ResultadoGate:
    saida = Validador(config).executar(recurso.caminho_testes, list(config.gate("b").flags))
    return resultado_do_validador(saida, gate=NOME)


def _formatador(
    config: Config,
    nome: str,
    codigo: str,
    comando: list[str],
    recurso: Recurso,
) -> ResultadoGate | None:
    """Roda prettier/eslint sobre o diretório do recurso. Lista vazia = desligado."""
    if not comando:
        return None

    alvo = _relativo_ao_projeto(recurso.caminho_testes, config.caminhos.projeto_testes)
    try:
        saida = rodar_processo(
            [*comando, alvo],
            cwd=config.caminhos.projeto_testes,
            timeout_s=config.execucao.timeout_s,
        )
    except ExecutavelAusente as erro:
        problema = Violacao(codigo="QAORQ-022", mensagem=f"{nome} indisponível: {erro}")
        if config.execucao.exigir_formatadores:
            return ResultadoGate(aprovado=False, violacoes=[problema], gate=NOME)
        return ResultadoGate(aprovado=True, avisos=[problema], gate=NOME)

    if saida.codigo == 0:
        return ResultadoGate(aprovado=True, saida_bruta=saida.texto, gate=NOME)

    violacoes = violacoes_do_eslint(saida.stdout) if nome == "eslint" else []
    if not violacoes:
        violacoes = [
            Violacao(
                codigo=codigo,
                mensagem=(
                    f"{nome} reprovou (código {saida.codigo}): "
                    f"{saida.texto[:LIMITE_DE_SAIDA] or '(sem saída)'}"
                ),
            )
        ]
    return ResultadoGate(
        aprovado=False, violacoes=violacoes, saida_bruta=saida.texto, gate=NOME
    )


def _relativo_ao_projeto(alvo: Path, projeto: Path) -> str:
    try:
        return alvo.resolve().relative_to(projeto.resolve()).as_posix()
    except ValueError:
        return str(alvo)
