"""Reprova se algum teste de integração foi pulado — ou se nenhum rodou.

Os testes de integração pulam sozinhos quando um pré-requisito falta (hoje só o
`uv`): comportamento certo na máquina de quem desenvolve, e desastroso na CI,
onde um pulo silencioso vira um job verde que não executou nada. O job de
integração garante esse pré-requisito, então pulo ali é anomalia.

Isto não é hipotético: os testes `e2e` deste repositório pularam por meses
procurando um checkout da skill `qa-api` que a configuração já não declarava, e
a suíte continuou verde o tempo todo.

Este script lê o JUnit XML do pytest e falha em dois casos:

* qualquer `<skipped>` — as dependências estavam garantidas, então pulo é anomalia;
* zero testes executados — seleção vazia (arquivo renomeado, marker trocado)
  também produz verde, e é o mesmo defeito com outra origem.

Uso: python .github/scripts/checar_pulos.py <arquivo-junit.xml>
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"uso: {Path(argv[0]).name} <arquivo-junit.xml>", file=sys.stderr)
        return 2

    relatorio = Path(argv[1])
    if not relatorio.is_file():
        print(
            f"::error::relatório {relatorio} não existe — o pytest não chegou a rodar?",
            file=sys.stderr,
        )
        return 2

    # O XML é gerado pelo próprio pytest, no passo anterior deste mesmo job:
    # não é entrada de terceiro.
    raiz = ET.parse(relatorio).getroot()  # noqa: S314
    casos = list(raiz.iter("testcase"))

    pulados = [
        (caso.get("classname", "?"), caso.get("name", "?"), pulo.get("message", ""))
        for caso in casos
        for pulo in caso.iter("skipped")
    ]

    if not casos:
        print(
            "::error::nenhum teste de integração foi executado. "
            "Seleção vazia conta como falha: o job existe para executá-los.",
            file=sys.stderr,
        )
        return 1

    if pulados:
        for classe, nome, motivo in pulados:
            print(f"::error::teste de integração pulado: {classe}::{nome} — {motivo}")
        print(
            f"::error::{len(pulados)} de {len(casos)} testes de integração foram pulados. "
            "Este job garante os pré-requisitos, então pulo aqui significa "
            "pré-requisito quebrado, não ambiente incompleto.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(casos)} testes de integração executados, nenhum pulado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
