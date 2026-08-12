"""Gate B — verificação da implementação (determinístico).

Quatro checagens:

1. **Formatadores** (prettier/eslint), quando configurados — são do projeto do
   cliente, não de skill nenhuma, e nunca reprovam o artefato: instalar
   toolchain não é trabalho que o executor faça reescrevendo teste.
2. **Limpeza gerada** (`gates/limpeza.py`) — a receita determinística virou
   código de verdade, ou o executor só disse que sim?
3. **Norma de código** (`gates/padrao_cypress.py`) — o pedaço de
   `prompts/padrao-de-codigo-cypress.md` que um script consegue provar.
4. **Cobertura** (`gates/cobertura.py`) — tudo que o gabarito prometeu virou
   `it`, e todo campo do schema de entrada foi exercitado?

A quarta é a que fechou o buraco do desacoplamento da skill. Entre 2026-08-10 e
2026-08-12 este gate não respondia "planejei e não entreguei": quem falava de
cobertura era o `QAORQ-050/051/052` no planejador, que mede o PLANO, e não o
código. Medido na volta, sobre a suíte publicada de `customers`: 33 de 39
categorias prometidas tinham teste — as outras 6 ninguém tinha visto faltar.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orquestrador.config import Config
from orquestrador.dominio.artefatos import SUFIXO_SCHEMA
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import ResultadoGate, Violacao
from orquestrador.excecoes import ExecutavelAusente
from orquestrador.ferramentas.processo import executar as rodar_processo
from orquestrador.gates.cobertura import conferir_cobertura
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
    del out_cobertura  # ver a docstring: era o relatório do script da skill
    codigo = _codigo_do_recurso(dir_recurso)
    partes = [
        _formatador(config, "prettier", "QAORQ-020", config.execucao.prettier, dir_recurso),
        _formatador(config, "eslint", "QAORQ-021", config.execucao.eslint, dir_recurso),
        conferir_limpeza(_modulos_de_suporte(dir_recurso), inventario, dossie),
        conferir_padrao(codigo),
        conferir_cobertura(
            manifesto,
            {nome: fonte for nome, fonte in codigo.items() if nome.endswith(".cy.js")},
            _schemas_do_recurso(dir_schemas, recurso),
        ),
    ]
    combinado = ResultadoGate.combinar([parte for parte in partes if parte], gate=NOME)
    # Checagem que não rodou não vira delta: `exigir_veredito` interrompe o recurso
    # em vez de devolver ao executor uma lista que ele não tem como satisfazer.
    return combinado.exigir_veredito()


def _schemas_do_recurso(dir_schemas: Path, recurso: Recurso) -> dict[str, dict[str, Any]]:
    """Os schemas de entrada do recurso, indexados pela referência do gabarito.

    A chave é o nome curto (`create`, `patch`) porque é assim que o
    `schemaEntrada` do gabarito os cita — resolver o caminho aqui e comparar por
    caminho faria a conta depender do layout, que já tem dono em
    `dominio/artefatos.py`.
    """
    raiz = dir_schemas / recurso.nome
    if not raiz.is_dir():
        return {}
    schemas: dict[str, dict[str, Any]] = {}
    for arquivo in sorted(raiz.glob(f"*{SUFIXO_SCHEMA}")):
        try:
            conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Schema ilegível não vira lacuna de cobertura: quem responde por
            # schema quebrado é o Gate A, sobre o artefato do mapeador.
            continue
        if isinstance(conteudo, dict):
            schemas[arquivo.name[: -len(SUFIXO_SCHEMA)]] = conteudo
    return schemas


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
