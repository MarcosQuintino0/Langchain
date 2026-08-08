"""`orquestrador --estimar` — o tamanho do trabalho antes de gastar um token.

Roda **só o Bloco 0**, que é determinístico e conta endpoints. Nenhum modelo é
chamado, nenhum arquivo do consumidor é tocado.

Por que uma faixa, e por que ela é larga
----------------------------------------
O custo real depende de coisas que não se sabe antes: quantas voltas de reparo
cada gate vai exigir, quanto o backend devolve por consulta de tool, quanto o
modelo escolhe emitir. A faixa aqui vem de multiplicar o número de endpoints por
um custo por endpoint que **está na configuração**, com padrões medidos numa
execução real — uma só.

Isso é pouca evidência, e a saída diz isso. Uma estimativa que se apresenta como
precisa é pior que nenhuma: ela vira número em planilha, e a primeira vez que
erra por três vezes, é o orçamento que leva a culpa.

Por que não estima moeda
------------------------
Preço é do modelo, e nenhum nome de modelo aparece em código (princípio 6). Quem
quiser moeda multiplica a faixa de token pelo preço que o provedor cobra pelo
modelo que **ele** configurou — e esse número muda por decisão comercial de
terceiro, não por mudança nossa.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from orquestrador.analise_estatica.extrator_de_endpoints import extrair
from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, SUCESSO
from orquestrador.config import Config
from orquestrador.excecoes import ErroDeFerramenta


def _faixa(quantidade: int, config: Config) -> tuple[int, int]:
    estimativa = config.orcamento.estimativa
    return (
        quantidade * estimativa.tokens_por_endpoint_min,
        quantidade * estimativa.tokens_por_endpoint_max,
    )


def estimar(config: Config, console: Console) -> int:
    """Conta o que o backend expõe e devolve a faixa de token. Não chama modelo."""
    try:
        backend = extrair(graph=config.caminhos.graph_abs, backend=config.caminhos.backend)
    except ErroDeFerramenta as erro:
        console.print(f"[red]não foi possível ler o backend:[/red] {erro}")
        return ERRO_DE_USO

    if not backend.endpoints:
        console.print(
            "[yellow]nenhum endpoint encontrado.[/yellow] Ou o `graph.json` está "
            "desatualizado (rode o qa-reindex), ou o backend está fora da matriz de "
            "suporte — e nesse caso não há denominador determinístico, então estimar "
            "seria inventar."
        )
        return ERRO_DE_USO

    # Agrupado por classe controladora, e não por recurso: o extrator lê o backend e
    # não sabe a que recurso um endpoint pertence — quem decide isso é o mapeador, no
    # Bloco 1. Prometer um agrupamento por recurso aqui seria adiantar uma decisão
    # que ainda não foi tomada.
    tabela = Table(title="Estimativa — nenhum modelo foi chamado")
    tabela.add_column("classe controladora")
    tabela.add_column("endpoints", justify="right")
    tabela.add_column("tokens (faixa)", justify="right")

    for classe in sorted(backend.classes, key=lambda c: c.classe):
        if not classe.endpoints:
            continue
        minimo, maximo = _faixa(len(classe.endpoints), config)
        tabela.add_row(classe.classe, str(len(classe.endpoints)), f"{minimo:,} a {maximo:,}")

    total = len(backend.endpoints)
    minimo, maximo = _faixa(total, config)
    tabela.add_section()
    tabela.add_row("TOTAL", str(total), f"{minimo:,} a {maximo:,}", style="bold")
    console.print(tabela)

    nao_resolvidas = sum(len(classe.nao_resolvidas) for classe in backend.classes)
    if nao_resolvidas:
        console.print(
            f"[yellow]{nao_resolvidas} rota(s) o extrator não resolveu.[/yellow] Elas "
            "ficam de fora da conta: incerteza não vira estimativa."
        )
    if backend.arquivos_ausentes:
        console.print(
            f"[yellow]{len(backend.arquivos_ausentes)} arquivo(s) do grafo não foram "
            "encontrados no disco.[/yellow] O `graph.json` pode estar desatualizado — "
            "a conta abaixo é do que existe hoje."
        )

    # O `\[` escapa o colchete: o Rich lê `[orcamento]` como tag de estilo e apaga o
    # texto — que é o jeito mais irônico possível de perder justamente o nome do
    # bloco de configuração que o leitor precisa ir procurar.
    console.print(
        "\nA faixa vem de \\[orcamento].estimativa, cujos padrões saíram de UMA "
        "execução medida. É ordem de grandeza, não previsão: quantas voltas de reparo "
        "cada gate vai exigir é justamente o que não se sabe antes de rodar."
    )
    return SUCESSO
