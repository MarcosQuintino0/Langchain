"""Contexto hierárquico de traces e spans, sem decidir o fluxo observado.

O módulo só cria identidade e registra início/fim/falha. A exceção original
sempre continua subindo, e nenhum evento altera aprovação, retry ou orçamento.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Protocol

from orquestrador.observabilidade.eventos import TipoDeEvento

__all__ = ["ContextoDeSpan", "Rastreador", "contexto_atual"]


class _Emissor(Protocol):
    trace_id: str

    def evento(self, tipo: TipoDeEvento | str, **campos: Any) -> None: ...


@dataclass(frozen=True)
class ContextoDeSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None


_CONTEXTO: ContextVar[ContextoDeSpan | None] = ContextVar("span_observabilidade", default=None)


def contexto_atual() -> ContextoDeSpan | None:
    return _CONTEXTO.get()


class Rastreador:
    """Abre operações aninhadas e conserva a árvore em `ContextVar`."""

    def __init__(self, emissor: _Emissor) -> None:
        self.emissor = emissor

    @contextmanager
    def operacao(self, nome: str, **atributos: Any) -> Generator[ContextoDeSpan]:
        pai = contexto_atual()
        contexto = ContextoDeSpan(
            trace_id=pai.trace_id if pai else self.emissor.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=pai.span_id if pai else None,
        )
        token: Token[ContextoDeSpan | None] = _CONTEXTO.set(contexto)
        inicio = time.perf_counter()
        self.emissor.evento(TipoDeEvento.OPERACAO_INICIADA, operacao=nome, **atributos)
        try:
            yield contexto
        except BaseException as erro:
            self.emissor.evento(
                TipoDeEvento.OPERACAO_FALHOU,
                operacao=nome,
                duracao_s=round(time.perf_counter() - inicio, 6),
                erro_tipo=type(erro).__name__,
            )
            raise
        else:
            self.emissor.evento(
                TipoDeEvento.OPERACAO_CONCLUIDA,
                operacao=nome,
                duracao_s=round(time.perf_counter() - inicio, 6),
            )
        finally:
            _CONTEXTO.reset(token)
