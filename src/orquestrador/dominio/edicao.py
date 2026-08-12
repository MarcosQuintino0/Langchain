"""Edição pontual de um arquivo: o contrato da troca e a aplicação, pura.

Modelo nenhum toca em disco — nem aqui, nem num assistente de código. O que ele
produz é **texto dizendo o que trocar**; quem faz a cirurgia é código. Este módulo
é esse código, e o contrato que o modelo preenche para acioná-lo.

Por que isto existe
-------------------
O executor devolvia sempre o arquivo COMPLETO, porque era a única forma que o
nosso lado sabia receber. Consertar cinco detalhes num spec de 967 linhas
significava redigitar as 967 — e redigitar arquivo grande tem dois defeitos
medidos em 2026-08-11: o modelo resume (57 testes viraram 20) ou entra em laço
(120 mil tokens de saída para corrigir 5 violações, contra 14 mil para gerar o
arquivo do zero).

Trocar um trecho não tem nenhum dos dois: a resposta é curta, e o que não foi
citado não é tocado por construção.

A regra que torna isto seguro
-----------------------------
`antigo` precisa aparecer **exatamente uma vez**. Zero vezes significa que o
modelo parafraseou em vez de copiar; mais de uma significa que a troca é ambígua e
acertaria o lugar errado. Nos dois casos a edição é **recusada**, e o arquivo fica
como estava — falha fechada, como todo gate deste projeto. A violação volta no
ciclo seguinte, o que custa uma tentativa; aplicar uma troca ambígua custaria um
arquivo corrompido que ninguém sabe ler.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from orquestrador.dominio.artefatos import ArquivoGerado


class TrocaDeTexto(BaseModel):
    """Uma substituição pontual, do jeito que o modelo a declara.

    `antigo` é copiado do arquivo, não descrito: é ele que localiza o ponto, e
    descrição não casa com texto.
    """

    model_config = ConfigDict(extra="forbid")

    caminho: str = Field(min_length=1)
    antigo: str = Field(min_length=1)
    novo: str


class SaidaDeReparo(BaseModel):
    """O que o executor devolve quando está consertando, e não criando.

    Dois campos porque há duas naturezas de violação. A esmagadora maioria é de
    **ponto** — título fora do padrão, `expect` sem mensagem, `cy.request` no lugar
    errado — e resolve com troca. Mas "o gabarito prometeu CAT-05 e não existe
    teste nenhum" é **ausência**: não há trecho antigo para citar, e aí o arquivo
    inteiro volta pelo caminho antigo.
    """

    model_config = ConfigDict(extra="forbid")

    trocas: list[TrocaDeTexto] = Field(default_factory=list[TrocaDeTexto])
    arquivos: list[ArquivoGerado] = Field(default_factory=list[ArquivoGerado])


@dataclass(frozen=True)
class ResultadoDaEdicao:
    """O texto final e o que foi recusado, para quem precisar contar."""

    conteudo: str
    aplicadas: int
    recusadas: tuple[str, ...]

    @property
    def mudou(self) -> bool:
        return self.aplicadas > 0


def aplicar(conteudo: str, trocas: list[TrocaDeTexto]) -> ResultadoDaEdicao:
    """Aplica as trocas em ordem, recusando as que não casam exatamente uma vez.

    Em ordem, e não todas de uma vez, porque uma troca pode criar ou destruir o
    trecho que a seguinte procura. Cada uma é conferida contra o estado ATUAL do
    texto — que é o mesmo que o modelo veria se estivesse editando de verdade.
    """
    atual = conteudo
    aplicadas = 0
    recusadas: list[str] = []
    for troca in trocas:
        ocorrencias = atual.count(troca.antigo)
        if ocorrencias != 1:
            recusadas.append(
                f"{'nenhuma ocorrência' if ocorrencias == 0 else f'{ocorrencias} ocorrências'} "
                f"de {_resumo(troca.antigo)}"
            )
            continue
        atual = atual.replace(troca.antigo, troca.novo, 1)
        aplicadas += 1
    return ResultadoDaEdicao(conteudo=atual, aplicadas=aplicadas, recusadas=tuple(recusadas))


def _resumo(trecho: str) -> str:
    """O trecho encurtado para caber numa mensagem de log sem virar parede."""
    de_uma_linha = " ".join(trecho.split())
    if len(de_uma_linha) <= 60:
        return f"`{de_uma_linha}`"
    return f"`{de_uma_linha[:57]}...`"
