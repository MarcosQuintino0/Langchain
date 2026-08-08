"""Quanto uma execução pode gastar, e a decisão pura de parar.

O que este módulo é dono: comparar **o que já foi gasto** com **o que foi
autorizado**, e dizer se há folga. Nada aqui mede, registra ou interrompe — quem
mede é `observabilidade/telemetria.py`, quem interrompe é o agente que consultou.

Sobre a honestidade do teto
---------------------------
Um teto de token não consegue prometer "nunca ultrapassa". O custo de uma chamada
só é conhecido **depois** dela: o modelo decide quantos tokens de saída emite, e a
resposta de uma tool pode vir muito maior do que o esperado.

O que dá para prometer é o que está implementado: **nenhuma chamada nova começa
depois que o teto foi alcançado**. A última pode ultrapassar, e por isso
`Orcamento` fala em `excedido`, não em `garantido`. Prometer o outro seria a mesma
categoria de mentira que este projeto passa o tempo todo evitando.

Dois escopos, sempre
--------------------
Por execução e por recurso. Só o de execução deixaria um único recurso patológico
consumir tudo antes de o segundo começar; só o de recurso não teria como parar uma
lista de trinta recursos que sangram devagar.
"""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    model_validator,
)


class Consumo(BaseModel):
    """O que já foi gasto, num escopo. Somável."""

    chamadas: NonNegativeInt = 0
    tokens: NonNegativeInt = 0
    caracteres_de_tools: NonNegativeInt = 0
    segundos: NonNegativeFloat = 0.0

    def __add__(self, outro: Consumo) -> Consumo:
        return Consumo(
            chamadas=self.chamadas + outro.chamadas,
            tokens=self.tokens + outro.tokens,
            caracteres_de_tools=self.caracteres_de_tools + outro.caracteres_de_tools,
            segundos=self.segundos + outro.segundos,
        )


class Limites(BaseModel):
    """Os tetos de um escopo. `None` significa **sem teto**, não zero.

    A distinção importa: zero é um teto legítimo — "não me deixe chamar o modelo" —
    e um campo ausente que virasse zero desligaria a execução inteira na primeira
    tentativa, com uma mensagem sobre orçamento que ninguém configurou.
    """

    chamadas: NonNegativeInt | None = None
    tokens: NonNegativeInt | None = None
    caracteres_de_tools: NonNegativeInt | None = None
    segundos: NonNegativeFloat | None = None

    def excedido(self, consumo: Consumo, *, escopo: str) -> str | None:
        """O primeiro teto alcançado, descrito para quem lê o erro. Ou `None`.

        Compara com `>=`, e não com `>`, porque a pergunta é feita **antes** da
        próxima chamada: "já cheguei ao teto" é a hora de parar, não "já passei
        dele".
        """
        for rotulo, gasto, teto, unidade in (
            ("chamadas ao modelo", consumo.chamadas, self.chamadas, ""),
            ("tokens", consumo.tokens, self.tokens, ""),
            (
                "caracteres devolvidos por tools",
                consumo.caracteres_de_tools,
                self.caracteres_de_tools,
                "",
            ),
            ("tempo", consumo.segundos, self.segundos, "s"),
        ):
            if teto is not None and gasto >= teto:
                return (
                    f"teto de {rotulo} por {escopo} alcançado: "
                    f"{gasto:,.0f}{unidade} de {teto:,.0f}{unidade}".replace(",", ".")
                )
        return None


class Estimativa(BaseModel):
    """Quanto um endpoint custa, para o `--estimar` multiplicar.

    Os padrões saíram de **uma** execução medida — 5 endpoints, dois estágios com
    reparo em ambos os gates, ~412 mil tokens. Uma medição é pouca evidência, e por
    isso a faixa é larga e a saída do comando diz de onde ela veio. Quem rodar em
    backend próprio deve substituí-los pelos números da própria execução: é para
    isso que eles estão em configuração e não em constante.
    """

    model_config = ConfigDict(extra="forbid")

    tokens_por_endpoint_min: NonNegativeInt = 40_000
    tokens_por_endpoint_max: NonNegativeInt = 120_000

    @model_validator(mode="after")
    def _faixa_coerente(self) -> Estimativa:
        if self.tokens_por_endpoint_min > self.tokens_por_endpoint_max:
            raise ValueError(
                "tokens_por_endpoint_min não pode ser maior que o max "
                f"({self.tokens_por_endpoint_min} > {self.tokens_por_endpoint_max})"
            )
        return self


class Orcamento(BaseModel):
    """Os dois escopos, e a pergunta que os agentes fazem antes de chamar o modelo.

    Sem nenhum teto configurado, `motivo_para_parar` devolve sempre `None` e o
    custo do objeto é uma comparação por chamada. É o padrão: um orçamento que
    aparece sem ninguém pedir interrompe execução legítima e ensina a desligá-lo.
    """

    por_execucao: Limites = Field(default_factory=Limites)
    por_recurso: Limites = Field(default_factory=Limites)
    # Só o `--estimar` lê isto. Não é teto: é a régua de uma conta que acontece
    # antes de qualquer chamada.
    estimativa: Estimativa = Field(default_factory=Estimativa)

    @property
    def configurado(self) -> bool:
        """Há algum teto? A `estimativa` não conta: ela não interrompe nada."""
        return any(
            valor is not None
            for limites in (self.por_execucao, self.por_recurso)
            for valor in limites.model_dump().values()
        )

    def motivo_para_parar(self, *, execucao: Consumo, recurso: Consumo) -> str | None:
        """O escopo do recurso primeiro: é o teto mais específico e o mais acionável.

        Quem vê "teto por recurso alcançado em `pedidos`" sabe o que fazer; quem vê
        o da execução inteira precisa descobrir qual recurso consumiu.
        """
        return self.por_recurso.excedido(recurso, escopo="recurso") or self.por_execucao.excedido(
            execucao, escopo="execução"
        )
