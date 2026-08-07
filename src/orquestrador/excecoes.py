"""Exceções do pipeline, num lugar só.

A distinção que importa é **quem errou**:

* `ErroDeFerramenta` e derivadas — erro de invocação **nosso**: comando mal
  montado, executável ausente, tempo esgotado, exit 2 do validador. Nunca vira
  delta para o LLM; o pipeline falha alto para que seja corrigido no código.
* `FalhaDeEstagio` e `FalhaDeGate` — o LLM não entregou artefato válido dentro do
  limite de tentativas. Falha **daquele recurso**, não da execução: `Pipeline`
  captura, registra e segue para o próximo recurso.

As duas últimas carregam os arquivos que ficaram em disco em estado reprovado.
Os arquivos não são apagados (apagar arquivo do usuário é pior que deixar, e o
artefato reprovado é o que se quer inspecionar) — mas o efeito não pode ser
silencioso, então quem falha diz o que deixou para trás.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # evita ciclo de import em tempo de execução
    from orquestrador.contratos import Violacao


class ErroDeFerramenta(RuntimeError):
    """Falha ao invocar uma ferramenta externa (ausente, timeout, uso inválido)."""


class ExecutavelAusente(ErroDeFerramenta):
    """Executável não encontrado no PATH."""


class ErroDeInvocacao(ErroDeFerramenta):
    """Exit code 2 do validador: erro de uso nosso, não reprovação do artefato."""


class ProjetoNaoPreparado(ErroDeFerramenta):
    """O projeto de testes não tem os módulos compartilhados que o executor consome.

    Pré-condição, não falha de recurso: o orquestrador **gera testes** num projeto
    já preparado; preparar o projeto é outro fluxo da skill
    (`references/preparar-projeto.md`). Falha cedo, antes de qualquer chamada de
    modelo — substituir em silêncio pela arquitetura-base produziria imports que não
    existem no projeto do usuário, e o loop de reparo não converge sobre isso.
    """


class FalhaComArtefatos(RuntimeError):
    """Falha de recurso que pode ter deixado artefato reprovado em disco."""

    def __init__(
        self,
        mensagem: str,
        *,
        arquivos: Iterable[Path] | None = None,
        violacoes: "Iterable[Violacao] | None" = None,
    ) -> None:
        super().__init__(mensagem)
        self.arquivos: list[Path] = list(arquivos or ())
        self.violacoes: list = list(violacoes or ())

    @property
    def codigos(self) -> list[str]:
        return sorted({violacao.codigo for violacao in self.violacoes})


class FalhaDeEstagio(FalhaComArtefatos):
    """O estágio não produziu artefato válido dentro do limite de tentativas.

    Cobre tanto a saída que não valida contra o contrato Pydantic quanto o
    estouro do limite de passos do agente ReAct.
    """


class FalhaDeGate(FalhaComArtefatos):
    """Um gate reprovou em todas as tentativas permitidas."""
