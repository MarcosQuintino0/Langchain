"""A limpeza gerada faz o que a receita mandou? — checagem determinística.

Fecha o último trecho da promessa "cobertura provada" que ainda era esperança.
`dominio/limpeza.py` calcula a receita, o executor a recebe no `_support/`, e até
aqui ninguém conferia se ela virou código. Cleanup cego é o defeito mais caro
desta suíte porque ele **parece** funcionar: a massa se acumula em silêncio até o
dia em que a unicidade estoura e derruba testes que não têm relação com a causa.

As três perguntas, e por que só estas
-------------------------------------
1. **Existe limpeza?** O inventário tem rota de exclusão e o `_support/` não
   chama `DELETE` em lugar nenhum.
2. **Ela olha o resultado?** Chama `DELETE` e engole o desfecho — `catch` vazio,
   ou `failOnStatusCode: false` sem asserção nenhuma.
3. **Ela respeita a pré-condição?** O contrato de erro da exclusão prevê `412` ou
   `428` e o código não manda cabeçalho condicional nenhum.

A terceira usa **status HTTP**, não prosa. `428 Precondition Required` e
`412 Precondition Failed` são a declaração, no vocabulário do protocolo, de que a
exclusão exige um cabeçalho condicional — então o sinal sai do campo estruturado
`erros` do dossiê, e não de adivinhar nome de cabeçalho no meio de uma frase. Uma
extração por texto acertaria `If-Match` e um dia cobraria `Java-Spring`.

O que fica de fora, deliberadamente: se a limpeza é *chamada* pelos specs, se ela
roda em `after` ou `afterEach`, se o id que ela apaga é o certo. Tudo isso exige
entender o JavaScript de verdade, e o alvo aqui — cleanup que não confere nada —
aparece na superfície. Falso negativo é aceito; falso positivo, não.
"""

from __future__ import annotations

from orquestrador.analise_estatica.limpeza_javascript import analisar_limpeza
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.limpeza import endpoints_de_exclusao
from orquestrador.dominio.veredito import ResultadoGate, Violacao

NOME = "gate_b"

CODIGO_SEM_LIMPEZA = "QAORQ-031"
CODIGO_LIMPEZA_CEGA = "QAORQ-032"
CODIGO_SEM_PRECONDICAO = "QAORQ-033"

# Os dois status com que o protocolo declara "esta operação exige pré-condição".
STATUS_DE_PRECONDICAO = frozenset({412, 428})

# Os cabeçalhos condicionais do HTTP. Lista fechada: é vocabulário do protocolo,
# não do backend, então não muda de recurso para recurso.
CABECALHOS_CONDICIONAIS = (
    "If-Match",
    "If-None-Match",
    "If-Unmodified-Since",
    "If-Modified-Since",
)

# O arquivo apontado pelas violações. É onde o cleanup nasce, e é o que o reparo
# do executor precisa reescrever — apontar o spec mandaria ele ao lugar errado.
ARQUIVO_DO_SUPORTE = "_support/api.js"


def conferir_limpeza(
    modulos_de_suporte: dict[str, str],
    inventario: Inventario | None,
    dossie: DossieDoRecurso | None,
) -> ResultadoGate:
    """O veredito sobre a limpeza gerada, a partir do `_support/` em disco.

    Sem inventário não há o que conferir: é ele que diz se existe exclusão. E
    recurso sem rota de exclusão aprova de saída — a receita, nesse caso, é o
    aviso de que a massa permanece, e isso é conteúdo de plano, não de código.
    """
    if inventario is None:
        return ResultadoGate.aprovado_por(gate=NOME)
    exclusoes = endpoints_de_exclusao(inventario)
    if not exclusoes:
        return ResultadoGate.aprovado_por(gate=NOME)

    achado = analisar_limpeza(modulos_de_suporte, list(CABECALHOS_CONDICIONAIS))
    violacoes: list[Violacao] = []

    if not achado.tem_delete:
        violacoes.append(
            Violacao(
                codigo=CODIGO_SEM_LIMPEZA,
                arquivo=ARQUIVO_DO_SUPORTE,
                mensagem=(
                    f"o recurso tem rota de exclusão ({', '.join(exclusoes)}) e o "
                    "`_support/` não chama DELETE em lugar nenhum: a suíte cria massa "
                    "e não apaga. Implemente o helper de limpeza da receita."
                ),
            )
        )
    elif achado.engole_falha:
        violacoes.append(
            Violacao(
                codigo=CODIGO_LIMPEZA_CEGA,
                arquivo=ARQUIVO_DO_SUPORTE,
                mensagem=(
                    "a limpeza chama DELETE e não confere o resultado (catch vazio ou "
                    "`failOnStatusCode: false` sem asserção). Cleanup que falha calado "
                    "deixa o ambiente sujo e faz a próxima execução falhar por um "
                    "motivo sem relação com o teste. Confira o status da exclusão e "
                    "reporte a falha."
                ),
            )
        )

    if achado.tem_delete and not achado.cabecalhos:
        exigidos = _exclusoes_com_precondicao(dossie, exclusoes)
        if exigidos:
            violacoes.append(
                Violacao(
                    codigo=CODIGO_SEM_PRECONDICAO,
                    arquivo=ARQUIVO_DO_SUPORTE,
                    mensagem=(
                        f"o contrato de erro de {', '.join(exigidos)} prevê "
                        f"{sorted(STATUS_DE_PRECONDICAO)} — a exclusão exige cabeçalho "
                        "condicional — e a limpeza não envia nenhum de "
                        f"{', '.join(CABECALHOS_CONDICIONAIS)}. Sem ele o DELETE é "
                        "recusado e a limpeza só parece funcionar."
                    ),
                )
            )

    if violacoes:
        return ResultadoGate.reprovado_por(violacoes, gate=NOME)
    return ResultadoGate.aprovado_por(gate=NOME)


def _exclusoes_com_precondicao(dossie: DossieDoRecurso | None, exclusoes: list[str]) -> list[str]:
    """Endpoints de exclusão cujo contrato de erro declara pré-condição."""
    if dossie is None:
        return []
    return [
        endpoint
        for endpoint in exclusoes
        if (item := dossie.erros_do_endpoint(endpoint)) is not None
        and any(resposta.status in STATUS_DE_PRECONDICAO for resposta in item.respostas)
    ]
