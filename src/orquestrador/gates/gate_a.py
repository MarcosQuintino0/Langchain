"""Gate A — verificação do plano (determinístico).

Duas checagens:

1. `validar-suite-gerada.mjs <recurso> --so-manifesto --json` — a contabilidade das
   12 categorias e as justificativas de `naoAplica`.
2. **Diff grafo × manifesto** — compara os handlers presentes no `graph.json` com
   os endpoints declarados no `cobertura.json`. **STUB na Fase 1.**

Por que a segunda checagem existe: o validador da skill enxerga apenas o projeto de
testes, nunca o backend — limite deliberado, documentado em
`skills/qa-api/scripts/cobertura/handlers.mjs:15`. Ele prova "entreguei o que
planejei", nunca "planejei tudo que existe". O diff é o que fecha esse elo.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.config import Config
from orquestrador.contratos import Inventario, Manifesto, Recurso, ResultadoGate, Violacao
from orquestrador.ferramentas.scripts_qa import Validador
from orquestrador.gates.parser import resultado_do_validador

NOME = "gate_a"


def executar(
    config: Config,
    recurso: Recurso,
    *,
    inventario: Inventario | None = None,
    manifesto: Manifesto | None = None,
) -> ResultadoGate:
    """Roda as duas checagens do Gate A sobre o artefato já em disco."""
    flags = list(config.gate("a").flags)
    if "--so-manifesto" not in flags:
        flags.insert(0, "--so-manifesto")

    saida = Validador(config).executar(recurso.caminho_testes, flags)
    manifesto_ok = resultado_do_validador(saida, gate=NOME)
    diff = diff_grafo_manifesto(
        graph=config.caminhos.graph_abs,
        inventario=inventario,
        manifesto=manifesto,
        recurso=recurso,
    )
    # `exigir_veredito` interrompe quando o validador não se comportou como o
    # contrato dele diz: sem veredito confiável não há o que mandar ao mapeador.
    return ResultadoGate.combinar([manifesto_ok, diff], gate=NOME).exigir_veredito()


def diff_grafo_manifesto(
    *,
    graph: Path,
    inventario: Inventario | None,
    manifesto: Manifesto | None,
    recurso: Recurso,
) -> ResultadoGate:
    """STUB (Fase 1) — compara os handlers do grafo com os endpoints do manifesto.

    Interface definitiva, implementação pendente. Quando implementado, deve:

    1. ler `graph.json` e extrair os nós de handler HTTP que pertencem ao recurso
       (método, rota completa depois de resolver prefixo/versão/router montado,
       arquivo e linha);
    2. reprovar com `QAORQ-002` cada endpoint que existe no grafo e não aparece em
       `manifesto.endpoints` — é exatamente o "planejei menos do que existe" que o
       validador da skill não consegue enxergar;
    3. reprovar com `QAORQ-003` o caminho inverso (endpoint declarado sem
       correspondente no grafo), que denuncia rota inventada;
    4. tratar `inventario.rotas_dinamicas_nao_resolvidas` como registro explícito de
       incerteza — não como ausência.

    TODO(Fase 2+): a implementação exige sondar o formato real do `graph.json`
    (nomes de nó, arestas e onde a rota HTTP aparece), que varia por extrator de
    linguagem. Enquanto isso o gate **não reprova**: emite um aviso para que a
    lacuna fique visível no log em vez de passar por checagem cumprida.
    """
    aviso = Violacao(
        codigo="QAORQ-001",
        mensagem=(
            "diff grafo × manifesto é stub da Fase 1: o Gate A ainda prova apenas "
            '"entreguei o que planejei", nunca "planejei tudo que existe" '
            f"(grafo {'presente' if graph.is_file() else 'AUSENTE'}; "
            f"{len(inventario.endpoints) if inventario else 0} endpoint(s) no inventário, "
            f"{len(manifesto.endpoints) if manifesto else 0} no manifesto)"
        ),
        arquivo=f"{recurso.nome}/_support/cobertura.json",
    )
    return ResultadoGate(aprovado=True, avisos=[aviso], gate=NOME)
