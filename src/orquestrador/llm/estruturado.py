"""Saída estruturada com mini-loop de reparo de schema.

Saída estruturada é tratada como **não confiável**: suporte a `structured output`
e a `tool calling` varia muito entre modelos do OpenRouter. O fluxo é sempre
(1) pedir pelo mecanismo configurado, (2) validar com Pydantic aqui, (3) se
falhar, reenviar **apenas** um delta de estágio `"schema"` — mini-loop de reparo,
praticamente grátis em contexto.
"""

from __future__ import annotations

import time
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from orquestrador.config import ConfigEstagio
from orquestrador.contratos import Delta, RegistroDeChamada, UsoDeTokens, Violacao
from orquestrador.excecoes import FalhaDeEstagio
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.llm.mensagens import medir_mensagens, texto_da_mensagem, uso_da_mensagem
from orquestrador.llm.montagem import montar_entrada_reparo
from orquestrador.observabilidade.telemetria import Telemetria

T = TypeVar("T", bound=BaseModel)

# Tradução do vocabulário da config para o do langchain-openai.
_METODOS = {
    "json_schema": "json_schema",
    "json_object": "json_mode",
    "tools": "function_calling",
}


def violacoes_de_validacao(erro: ValidationError) -> list[Violacao]:
    """Erros do Pydantic viram violações — a mesma moeda dos gates."""
    violacoes: list[Violacao] = []
    for detalhe in erro.errors():
        caminho = ".".join(str(parte) for parte in detalhe.get("loc", ())) or "(raiz)"
        violacoes.append(
            Violacao(
                codigo="QAORQ-010",
                mensagem=f"{caminho}: {detalhe.get('msg', 'valor inválido')}",
            )
        )
    return violacoes


class GeradorEstruturado:
    """Uma chamada de LLM que precisa devolver um contrato Pydantic válido."""

    def __init__(
        self,
        *,
        modelo: BaseChatModel,
        estagio: str,
        parametros: ConfigEstagio,
        telemetria: Telemetria,
        registro=None,
    ) -> None:
        self.modelo = modelo
        self.estagio = estagio
        self.parametros = parametros
        self.telemetria = telemetria
        self.registro = registro
        self._modo = parametros.modo_estruturado

    def gerar(
        self,
        tipo: type[T],
        *,
        instrucao: str,
        entrada: str,
        recurso: str,
        tentativa: int = 1,
    ) -> T:
        entrada_atual = entrada
        ultimo_texto = ""
        ultimas_violacoes: list[Violacao] = []

        for passo in range(1, self.parametros.max_tentativas_schema + 1):
            mensagens: list[BaseMessage] = [
                SystemMessage(content=instrucao),
                HumanMessage(content=entrada_atual),
            ]
            resposta, objeto, erro = self._invocar(tipo, mensagens, recurso, tentativa, passo)
            ultimo_texto = texto_da_mensagem(resposta) or ultimo_texto

            if objeto is not None:
                return objeto

            ultimas_violacoes = erro or [
                Violacao(codigo="QAORQ-011", mensagem="resposta vazia do modelo")
            ]
            if self.registro:
                self.registro.evento(
                    "delta",
                    estagio="schema",
                    de=self.estagio,
                    recurso=recurso,
                    tentativa=passo,
                    codigos=[violacao.codigo for violacao in ultimas_violacoes],
                )
            delta = Delta(
                estagio="schema",
                recurso=recurso,
                violacoes=ultimas_violacoes,
                tentativa=passo,
            )
            # Princípio 2, também aqui: só o artefato atual e as violações.
            entrada_atual = montar_entrada_reparo(ultimo_texto or "(vazio)", delta)

        detalhes = "; ".join(violacao.render() for violacao in ultimas_violacoes)
        raise FalhaDeEstagio(
            f"estágio {self.estagio} não produziu {tipo.__name__} válido para o recurso "
            f"{recurso!r} em {self.parametros.max_tentativas_schema} tentativa(s) de schema. "
            f"Últimas violações: {detalhes}"
        )

    # -- internos -----------------------------------------------------------

    def _invocar(
        self,
        tipo: type[T],
        mensagens: list[BaseMessage],
        recurso: str,
        tentativa: int,
        passo: int,
    ) -> tuple[BaseMessage | None, T | None, list[Violacao] | None]:
        inicio = time.perf_counter()
        modo = self._modo
        resposta: BaseMessage | None = None
        objeto: T | None = None
        problemas: list[Violacao] | None = None

        if modo != "prompt":
            try:
                estruturado = self.modelo.with_structured_output(
                    tipo, method=_METODOS[modo], include_raw=True
                )
                retorno = estruturado.invoke(mensagens)
                resposta = retorno.get("raw")
                objeto = retorno.get("parsed")
                if objeto is None:
                    problemas = [
                        Violacao(
                            codigo="QAORQ-011",
                            mensagem=str(retorno.get("parsing_error") or "saída não parseável"),
                        )
                    ]
            except NotImplementedError:
                # Modelo do OpenRouter sem suporte ao mecanismo: cai para prompt e
                # não tenta de novo nesta execução.
                self._modo = "prompt"
                modo = "prompt"

        if modo == "prompt":
            resposta = self.modelo.invoke(mensagens)
            objeto, problemas = self._parsear(tipo, texto_da_mensagem(resposta))

        instrucao, entrada = medir_mensagens(mensagens)
        self.telemetria.registrar(
            RegistroDeChamada(
                estagio=self.estagio,
                recurso=recurso,
                tentativa=tentativa,
                modelo=self.parametros.modelo,
                uso=uso_da_mensagem(resposta) if resposta else UsoDeTokens(),
                duracao_s=time.perf_counter() - inicio,
                simulado=getattr(self.modelo, "simulado", False),
                detalhe=f"schema:{passo}",
                caracteres_instrucao=instrucao,
                caracteres_entrada=entrada,
            )
        )
        return resposta, objeto, problemas

    @staticmethod
    def _parsear(tipo: type[T], texto: str) -> tuple[T | None, list[Violacao] | None]:
        try:
            dados = extrair_json(texto)
        except ValueError as erro:  # JSONDecodeError herda de ValueError
            return None, [
                Violacao(
                    codigo="QAORQ-011",
                    mensagem=(
                        f"a resposta não é um objeto JSON válido ({erro}). "
                        "Responda APENAS com o JSON do contrato, sem prosa em volta."
                    ),
                )
            ]
        try:
            return tipo.model_validate(dados), None
        except ValidationError as erro:
            return None, violacoes_de_validacao(erro)
