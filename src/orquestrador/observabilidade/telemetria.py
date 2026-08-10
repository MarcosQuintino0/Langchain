"""Telemetria de token e de tamanho de entrada — agregação, só.

Sem isto não há como comprovar que o custo quadrático foi resolvido — que é
metade do objetivo do projeto.

Este módulo conta e agrupa; ele não desenha. A apresentação em tabela Rich mora
em `tabelas.py`, que consome os agregados daqui. A divisão vale porque as duas
metades mudam por motivos diferentes: acrescentar uma métrica mexe aqui,
acrescentar uma coluna ao console mexe lá — e o pipeline, que só precisa contar
token, não deve arrastar a dependência de terminal junto.

O que sai daqui para fora é dado: agregados e `resumo_para_log`.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from orquestrador.dominio.orcamento import Consumo
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.medidas import RegistroDeChamada, RegistroDeTool, UsoDeTokens
from orquestrador.observabilidade.registro import RegistradorDeEventos

# A chave de agrupamento muda por método (`str`, `(str, str)`, `(str, str, int)`) e
# é ela que tipa o dicionário devolvido. Com `Any` no lugar, `por_tentativa()[...]`
# aceitava qualquer chave e o desempacotamento de três elementos em `resumo_para_log`
# não era conferido por ninguém.
Chave = TypeVar("Chave", bound=Hashable)


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

    def __init__(self, registro: RegistradorDeEventos | None = None) -> None:
        self.chamadas: list[RegistroDeChamada] = []
        self.tools: list[RegistroDeTool] = []
        self.registro = registro

    def registrar(self, chamada: RegistroDeChamada) -> RegistroDeChamada:
        self.chamadas.append(chamada)
        # No schema v2 a linha nasce no callback do provedor, no instante exato da
        # requisição. Emitir aqui repetiria a mesma chamada e voltaria a confundir
        # uma invocação ReAct (que pode conter várias requisições) com uma request.
        return chamada

    def registrar_tool(self, tool: RegistroDeTool) -> RegistroDeTool:
        self.tools.append(tool)
        if self.registro is not None:
            # Uma linha por chamada, com a ordem: é o que permite reconstruir a
            # sequência de exploração sem reexecutar nada.
            self.registro.evento(TipoDeEvento.TOOL, **tool.model_dump())
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

    def _agregar(self, chave: Callable[[RegistroDeChamada], Chave]) -> dict[Chave, Agregado]:
        agregado: dict[Chave, Agregado] = {}
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
        return self._agregar(lambda chamada: (chamada.recurso, chamada.estagio, chamada.tentativa))

    def tools_por_nome(self) -> dict[str, AgregadoDeTools]:
        agregado: dict[str, AgregadoDeTools] = {}
        for tool in self.tools:
            agregado.setdefault(tool.nome, AgregadoDeTools()).somar(tool)
        return agregado

    def resumo_para_log(self) -> dict[str, Any]:
        return {
            "simulado": self.simulado,
            "chamadas": len(self.chamadas),
            "tools": len(self.tools),
            "caracteres_de_tools": self.caracteres_de_tools(),
            "por_tool": {
                nome: agregado.para_log() for nome, agregado in self.tools_por_nome().items()
            },
            "total": self.total().model_dump(),
            "por_estagio": {
                estagio: agregado.para_log() for estagio, agregado in self.por_estagio().items()
            },
            "por_tentativa": {
                f"{recurso}/{estagio}/t{tentativa}": agregado.para_log()
                for (recurso, estagio, tentativa), agregado in self.por_tentativa().items()
            },
        }

    def consumo(self, recurso: str | None = None) -> Consumo:
        """O que já foi gasto, em `Consumo` — o vocabulário que o orçamento entende.

        Com `recurso`, só o daquele recurso; sem, o da execução inteira. São os dois
        escopos que `dominio/orcamento.py` compara, e é por isso que a agregação
        mora aqui: a telemetria já tem os registros, e duplicar a soma do outro lado
        criaria duas contas que divergem.

        Este módulo **não decide** parar. Ele responde "quanto"; quem compara com o
        teto e interrompe é o agente, antes de chamar o modelo.
        """
        chamadas = [c for c in self.chamadas if recurso is None or c.recurso == recurso]
        tools = [t for t in self.tools if recurso is None or t.recurso == recurso]
        return Consumo(
            chamadas=len(chamadas),
            tokens=sum(c.uso.total for c in chamadas),
            caracteres_de_tools=sum(t.caracteres for t in tools),
            # Só o tempo de chamada de modelo e de tool: o do pipeline em volta não é
            # gasto de provedor, e contá-lo faria um gate lento parecer estouro de
            # orçamento.
            segundos=sum(c.duracao_s for c in chamadas) + sum(t.duracao_s for t in tools),
        )
