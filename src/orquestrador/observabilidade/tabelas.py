"""Apresentação da telemetria em tabelas Rich.

Funções, não métodos: recebem uma `Telemetria` já agregada e devolvem um `Table`.
A separação existe porque os dois lados mudam por motivos diferentes — a
agregação muda quando muda o que é medido, a tabela muda quando muda o que se
quer olhar. Enquanto moravam na mesma classe, somar uma coluna ao console mexia
no módulo que o pipeline importa para contar token.

Nada aqui decide fluxo, e nada aqui recalcula número: toda conta vem dos
agregados de `telemetria`. O que este módulo escolhe é ordem, formato e o que
merece destaque.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

if TYPE_CHECKING:
    from orquestrador.observabilidade.medidas import UsoDeTokens
    from orquestrador.observabilidade.telemetria import Telemetria


def raciocinio_da_saida(uso: UsoDeTokens) -> str:
    """Quanto da saída foi pensamento, em token e porcentagem.

    Num modelo de raciocínio, pensamento e resposta dividem o mesmo orçamento de
    saída. Sem esta célula, uma espiral de raciocínio (o modelo gasta o teto
    inteiro pensando e a resposta chega cortada) aparece como "saída grande" —
    e a decisão de trocar de modelo, limitar o pensamento ou não fazer nada
    fica sem o número que a justifica. Modelo que não reporta pensamento mostra
    zero, e zero constante é informação: não há o que limitar.
    """
    if not uso.saida:
        return "—"
    return f"{uso.raciocinio:,} ({uso.raciocinio / uso.saida:.0%})" if uso.raciocinio else "0"


def cache_da_entrada(uso: UsoDeTokens) -> str:
    """Quanto da entrada veio do cache, em token e em porcentagem.

    Fica ao lado de "entrada" e não numa coluna de custo porque a pergunta que ele
    responde é sobre reaproveitamento, não sobre preço: um estágio que reenvia o
    mesmo prefixo dezenas de vezes deveria mostrar taxa alta, e mostra a real.

    Cache gravado só aparece quando existe. Ele é ruído na maioria das linhas e
    diagnóstico numa: escrever muito e ler pouco é pagar a mais por nada.
    """
    if not uso.entrada:
        return "—"
    texto = f"{uso.cache_lido:,} ({uso.taxa_de_cache:.0%})"
    return f"{texto} +{uso.cache_escrito:,}w" if uso.cache_escrito else texto


def tabela_por_estagio(telemetria: Telemetria) -> Table:
    """Tokens somados por estágio, com a linha de total."""
    simulado = telemetria.simulado
    sufixo = " [yellow](SIMULADO — nenhum modelo foi chamado)[/yellow]" if simulado else ""
    tabela = Table(title=f"Tokens por estágio{sufixo}", title_justify="left")
    tabela.add_column("estágio")
    tabela.add_column("chamadas", justify="right")
    tabela.add_column("entrada", justify="right")
    tabela.add_column("do cache", justify="right")
    tabela.add_column("saída", justify="right")
    tabela.add_column("pensando", justify="right")
    tabela.add_column("total", justify="right")
    tabela.add_column("tempo (s)", justify="right")
    for estagio, agregado in sorted(telemetria.por_estagio().items()):
        tabela.add_row(
            estagio,
            str(agregado.chamadas),
            f"{agregado.uso.entrada:,}",
            cache_da_entrada(agregado.uso),
            f"{agregado.uso.saida:,}",
            raciocinio_da_saida(agregado.uso),
            f"{agregado.uso.total:,}",
            f"{agregado.duracao_s:.1f}",
        )
    total = telemetria.total()
    tabela.add_section()
    tabela.add_row(
        "[bold]TOTAL",
        f"[bold]{len(telemetria.chamadas)}",
        f"[bold]{total.entrada:,}",
        f"[bold]{cache_da_entrada(total)}",
        f"[bold]{total.saida:,}",
        f"[bold]{raciocinio_da_saida(total)}",
        f"[bold]{total.total:,}",
        f"[bold]{sum(c.duracao_s for c in telemetria.chamadas):.1f}",
    )
    return tabela


def tabela_por_recurso(telemetria: Telemetria) -> Table:
    """Tokens no cruzamento recurso × estágio."""
    tabela = Table(title="Tokens por recurso × estágio", title_justify="left")
    tabela.add_column("recurso")
    tabela.add_column("estágio")
    tabela.add_column("chamadas", justify="right")
    tabela.add_column("total", justify="right")
    for (recurso, estagio), agregado in sorted(telemetria.por_recurso().items()):
        tabela.add_row(recurso, estagio, str(agregado.chamadas), f"{agregado.uso.total:,}")
    return tabela


def tabela_entrada_por_tentativa(telemetria: Telemetria) -> Table:
    """A tabela que prova (ou refuta) o custo linear.

    A coluna "entrada" é o que foi enviado ao modelo naquela tentativa, sem a
    instrução fixa. Se ela cresce da tentativa 1 para a 2, o reparo está levando
    histórico junto — que é exatamente o que o princípio 2 proíbe.
    """
    tabela = Table(title="Entrada enviada por tentativa (caracteres)", title_justify="left")
    tabela.add_column("recurso")
    tabela.add_column("estágio")
    tabela.add_column("tentativa", justify="right")
    tabela.add_column("chamadas", justify="right")
    tabela.add_column("instrução fixa", justify="right")
    tabela.add_column("entrada", justify="right")
    for (recurso, estagio, tentativa), agregado in sorted(telemetria.por_tentativa().items()):
        tabela.add_row(
            recurso,
            estagio,
            str(tentativa),
            str(agregado.chamadas),
            f"{agregado.caracteres_instrucao:,}",
            f"{agregado.caracteres_entrada:,}",
        )
    return tabela


def tabela_de_tools(telemetria: Telemetria) -> Table:
    """Como o mapeador explorou o backend, e o que isso custou.

    A coluna "devolvido" é o que interessa: cada caractere que uma tool devolve
    entra no histórico do ReAct e é reenviado em toda volta seguinte. Uma
    `buscar_no_backend` generosa custa muito mais que o próprio retorno dela.

    `graphify_query` alto com `ler_arquivo` baixo é o comportamento que a
    instrução pede. O inverso significa que o grafo está sendo ignorado — e o
    Graphify deixou de pagar o que promete.
    """
    tabela = Table(title="Tools do mapeador", title_justify="left")
    tabela.add_column("tool")
    tabela.add_column("chamadas", justify="right")
    tabela.add_column("devolvido", justify="right")
    tabela.add_column("% do total", justify="right")
    tabela.add_column("erros", justify="right")
    tabela.add_column("tempo (s)", justify="right")
    total = telemetria.caracteres_de_tools()
    for nome, agregado in sorted(
        telemetria.tools_por_nome().items(), key=lambda item: -item[1].caracteres
    ):
        fatia = (agregado.caracteres / total * 100) if total else 0.0
        tabela.add_row(
            nome,
            str(agregado.chamadas),
            f"{agregado.caracteres:,}",
            f"{fatia:.0f}%",
            f"[red]{agregado.erros}" if agregado.erros else "0",
            f"{agregado.duracao_s:.1f}",
        )
    tabela.add_section()
    tabela.add_row(
        "[bold]TOTAL",
        f"[bold]{len(telemetria.tools)}",
        f"[bold]{total:,}",
        "",
        f"[bold]{sum(t.erro for t in telemetria.tools)}",
        f"[bold]{sum(t.duracao_s for t in telemetria.tools):.1f}",
    )
    return tabela
