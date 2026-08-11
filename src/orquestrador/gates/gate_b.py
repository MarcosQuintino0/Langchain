"""Gate B — verificação da implementação (determinístico).

Duas checagens, depois do desacoplamento da skill (2026-08-10):

1. **Formatadores** (prettier/eslint), quando configurados — são do projeto do
   cliente, não de skill nenhuma, e nunca reprovam o artefato: instalar
   toolchain não é trabalho que o executor faça reescrevendo teste.
2. **Limpeza gerada** (`gates/limpeza.py`) — a receita determinística virou
   código de verdade, ou o executor só disse que sim?

O que saiu com a skill: a reconciliação de cobertura por categoria e por campo
(`QAAPI-025`, `QAORQ-030` via `qa-cobertura.mjs`) e as checagens de padrão de
código. **É a maior perda do desacoplamento** e está registrada como pendência
em `docs/arquitetura/pendencias.md`: enquanto ela não voltar, o Gate B não
responde "planejei e não entreguei" — quem responde por cobertura hoje é o
QAORQ-050/051/052 no planejador, que é sobre o PLANO, não sobre o código.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.config import Config
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import ResultadoGate, Violacao
from orquestrador.excecoes import ExecutavelAusente
from orquestrador.ferramentas.processo import executar as rodar_processo
from orquestrador.gates.limpeza import conferir_limpeza
from orquestrador.gates.padrao_cypress import conferir_padrao
from orquestrador.gates.saidas import violacoes_do_eslint

NOME = "gate_b"

# Saída de formatador é verbosa; o delta só precisa do bastante para localizar.
LIMITE_DE_SAIDA = 4000


def executar(
    config: Config,
    recurso: Recurso,
    *,
    dir_recurso: Path,
    dir_schemas: Path,
    manifesto: Manifesto | None = None,
    out_cobertura: Path | None = None,
    inventario: Inventario | None = None,
    dossie: DossieDoRecurso | None = None,
) -> ResultadoGate:
    """As cinco checagens do Gate B sobre o **staging** da execução.

    Ver `gate_a.executar` sobre por que `dir_recurso` e `dir_schemas` não têm
    padrão.
    """
    del dir_schemas, manifesto, out_cobertura, recurso  # ver a docstring: saíram com a skill
    partes = [
        _formatador(config, "prettier", "QAORQ-020", config.execucao.prettier, dir_recurso),
        _formatador(config, "eslint", "QAORQ-021", config.execucao.eslint, dir_recurso),
        conferir_limpeza(_modulos_de_suporte(dir_recurso), inventario, dossie),
        conferir_padrao(_codigo_do_recurso(dir_recurso)),
    ]
    combinado = ResultadoGate.combinar([parte for parte in partes if parte], gate=NOME)
    # Checagem que não rodou não vira delta: `exigir_veredito` interrompe o recurso
    # em vez de devolver ao executor uma lista que ele não tem como satisfazer.
    return combinado.exigir_veredito()


def _codigo_do_recurso(dir_recurso: Path) -> dict[str, str]:
    """Todo o JavaScript do recurso — specs e `_support/` —, por caminho relativo.

    A norma de código vale nos dois: `_support/asserts.js` é justamente onde a
    mensagem de asserção mais importa (quem lê o spec não vê aquela linha) e foi
    onde ela mais faltou na suíte publicada — 0 de 20.
    """
    if not dir_recurso.is_dir():
        return {}
    return {
        arquivo.relative_to(dir_recurso).as_posix(): arquivo.read_text(
            encoding="utf-8", errors="replace"
        )
        for arquivo in sorted(dir_recurso.rglob("*.js"))
    }


def _modulos_de_suporte(dir_recurso: Path) -> dict[str, str]:
    """Os `_support/*.js` do staging, como texto. Ausente vira dicionário vazio.

    Dicionário vazio não é aprovação disfarçada: `conferir_limpeza` trata isso
    como "não há DELETE em lugar nenhum", que é exatamente o que a ausência do
    `_support/` significa para a limpeza.
    """
    raiz = dir_recurso / "_support"
    if not raiz.is_dir():
        return {}
    return {
        arquivo.name: arquivo.read_text(encoding="utf-8", errors="replace")
        for arquivo in sorted(raiz.glob("*.js"))
    }


def _formatador(
    config: Config,
    nome: str,
    codigo: str,
    comando: list[str],
    dir_recurso: Path,
) -> ResultadoGate | None:
    """Roda prettier/eslint sobre o diretório do recurso. Lista vazia = desligado."""
    if not comando:
        return None

    alvo = _relativo_ao_projeto(dir_recurso, config.caminhos.projeto_testes)
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
        return ResultadoGate.aprovado_por(
            avisos=[Violacao(codigo="QAORQ-022", mensagem=f"{nome} indisponível: {erro}")],
            gate=NOME,
        )

    if saida.codigo == 0:
        return ResultadoGate.aprovado_por(saida_bruta=saida.texto, gate=NOME)

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
    return ResultadoGate.reprovado_por(violacoes, saida_bruta=saida.texto, gate=NOME)


def _relativo_ao_projeto(alvo: Path, projeto: Path) -> str:
    try:
        return alvo.resolve().relative_to(projeto.resolve()).as_posix()
    except ValueError:
        return str(alvo)
