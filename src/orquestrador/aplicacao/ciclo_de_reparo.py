"""O loop de reparo: gera → persiste → avalia → (delta → repete).

O coração da arquitetura, e o único lugar onde o princípio 2 vira código. Sai de
`pipeline.py` porque muda por uma razão só — a mecânica do reparo —, enquanto o
pipeline muda quando a **ordem** dos blocos muda. Enquanto eram o mesmo objeto,
qualquer ajuste no laço obrigava a reler os quatro blocos para ter certeza de que
nada mais dependia do estado compartilhado.

Genérico em `Artefato` porque o laço é idêntico para o `SaidaMapeador` do Bloco 1
e o `SaidaExecutor` do Bloco 2: tudo que ele faz com o artefato é passá-lo aos
quatro callbacks. Com `Any` no lugar, ninguém conferia que os quatro falam do
mesmo artefato.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

from orquestrador.config import Config
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import Delta, EstagioDelta, ResultadoGate
from orquestrador.excecoes import FalhaDeEstagio, FalhaDeGate
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria

# Os dois gates que têm loop de reparo. O tipo fecha a porta que o `# type: ignore`
# mantinha aberta: o estágio do delta era montado por interpolação
# (`f"gate_{gate}"`), e um `gate="c"` produziria a string "gate_c", que nenhum
# consumidor de `EstagioDelta` reconhece.
NomeDeGate = Literal["a", "b"]
_ESTAGIO_DO_GATE: dict[NomeDeGate, EstagioDelta] = {"a": "gate_a", "b": "gate_b"}

# O artefato que atravessa uma volta do loop: `SaidaMapeador` no Bloco 1,
# `SaidaExecutor` no Bloco 2.
Artefato = TypeVar("Artefato")


def _unir_caminhos(atuais: list[Path], novos: list[Path] | None) -> list[Path]:
    """Concatena preservando ordem e sem repetir."""
    unidos = list(atuais)
    for caminho in novos or []:
        if caminho not in unidos:
            unidos.append(caminho)
    return unidos


class CicloDeReparo:
    """Uma volta do loop por tentativa, até o gate aprovar ou as tentativas acabarem.

    Recebe as três coisas de que precisa e nada mais: a configuração (para o teto
    de tentativas do gate), o registro (para o evento e o console) e a telemetria
    (para medir o que cada tentativa enviou). Não conhece staging, publicação nem
    Cypress.
    """

    def __init__(self, config: Config, registro: Registro, telemetria: Telemetria) -> None:
        self.config = config
        self.registro = registro
        self.telemetria = telemetria

    def executar(
        self,
        *,
        estagio: str,
        gate: NomeDeGate,
        recurso: Recurso,
        produzir: Callable[[int, Delta | None, str | None], Artefato],
        persistir: Callable[[Artefato], list[Path]],
        avaliar: Callable[[Artefato], ResultadoGate],
        texto_do_artefato: Callable[[Artefato, Delta], str],
    ) -> tuple[Artefato, ResultadoGate, int]:
        """Gera → persiste → avalia → (delta → repete). O coração da arquitetura.

        Genérico em `Artefato` porque o laço é o mesmo para o `SaidaMapeador` do
        Bloco 1 e o `SaidaExecutor` do Bloco 2, e a única coisa que ele faz com o
        artefato é passá-lo adiante para os quatro callbacks. Com `Any` no lugar do
        parâmetro de tipo, ninguém conferia que os quatro falam do mesmo artefato — e
        o tipo devolvido a `bloco1`/`bloco2` era `Any`, o que apagava a checagem de
        tudo que eles fazem com a saída depois.
        """
        maximo = self.config.gate(gate).max_tentativas
        delta: Delta | None = None
        artefato_atual: str | None = None
        resultado: ResultadoGate | None = None
        persistidos: list[Path] = []

        for tentativa in range(1, maximo + 1):
            marca = len(self.telemetria.chamadas)
            marca_tools = len(self.telemetria.tools)
            try:
                artefato = produzir(tentativa, delta, artefato_atual)
            except FalhaDeEstagio as erro:
                # O que já foi escrito em disco continua lá; quem falha precisa dizer
                # o que deixou para trás (A3).
                erro.arquivos = list(persistidos)
                raise
            finally:
                # No `finally` de propósito: a tentativa fica registrada mesmo quando
                # o estágio explode, e é dela que sai a medida de entrada (A2).
                self._registrar_tentativa(
                    estagio=estagio,
                    recurso=recurso,
                    tentativa=tentativa,
                    delta=delta,
                    desde=marca,
                    desde_tools=marca_tools,
                )
            persistidos = _unir_caminhos(persistidos, persistir(artefato))
            resultado = avaliar(artefato)

            self.registro.evento(
                TipoDeEvento.GATE,
                gate=f"gate_{gate}",
                estagio=estagio,
                recurso=recurso.nome,
                tentativa=tentativa,
                aprovado=resultado.aprovado,
                violacoes=[v.model_dump() for v in resultado.violacoes],
                avisos=[v.model_dump() for v in resultado.avisos],
            )
            for aviso in resultado.avisos:
                self.registro.aviso(aviso.render())

            if resultado.aprovado:
                self.registro.ok(
                    f"gate_{gate} aprovou {recurso.nome} na tentativa {tentativa}/{maximo}"
                )
                return artefato, resultado, tentativa

            self.registro.falha(
                f"gate_{gate} reprovou {recurso.nome} na tentativa {tentativa}/{maximo}: "
                f"{len(resultado.violacoes)} violação(ões) "
                f"[{', '.join(sorted({v.codigo for v in resultado.violacoes}))}]"
            )
            for violacao in resultado.violacoes[:10]:
                self.registro.info(f"    {violacao.render()}")

            delta = Delta(
                estagio=_ESTAGIO_DO_GATE[gate],
                recurso=recurso.nome,
                violacoes=resultado.violacoes,
                tentativa=tentativa,
            )
            # O delta entra na projeção do artefato: é ele que diz quais arquivos e
            # quais linhas precisam estar à vista. Ver `llm.montagem`.
            artefato_atual = texto_do_artefato(artefato, delta)
            self.registro.evento(
                TipoDeEvento.DELTA,
                estagio=f"gate_{gate}",
                de=estagio,
                recurso=recurso.nome,
                tentativa=tentativa,
                codigos=[v.codigo for v in resultado.violacoes],
                bytes_do_artefato=len(artefato_atual),
            )

        violacoes = list(resultado.violacoes) if resultado else []
        codigos = sorted({v.codigo for v in violacoes})
        raise FalhaDeGate(
            f"gate_{gate} reprovou o recurso {recurso.nome!r} em {maximo} tentativa(s). "
            f"Códigos remanescentes: {', '.join(codigos) or '(nenhum)'}",
            arquivos=persistidos,
            violacoes=violacoes,
        )

    def _registrar_tentativa(
        self,
        *,
        estagio: str,
        recurso: Recurso,
        tentativa: int,
        delta: Delta | None,
        desde: int,
        desde_tools: int = 0,
    ) -> None:
        """Evento `estagio_tentativa` com o que foi efetivamente enviado ao modelo.

        Os tamanhos vêm das chamadas registradas durante esta tentativa: a
        instrução fixa (constante, linha de base) e a entrada. É o par que torna o
        princípio 2 verificável a partir do log, sem reexecutar nada.

        O resumo de tools responde a outra pergunta, do mesmo log: a exploração
        seguiu a instrução do estágio? `primeira_tool` é o teste mais direto — a
        instrução manda consultar o grafo antes de procurar no backend, então
        qualquer coisa diferente de `graphify_query` aqui é desvio.
        """
        chamadas = self.telemetria.chamadas[desde:]
        tools = self.telemetria.tools[desde_tools:]
        por_nome: dict[str, int] = {}
        for tool in tools:
            por_nome[tool.nome] = por_nome.get(tool.nome, 0) + 1
        self.registro.evento(
            TipoDeEvento.ESTAGIO_TENTATIVA,
            estagio=estagio,
            recurso=recurso.nome,
            tentativa=tentativa,
            com_delta=delta is not None,
            violacoes_no_delta=[v.codigo for v in delta.violacoes] if delta else [],
            chamadas=len(chamadas),
            caracteres_instrucao=max((c.caracteres_instrucao for c in chamadas), default=0),
            caracteres_entrada=sum(c.caracteres_entrada for c in chamadas),
            tools=len(tools),
            tools_por_nome=por_nome,
            caracteres_de_tools=sum(tool.caracteres for tool in tools),
            tools_com_erro=sum(tool.erro for tool in tools),
            primeira_tool=tools[0].nome if tools else None,
        )
