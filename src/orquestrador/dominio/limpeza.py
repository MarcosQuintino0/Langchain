"""O veredito determinístico de limpeza: os testes conseguem apagar o que criarem?

Nenhum modelo decide isto. A pergunta se responde olhando o inventário — existe
rota de exclusão? — e o que muda com a resposta é template, não julgamento:
com DELETE, a suíte recebe a receita de limpeza (enriquecida com as regras
transversais e o contrato de erro que o dossiê tiver sobre a exclusão); sem
DELETE, recebe o aviso de que a massa criada **permanece no ambiente**, porque
limpeza impossível fingida de feita é o pior dos mundos — dá a sensação de
ambiente limpo sem limpar nada.

Do mesmo inventário sai a lista do que **não** existe (método de escrita que
nenhuma rota usa), porque ausência também orienta teste: suíte que assume um
`PUT` inexistente testa uma API imaginária. Gerar essas seções por LLM seria
pagar tokens por um fato derivável — e abrir espaço para o fato sair errado.
"""

from __future__ import annotations

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario

# A ordem canônica dos métodos de escrita no render de ausências. Frozenset não
# tem ordem, e texto que muda de ordem entre execuções custa cache e diff de log.
_ESCRITA_ORDENADA: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")

AVISO_SEM_EXCLUSAO = (
    "⚠️ **Atenção: os registros criados por estes testes não serão apagados.** "
    "Nenhum endpoint de exclusão existe no inventário deste recurso. A suíte cria "
    "registros para poder testar e eles permanecerão no ambiente depois da "
    "execução. Estratégia de redução de dano: toda massa criada usa prefixo "
    "reconhecível com sufixo único por execução, para nunca colidir com dados "
    "reais nem com execuções anteriores. Se o ambiente for compartilhado ou for "
    "produção, avalie antes de rodar."
)


def endpoints_de_exclusao(inventario: Inventario) -> list[str]:
    return [endpoint.canonico for endpoint in inventario.endpoints if endpoint.metodo == "DELETE"]


def metodos_de_escrita_ausentes(inventario: Inventario) -> list[str]:
    presentes = {endpoint.metodo for endpoint in inventario.endpoints}
    return [metodo for metodo in _ESCRITA_ORDENADA if metodo not in presentes]


def render_limpeza(inventario: Inventario, dossie: DossieDoRecurso | None = None) -> str:
    """A seção de limpeza que vai para o dossiê renderizado e para o `_support/`."""
    exclusoes = endpoints_de_exclusao(inventario)
    if not exclusoes:
        return "## Limpeza dos registros criados\n\n" + AVISO_SEM_EXCLUSAO

    linhas = [
        "## Limpeza dos registros criados",
        "",
        "Existe rota de exclusão — a suíte pode e deve apagar o que criar:",
        *(f"- `{endpoint}`" for endpoint in exclusoes),
        "",
        "A limpeza obedece três regras, sem exceção:",
        "1. respeitar os pré-requisitos da exclusão listados abaixo (headers de "
        "versão, dependentes) — DELETE que falha calado não é limpeza;",
        "2. conferir a resposta da exclusão e **reportar** falha em vez de engolir: "
        "cleanup silenciosamente quebrado suja o ambiente e derruba a próxima "
        "execução por um motivo que não tem nada a ver com o teste;",
        "3. massa que um cenário torna inapagável de propósito (dependente criado "
        "pelo próprio teste) é responsabilidade do cenário que a criou.",
    ]

    if dossie is not None:
        prerequisitos: list[str] = []
        for regra in dossie.regras:
            if regra.transversal or any(regra.aplica_a(endpoint) for endpoint in exclusoes):
                prerequisitos.append(regra.render())
        erros = [
            item.render()
            for endpoint in exclusoes
            if (item := dossie.erros_do_endpoint(endpoint)) is not None
        ]
        if prerequisitos:
            linhas += ["", "Pré-requisitos lidos da fonte:", "", *prerequisitos]
        if erros:
            linhas += ["", "Modos de falha conhecidos da exclusão:", "", *erros]

    return "\n".join(linhas)


def render_ausencias(inventario: Inventario) -> str:
    """O que não existe — afirmação derivada do inventário, não opinião de modelo."""
    total = len(inventario.endpoints)
    linhas = [
        "## O que não existe neste recurso",
        "",
        f"- O inventário tem exatamente {total} endpoint(s); não há rota fora dele. "
        "Teste que assume outra rota testa uma API imaginária.",
    ]
    ausentes = metodos_de_escrita_ausentes(inventario)
    if ausentes:
        linhas.append(
            "- Nenhuma rota usa "
            + ", ".join(f"`{metodo}`" for metodo in ausentes)
            + " — não planeje cenário que dependa desses métodos."
        )
    if inventario.rotas_dinamicas_nao_resolvidas:
        linhas.append(
            f"- {len(inventario.rotas_dinamicas_nao_resolvidas)} rota(s) dinâmica(s) não "
            "resolvida(s) estão registradas no inventário: sobre elas nada é afirmado."
        )
    return "\n".join(linhas)
