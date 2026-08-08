"""O contrato com quem automatiza: o que cada código de saída significa.

Módulo próprio, e tão pequeno, por necessidade estrutural: os outros três de
`cli/` precisam das constantes, e `doctor` importando de `principal` enquanto
`principal` importa `doctor` para o despacho é ciclo em tempo de importação.

Ele também é o dono de um contrato que só existia como comentário. Um pipeline
que sai com 3 e um que sai com 1 pedem coisas diferentes de quem agenda a
execução, e essa diferença precisa estar num lugar citável — hoje é
`docs/referencia/cli.md`, e a tabela de lá é **gerada** por `catalogo_markdown`.
"""

from __future__ import annotations

SUCESSO = 0
FALHA_DE_GATE = 1
ERRO_DE_USO = 2
# O terceiro estado do recurso: tudo passou, o artefato foi publicado, e alguma
# coisa precisa de olho humano — hoje, schema do consumidor que não declara campo
# que existe no backend. Não é 0 porque um pipeline verde esconderia a revisão
# pendente, e não é 1 porque nenhum gate reprovou e não há o que o modelo consertar.
REQUER_REVISAO = 3
# Indisponibilidade do provedor de LLM tem código próprio porque a resposta do
# operador é outra: `2` pede para arrumar a configuração ou o ambiente, `4` pede
# para esperar e repetir. Num agendamento, é a diferença entre alertar alguém e
# reenfileirar sozinho.
ERRO_DE_PROVEDOR = 4
# O teto de `[orcamento]` foi alcançado. Não é falha: é a execução obedecendo. O
# código é próprio porque a resposta de quem opera também é — não é arrumar o
# ambiente (`2`) nem esperar e repetir (`4`), é decidir se o trabalho valia mais
# do que o teto autorizava.
ORCAMENTO_ESGOTADO = 5

# A descrição fica ao lado da constante, e não na documentação, pelo mesmo motivo
# de sempre: duas cópias divergem. A tabela publicada sai daqui.
DESCRICAO: dict[int, str] = {
    SUCESSO: "todo recurso pedido terminou aprovado",
    FALHA_DE_GATE: "ao menos um recurso foi reprovado por um gate ou falhou num estágio",
    ERRO_DE_USO: (
        "erro de quem invocou ou do ambiente: configuração inválida, comando "
        "inexistente, ferramenta indisponível"
    ),
    REQUER_REVISAO: (
        "publicado, e alguma coisa precisa de olho humano — hoje, schema do "
        "consumidor que declara menos campos do que o backend tem"
    ),
    ERRO_DE_PROVEDOR: (
        "o provedor de LLM não respondeu dentro da política de retentativa. "
        "Esperar e repetir é a resposta certa"
    ),
    ORCAMENTO_ESGOTADO: (
        "o teto de `[orcamento]` foi alcançado e nenhuma chamada nova começou. "
        "Não é falha: é a execução obedecendo"
    ),
}


def catalogo_markdown() -> str:
    """A tabela de códigos de saída, como ela é publicada.

    Gerada, e não escrita à mão, porque uma tabela mantida à mão diverge — e diverge
    justamente no código novo, que é o que ninguém conhece de cabeça.
    """
    linhas = ["| Código | Significado |", "| --- | --- |"]
    linhas += [f"| `{codigo}` | {texto} |" for codigo, texto in sorted(DESCRICAO.items())]
    return "\n".join(linhas)
