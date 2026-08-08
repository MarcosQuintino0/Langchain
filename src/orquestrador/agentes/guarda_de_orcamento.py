"""A guarda que os dois agentes fazem antes de chamar o modelo.

Três peças, três donos, e é a separação que faz a regra caber em cada um deles:

* `dominio/orcamento.py` **decide** — compara gasto com teto, sem saber de nada;
* `observabilidade/telemetria.py` **mede** — já tem os registros, e devolve o
  `Consumo` dos dois escopos;
* este módulo **interrompe** — é a única linha que levanta.

A alternativa óbvia era pôr a checagem dentro da `Telemetria`, que é onde os
números estão. Ela está errada por uma razão que o `AGENTS.md` fixa: observabilidade
nunca decide fluxo. Uma telemetria que levanta deixa de ser instrumento e vira
gate — e um gate que ninguém procura ali é o pior tipo de gate.

Mora em `agentes/` e não em `aplicacao/` porque a chamada ao modelo é do agente. O
ciclo de reparo não tem como interceptá-la: o mini-loop de schema do mapeador dá
várias voltas de modelo **dentro** de uma tentativa, e uma guarda no ciclo deixaria
esse laço inteiro passar batido — que é exatamente o laço mais caro do pipeline.
"""

from __future__ import annotations

from orquestrador.config import Config
from orquestrador.excecoes import OrcamentoEsgotado
from orquestrador.observabilidade.telemetria import Telemetria


def exigir_folga(config: Config, telemetria: Telemetria, *, estagio: str, recurso: str) -> None:
    """Levanta `OrcamentoEsgotado` se o teto já foi alcançado. Sem teto, não faz nada.

    Chamada **antes** de cada volta de modelo, inclusive dentro do mini-loop de
    schema. Não promete que o teto não será ultrapassado — o custo de uma chamada só
    se conhece depois dela. Promete que nenhuma chamada nova começa depois de ele ter
    sido alcançado, que é o que dá para prometer.
    """
    if not config.orcamento.configurado:
        return

    motivo = config.orcamento.motivo_para_parar(
        execucao=telemetria.consumo(),
        recurso=telemetria.consumo(recurso),
    )
    if motivo is None:
        return

    raise OrcamentoEsgotado(
        f"{motivo}. A execução parou em {estagio}/{recurso} antes de chamar o modelo "
        "de novo.\n"
        "Isto não é falha: é o teto de [orcamento] sendo obedecido. Decida se o "
        "trabalho vale mais do que ele autoriza — e, se valer, suba o teto em vez de "
        "removê-lo, para o próximo estouro continuar sendo visível.\n"
        "Para estimar o tamanho antes de gastar: `orquestrador --estimar`."
    )
