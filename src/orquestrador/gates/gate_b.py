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
from orquestrador.excecoes import ExecutavelAusente
from orquestrador.ferramentas.processo import executar as rodar_processo
from orquestrador.ferramentas.scripts_qa import Validador
from orquestrador.gates import lacunas as gate_lacunas
from orquestrador.gates.saidas import resultado_do_validador, violacoes_do_eslint

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
        # validador da skill não cobre. Ver gates/lacunas.py.
        gate_lacunas.executar(config, recurso, manifesto=manifesto, gate=NOME, out=out_cobertura),
    ]
    combinado = ResultadoGate.combinar([parte for parte in partes if parte], gate=NOME)
    # Checagem que não rodou não vira delta: `exigir_veredito` interrompe o recurso
    # em vez de devolver ao executor uma lista que ele não tem como satisfazer.
    return combinado.exigir_veredito()


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
        if config.execucao.exigir_formatadores:
            # Exigido e ausente é falha de ambiente, não do artefato: o executor não
            # instala o `node_modules` do projeto, e um QAORQ-022 no delta gastaria
            # tentativa pedindo a ele que consertasse o PATH de quem o roda.
            return ResultadoGate.erro_da_ferramenta(
                f"{nome} está configurado em [execucao] e exigido em "
                f"exigir_formatadores, mas não foi encontrado: {erro}",
                gate=NOME,
            )
        return ResultadoGate(
            aprovado=True,
            avisos=[Violacao(codigo="QAORQ-022", mensagem=f"{nome} indisponível: {erro}")],
            gate=NOME,
        )

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
    return ResultadoGate(aprovado=False, violacoes=violacoes, saida_bruta=saida.texto, gate=NOME)


def _relativo_ao_projeto(alvo: Path, projeto: Path) -> str:
    try:
        return alvo.resolve().relative_to(projeto.resolve()).as_posix()
    except ValueError:
        return str(alvo)
