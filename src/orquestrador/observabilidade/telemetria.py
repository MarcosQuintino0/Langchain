"""Telemetria de token e de tamanho de entrada.

Sem isto não há como comprovar que o custo quadrático foi resolvido — que é
metade do objetivo do projeto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.table import Table

from orquestrador.contratos import RegistroDeChamada, RegistroDeTool, UsoDeTokens


@dataclass
class Agregado:
    """Soma de um conjunto de chamadas (por estágio, por recurso ou por tentativa)."""

    chamadas: int = 0
    uso: UsoDeTokens = field(default_factory=UsoDeTokens)
    duracao_s: float = 0.0
    caracteres_entrada: int = 0
    caracteres_instrucao: int = 0

    def somar(self, chamada: RegistroDeChamada) -> None:
        self.chamadas += 1
        self.uso = self.uso + chamada.uso
        self.duracao_s += chamada.duracao_s
        self.caracteres_entrada += chamada.caracteres_entrada
        # A instrução fixa é constante por estágio: o máximo é a linha de base,
        # somar repetiria a mesma constante uma vez por chamada.
        self.caracteres_instrucao = max(self.caracteres_instrucao, chamada.caracteres_instrucao)

    def para_log(self) -> dict[str, Any]:
        return {
            "chamadas": self.chamadas,
            **self.uso.model_dump(),
            "duracao_s": round(self.duracao_s, 3),
            "caracteres_entrada": self.caracteres_entrada,
            "caracteres_instrucao": self.caracteres_instrucao,
        }


@dataclass
class AgregadoDeTools:
    """Soma das chamadas de uma tool."""

    chamadas: int = 0
    caracteres: int = 0
    erros: int = 0
    duracao_s: float = 0.0

    def somar(self, tool: RegistroDeTool) -> None:
        self.chamadas += 1
        self.caracteres += tool.caracteres
        self.erros += int(tool.erro)
        self.duracao_s += tool.duracao_s

    def para_log(self) -> dict[str, Any]:
        return {
            "chamadas": self.chamadas,
            "caracteres": self.caracteres,
            "erros": self.erros,
            "duracao_s": round(self.duracao_s, 3),
        }


class Telemetria:
    """Tokens e tamanho de entrada por chamada, agregados por estágio, recurso e tentativa."""

    def __init__(self, registro: Any = None) -> None:
        self.chamadas: list[RegistroDeChamada] = []
        self.tools: list[RegistroDeTool] = []
        self.registro = registro

    def registrar(self, chamada: RegistroDeChamada) -> RegistroDeChamada:
        self.chamadas.append(chamada)
        if self.registro is not None:
            # Uma linha por chamada no JSONL: sem isso o log só teria o agregado, e
            # "quantos tokens custou a tentativa 2" viraria dedução em vez de registro.
            self.registro.evento("chamada_llm", **chamada.model_dump())
        return chamada

    def registrar_tool(self, tool: RegistroDeTool) -> RegistroDeTool:
        self.tools.append(tool)
        if self.registro is not None:
            # Uma linha por chamada, com a ordem: é o que permite reconstruir a
            # sequência de exploração sem reexecutar nada.
            self.registro.evento("tool", **tool.model_dump())
        return tool

    @property
    def simulado(self) -> bool:
        return any(chamada.simulado for chamada in self.chamadas)

    def caracteres_de_tools(self) -> int:
        return sum(tool.caracteres for tool in self.tools)

    def total(self) -> UsoDeTokens:
        soma = UsoDeTokens()
        for chamada in self.chamadas:
            soma = soma + chamada.uso
        return soma

    def _agregar(self, chave) -> dict[Any, Agregado]:
        agregado: dict[Any, Agregado] = {}
        for chamada in self.chamadas:
            agregado.setdefault(chave(chamada), Agregado()).somar(chamada)
        return agregado

    def por_estagio(self) -> dict[str, Agregado]:
        return self._agregar(lambda chamada: chamada.estagio)

    def por_recurso(self) -> dict[tuple[str, str], Agregado]:
        return self._agregar(lambda chamada: (chamada.recurso, chamada.estagio))

    def por_tentativa(self) -> dict[tuple[str, str, int], Agregado]:
        """Agregado por (recurso, estágio, tentativa) — a visão que refuta O(n²).

        É aqui que a entrada de cada tentativa fica lado a lado: se a coluna de
        caracteres cresce com o número da tentativa, o corte do princípio 2 vazou.
        """
        return self._agregar(
            lambda chamada: (chamada.recurso, chamada.estagio, chamada.tentativa)
        )

    # -- apresentação -------------------------------------------------------

    def tabela_por_estagio(self) -> Table:
        sufixo = " [yellow](SIMULADO — nenhum modelo foi chamado)[/yellow]" if self.simulado else ""
        tabela = Table(title=f"Tokens por estágio{sufixo}", title_justify="left")
        tabela.add_column("estágio")
        tabela.add_column("chamadas", justify="right")
        tabela.add_column("entrada", justify="right")
        tabela.add_column("saída", justify="right")
        tabela.add_column("total", justify="right")
        tabela.add_column("tempo (s)", justify="right")
        for estagio, agregado in sorted(self.por_estagio().items()):
            tabela.add_row(
                estagio,
                str(agregado.chamadas),
                f"{agregado.uso.entrada:,}",
                f"{agregado.uso.saida:,}",
                f"{agregado.uso.total:,}",
                f"{agregado.duracao_s:.1f}",
            )
        total = self.total()
        tabela.add_section()
        tabela.add_row(
            "[bold]TOTAL",
            f"[bold]{len(self.chamadas)}",
            f"[bold]{total.entrada:,}",
            f"[bold]{total.saida:,}",
            f"[bold]{total.total:,}",
            f"[bold]{sum(c.duracao_s for c in self.chamadas):.1f}",
        )
        return tabela

    def tabela_por_recurso(self) -> Table:
        tabela = Table(title="Tokens por recurso × estágio", title_justify="left")
        tabela.add_column("recurso")
        tabela.add_column("estágio")
        tabela.add_column("chamadas", justify="right")
        tabela.add_column("total", justify="right")
        for (recurso, estagio), agregado in sorted(self.por_recurso().items()):
            tabela.add_row(
                recurso, estagio, str(agregado.chamadas), f"{agregado.uso.total:,}"
            )
        return tabela

    def tabela_entrada_por_tentativa(self) -> Table:
        """A tabela que prova (ou refuta) o custo linear.

        A coluna "entrada" é o que foi enviado ao modelo naquela tentativa, sem a
        instrução fixa. Se ela cresce da tentativa 1 para a 2, o reparo está
        levando histórico junto — que é exatamente o que o princípio 2 proíbe.
        """
        tabela = Table(
            title="Entrada enviada por tentativa (caracteres)", title_justify="left"
        )
        tabela.add_column("recurso")
        tabela.add_column("estágio")
        tabela.add_column("tentativa", justify="right")
        tabela.add_column("chamadas", justify="right")
        tabela.add_column("instrução fixa", justify="right")
        tabela.add_column("entrada", justify="right")
        for (recurso, estagio, tentativa), agregado in sorted(self.por_tentativa().items()):
            tabela.add_row(
                recurso,
                estagio,
                str(tentativa),
                str(agregado.chamadas),
                f"{agregado.caracteres_instrucao:,}",
                f"{agregado.caracteres_entrada:,}",
            )
        return tabela

    def tools_por_nome(self) -> dict[str, AgregadoDeTools]:
        agregado: dict[str, AgregadoDeTools] = {}
        for tool in self.tools:
            agregado.setdefault(tool.nome, AgregadoDeTools()).somar(tool)
        return agregado

    def tabela_de_tools(self) -> Table:
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
        total = self.caracteres_de_tools()
        for nome, agregado in sorted(
            self.tools_por_nome().items(), key=lambda item: -item[1].caracteres
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
            f"[bold]{len(self.tools)}",
            f"[bold]{total:,}",
            "",
            f"[bold]{sum(t.erro for t in self.tools)}",
            f"[bold]{sum(t.duracao_s for t in self.tools):.1f}",
        )
        return tabela

    def resumo_para_log(self) -> dict[str, Any]:
        return {
            "simulado": self.simulado,
            "chamadas": len(self.chamadas),
            "tools": len(self.tools),
            "caracteres_de_tools": self.caracteres_de_tools(),
            "por_tool": {
                nome: agregado.para_log()
                for nome, agregado in self.tools_por_nome().items()
            },
            "total": self.total().model_dump(),
            "por_estagio": {
                estagio: agregado.para_log()
                for estagio, agregado in self.por_estagio().items()
            },
            "por_tentativa": {
                f"{recurso}/{estagio}/t{tentativa}": agregado.para_log()
                for (recurso, estagio, tentativa), agregado in self.por_tentativa().items()
            },
        }
