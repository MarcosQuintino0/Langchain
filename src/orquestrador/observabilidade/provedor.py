"""Callback de cada requisição efetiva feita por um modelo LangChain.

Observar o `agent.invoke()` por fora agrega todas as voltas ReAct numa duração só.
Este callback fica na fronteira do chat model: uma abertura e um desfecho para
cada request, inclusive as que o agente dispara internamente. Ele mede e chama a
guarda fornecida; não escolhe retry, modelo ou aprovação.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.medidas import RegistroDeChamada, UsoDeTokens
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

__all__ = ["ObservadorDeProvedor"]


@dataclass(frozen=True)
class _EmAndamento:
    inicio: float
    caracteres_instrucao: int
    caracteres_entrada: int


def _texto(mensagem: BaseMessage) -> str:
    conteudo = mensagem.content
    if isinstance(conteudo, str):
        return conteudo
    return str(conteudo)


def _medir(mensagens: list[list[BaseMessage]]) -> tuple[int, int]:
    planas = [mensagem for lote in mensagens for mensagem in lote]
    if not planas:
        return 0, 0
    return len(_texto(planas[0])), sum(len(_texto(item)) for item in planas[1:])


def _mensagem(resultado: LLMResult) -> BaseMessage | None:
    if not resultado.generations or not resultado.generations[0]:
        return None
    geracao = resultado.generations[0][0]
    return geracao.message if isinstance(geracao, ChatGeneration) else None


def _inteiro(mapa: dict[str, Any], *chaves: str) -> int:
    for chave in chaves:
        valor = mapa.get(chave)
        if isinstance(valor, int) and not isinstance(valor, bool):
            return valor
    return 0


def _uso(mensagem: BaseMessage | None, resultado: LLMResult) -> UsoDeTokens:
    padrao = cast(dict[str, Any], getattr(mensagem, "usage_metadata", None) or {})
    legado = cast(dict[str, Any], (resultado.llm_output or {}).get("token_usage", {}) or {})
    detalhes_entrada = cast(dict[str, Any], padrao.get("input_token_details", {}) or {})
    detalhes_saida = cast(dict[str, Any], padrao.get("output_token_details", {}) or {})
    return UsoDeTokens(
        entrada=_inteiro(padrao, "input_tokens") or _inteiro(legado, "prompt_tokens"),
        saida=_inteiro(padrao, "output_tokens") or _inteiro(legado, "completion_tokens"),
        cache_lido=_inteiro(detalhes_entrada, "cache_read", "cached_tokens"),
        cache_escrito=_inteiro(detalhes_entrada, "cache_creation", "cache_write"),
        raciocinio=_inteiro(detalhes_saida, "reasoning", "reasoning_tokens"),
    )


def _metadados(mensagem: BaseMessage | None) -> dict[str, Any]:
    return cast(dict[str, Any], getattr(mensagem, "response_metadata", None) or {})


class ObservadorDeProvedor(BaseCallbackHandler):
    """Transforma callbacks LangChain em eventos e medidas por request."""

    raise_error = True

    def __init__(
        self,
        *,
        telemetria: Telemetria,
        registro: RegistradorDeEventos | None,
        estagio: str,
        recurso: str,
        tentativa: int,
        modelo: str,
        endpoint: str = "",
        fatia: str = "",
        detalhe: str = "",
        simulado: bool = False,
        antes_de_chamar: Callable[[], None] | None = None,
    ) -> None:
        self.telemetria = telemetria
        self.registro = registro
        self.estagio = estagio
        self.recurso = recurso
        self.tentativa = tentativa
        self.modelo = modelo
        self.endpoint = endpoint
        self.fatia = fatia
        self.detalhe = detalhe
        self.simulado = simulado
        self.antes_de_chamar = antes_de_chamar
        self._em_andamento: dict[UUID, _EmAndamento] = {}
        self._mutex = threading.Lock()
        self.chamadas_finalizadas = 0

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        del serialized, parent_run_id, kwargs
        if self.antes_de_chamar is not None:
            self.antes_de_chamar()
        instrucao, entrada = _medir(messages)
        with self._mutex:
            self._em_andamento[run_id] = _EmAndamento(time.perf_counter(), instrucao, entrada)
        if self.registro is not None:
            self.registro.evento(
                TipoDeEvento.REQUISICAO_LLM_INICIADA,
                request_local_id=str(run_id),
                estagio=self.estagio,
                recurso=self.recurso,
                tentativa=self.tentativa,
                modelo=self.modelo,
                endpoint=self.endpoint,
                fatia=self.fatia,
                caracteres_instrucao=instrucao,
                caracteres_entrada=entrada,
                simulado=self.simulado,
            )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        del parent_run_id, kwargs
        andamento = self._retirar(run_id)
        mensagem = _mensagem(response)
        meta = _metadados(mensagem)
        request_id = str(meta.get("request_id") or getattr(mensagem, "id", "") or "")
        status_bruto = meta.get("status_code")
        status = status_bruto if isinstance(status_bruto, int) else None
        finish_reason = str(meta.get("finish_reason") or meta.get("stop_reason") or "")
        provedor = str(meta.get("provider") or meta.get("model_provider") or "")
        custo_bruto = meta.get("cost")
        custo = float(custo_bruto) if isinstance(custo_bruto, int | float) else None
        uso = _uso(mensagem, response)
        chamada = RegistroDeChamada(
            estagio=self.estagio,
            recurso=self.recurso,
            tentativa=self.tentativa,
            modelo=self.modelo,
            uso=uso,
            duracao_s=time.perf_counter() - andamento.inicio,
            simulado=self.simulado,
            detalhe=self.detalhe or f"request:{run_id}",
            caracteres_instrucao=andamento.caracteres_instrucao,
            caracteres_entrada=andamento.caracteres_entrada,
            endpoint=self.endpoint,
            fatia=self.fatia,
            request_id=request_id,
            status=status,
            finish_reason=finish_reason,
            provedor=provedor,
            custo_reportado=custo,
            estado="concluida",
        )
        self.telemetria.registrar(chamada)
        if self.registro is not None:
            self.registro.evento(
                TipoDeEvento.REQUISICAO_LLM_CONCLUIDA,
                **chamada.model_dump(),
                request_local_id=str(run_id),
            )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        del parent_run_id, kwargs
        andamento = self._retirar(run_id)
        status_bruto = getattr(error, "status_code", None)
        status = status_bruto if isinstance(status_bruto, int) else None
        chamada = RegistroDeChamada(
            estagio=self.estagio,
            recurso=self.recurso,
            tentativa=self.tentativa,
            modelo=self.modelo,
            duracao_s=time.perf_counter() - andamento.inicio,
            simulado=self.simulado,
            detalhe=f"request:{run_id}; erro:{type(error).__name__}",
            caracteres_instrucao=andamento.caracteres_instrucao,
            caracteres_entrada=andamento.caracteres_entrada,
            endpoint=self.endpoint,
            fatia=self.fatia,
            status=status,
            estado="falha",
        )
        if self.registro is not None:
            self.registro.evento(
                TipoDeEvento.REQUISICAO_LLM_FALHOU,
                **chamada.model_dump(),
                request_local_id=str(run_id),
                erro_tipo=type(error).__name__,
            )

    def _retirar(self, run_id: UUID) -> _EmAndamento:
        with self._mutex:
            self.chamadas_finalizadas += 1
            return self._em_andamento.pop(run_id, _EmAndamento(time.perf_counter(), 0, 0))
