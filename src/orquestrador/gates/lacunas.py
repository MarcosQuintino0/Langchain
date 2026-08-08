"""Gate de lacuna — categoria planejada que não virou teste.

O módulo nomeia o que produz: **lacunas**. Chamava-se `cobertura.py`, colidindo
conceitualmente com a classe `Cobertura` de `ferramentas/scripts_qa.py` — que é o
adaptador do script, não o gate. Dois nomes iguais para papéis diferentes no mesmo
fluxo mandam quem lê conferir qual dos dois está em jogo a cada import.

O buraco que este módulo fecha
------------------------------
O `validar-suite-gerada.mjs` prova forma: manifesto bem contabilizado, specs-base
presentes, imports resolvidos, campo do schema com teste `@campo` ou exceção. Ele
**não** confere se cada categoria declarada em `cats` virou um `it`.

Quem sabe disso é o `qa-cobertura.mjs`, que classifica cada célula do cruzamento
endpoint × categoria e conta as `lacunas`. Mas ele é relatório: roda no Bloco 3,
depois do loop, e sai com código 0 de qualquer jeito. O número aparecia na tela e
ninguém agia sobre ele.

Foi assim que uma execução real gerou 50 testes, deixou `CAT-07` (regras de
negócio) sem um único teste nos cinco endpoints, e passou no Gate B. É exatamente
o defeito "planejei e não entreguei" que o projeto existe para eliminar — só que
uma camada acima de onde os gates estavam olhando.

Autoridade e detalhe são coisas diferentes
------------------------------------------
Quem decide se reprova é o script da skill, pelo contador `lacunas`. O
orquestrador nunca recalcula esse número.

Só que o contador sozinho não é acionável: "6 lacunas" não diz ao executor o que
escrever. Então o detalhe é reconstruído aqui, cruzando o manifesto com as tags
dos specs — e é **conferido contra o contador antes de ser usado**. Se as duas
contas não baterem, a lista é descartada e o delta sai só com o número. Palpite
com cara de precisão é pior que número honesto: um endpoint×categoria errado na
lista faz o executor gastar tentativa consertando o que não estava quebrado.

E quando o contador não vem
---------------------------
Não há veredito. Não é aprovação — seria declarar cobertura sem tê-la medido, que
é o falso sucesso que este gate existe para fechar — nem reprovação, porque o
executor não tem como consertar um script que não rodou. O resultado é
`ERRO_DA_FERRAMENTA`, e quem o recebe interrompe o recurso.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.analise_estatica.tags_cypress import extrair_tags
from orquestrador.config import Config
from orquestrador.contratos import Manifesto, Recurso, ResultadoGate, Violacao
from orquestrador.ferramentas.scripts_qa import Cobertura
from orquestrador.gates.saidas import resumo_da_cobertura

CODIGO = "QAORQ-030"

# Quantas lacunas nomear no delta. Mais que isso vira parede de texto e o executor
# perde o começo; o número total continua na mensagem de cabeçalho.
LIMITE_DE_DETALHE = 20


def executar(
    config: Config,
    recurso: Recurso,
    *,
    manifesto: Manifesto | None = None,
    gate: str,
    out: Path | None = None,
) -> ResultadoGate | None:
    """Reprova quando o gabarito declarou categoria que nenhum `it` cobre.

    Devolve `None` quando a checagem está desligada em `[gates.b].exigir_cobertura` —
    desligada é decisão de quem configura, e não tem veredito nenhum a somar.
    """
    if not config.gate("b").exigir_cobertura:
        return None

    saida = Cobertura(config).executar(recurso.caminho_testes, out=out, json=True)
    contadores = resumo_da_cobertura(saida)
    if not contadores:
        # O script sai 0 mesmo sem conseguir gerar o relatório, então JSON ausente é
        # o único sinal. Sem contador não há veredito: aprovar seria declarar
        # cobertura sem tê-la medido, e reprovar mandaria o executor reescrever
        # specs por causa de um script que não rodou.
        return ResultadoGate.erro_da_ferramenta(
            "qa-cobertura.mjs não produziu contadores, então a lacuna de cobertura "
            "ficou sem medida. Rode o script à mão sobre "
            f"{recurso.caminho_testes} para ver o erro, ou desligue a checagem em "
            f"[gates.b].exigir_cobertura.\n{saida.texto[:400] or '(sem saída)'}",
            gate=gate,
            saida_bruta=saida.texto,
        )

    lacunas = int(contadores.get("lacunas", 0))
    if lacunas <= 0:
        return ResultadoGate.aprovado_por(saida_bruta=saida.stdout, gate=gate)

    faltando = _reconstruir_faltantes(recurso, manifesto)
    if faltando is not None and len(faltando) != lacunas:
        # As duas contas divergiram: a nossa leitura das tags não reproduz a do
        # script. O veredito continua sendo o dele; o detalhe vai fora.
        faltando = None

    return ResultadoGate.reprovado_por(
        _violacoes(recurso, lacunas, faltando),
        saida_bruta=saida.stdout,
        gate=gate,
    )


def _violacoes(
    recurso: Recurso, lacunas: int, faltando: list[tuple[str, str]] | None
) -> list[Violacao]:
    arquivo = f"{recurso.nome}/_support/cobertura.json"
    if not faltando:
        return [
            Violacao(
                codigo=CODIGO,
                arquivo=arquivo,
                mensagem=(
                    f"{lacunas} categoria(s) declarada(s) em cats sem nenhum teste. "
                    "Cada categoria do gabarito precisa de pelo menos um `it` com "
                    "`@endpoint <MÉTODO /rota> @cat <CAT-xx>`. Confira o relatório de "
                    "cobertura para saber quais."
                ),
            )
        ]

    violacoes = [
        Violacao(
            codigo=CODIGO,
            arquivo=arquivo,
            mensagem=(
                f"{endpoint}: {cat} está em cats do gabarito e nenhum `it` a cobre. "
                f"Escreva o teste e marque-o com `@endpoint {endpoint} @cat {cat}`."
            ),
        )
        for endpoint, cat in faltando[:LIMITE_DE_DETALHE]
    ]
    if len(faltando) > LIMITE_DE_DETALHE:
        violacoes.append(
            Violacao(
                codigo=CODIGO,
                arquivo=arquivo,
                mensagem=(
                    f"e mais {len(faltando) - LIMITE_DE_DETALHE} categoria(s) sem "
                    f"teste, de {len(faltando)} no total"
                ),
            )
        )
    return violacoes


def _reconstruir_faltantes(
    recurso: Recurso, manifesto: Manifesto | None
) -> list[tuple[str, str]] | None:
    """Pares `(endpoint, cat)` do gabarito sem tag correspondente nos specs.

    Devolve `None` quando não dá para afirmar nada: sem manifesto em memória, ou
    quando algum spec usa tag dinâmica (`@cat ${...}`), que este parser não resolve
    e que faria uma categoria coberta parecer lacuna.
    """
    if manifesto is None:
        return None

    marcados: set[tuple[str, str]] = set()
    for spec in sorted(recurso.caminho_testes.rglob("*.cy.js")):
        tags = extrair_tags(spec.read_text(encoding="utf-8", errors="replace"))
        if tags.dinamicas:
            return None
        marcados |= tags.pares

    return [
        (endpoint.endpoint, cat)
        for endpoint in manifesto.endpoints
        for cat in endpoint.cats
        if (endpoint.endpoint, cat) not in marcados
    ]
