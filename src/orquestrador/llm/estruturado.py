"""Saída estruturada com mini-loop de reparo de schema.

Saída estruturada é tratada como **não confiável**: suporte a `structured output`
e a `tool calling` varia muito entre modelos do OpenRouter. O fluxo é sempre
(1) pedir pelo mecanismo configurado, (2) validar com Pydantic aqui, (3) se
falhar, reenviar **apenas** um delta de estágio `"schema"` — mini-loop de reparo,
praticamente grátis em contexto.

A fronteira que este módulo não deixa borrar: **o modelo respondeu errado e o
provedor não respondeu são coisas diferentes.** A primeira é resposta recebida —
vira `Violacao`, entra no delta de schema e converge em uma ou duas voltas. A
segunda é operacional — vira `ErroDeProvedor` em `llm/cliente.py`, sobe e
interrompe, sem nunca entrar em `delta.violacoes`. Tratar indisponibilidade como
resposta inválida queima o orçamento de tentativas mandando o modelo consertar um
artefato que ele não chegou a produzir.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from orquestrador.config import ConfigEstagio
from orquestrador.dominio.veredito import Delta, Violacao
from orquestrador.excecoes import FalhaDeEstagio, RespostaTruncada
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    TentativaDeProvedor,
    chamar_com_retentativas,
    descrever_volta,
)
from orquestrador.llm.mensagens import medir_mensagens, texto_da_mensagem, uso_da_mensagem
from orquestrador.llm.montagem import LIMITE_PADRAO, montar_entrada_reparo_de_schema
from orquestrador.observabilidade.medidas import RegistroDeChamada, UsoDeTokens
from orquestrador.observabilidade.registro import RegistradorDeEventos
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


# `finish_reason == "length"` é o sinal padrão da API compatível com OpenAI; a
# Anthropic usa `stop_reason == "max_tokens"`. Os dois dizem a mesma coisa, e é
# por isso que a checagem é por conjunto e não por provedor.
_MOTIVOS_DE_TRUNCAMENTO = frozenset({"length", "max_tokens", "MAX_TOKENS"})


def exigir_resposta_inteira(resposta: BaseMessage | None, *, estagio: str, recurso: str) -> None:
    """Levanta `RespostaTruncada` quando o provedor cortou a resposta pelo teto.

    Chamada antes de tentar parsear: uma resposta cortada não é JSON inválido, é
    JSON pela metade, e tratá-la como violação manda o modelo consertar o que ele
    não causou.
    """
    if resposta is None:
        return

    # `response_metadata` é `dict[str, Any]` por construção: o conteúdo é do
    # provedor, e cada um põe o que quer. Ler por uma função que devolve o tipo
    # esperado mantém o `Any` contido aqui, em vez de espalhá-lo pela mensagem.
    meta = cast(dict[str, Any], getattr(resposta, "response_metadata", None) or {})

    def texto(mapa: dict[str, Any], chave: str) -> str | None:
        valor = mapa.get(chave)
        return valor if isinstance(valor, str) else None

    def inteiro(mapa: dict[str, Any], chave: str) -> int | None:
        valor = mapa.get(chave)
        return valor if isinstance(valor, int) else None

    def submapa(mapa: dict[str, Any], chave: str) -> dict[str, Any]:
        valor = mapa.get(chave)
        return cast(dict[str, Any], valor) if isinstance(valor, dict) else {}

    motivo = texto(meta, "finish_reason") or texto(meta, "stop_reason")
    if motivo not in _MOTIVOS_DE_TRUNCAMENTO:
        return

    uso = submapa(meta, "token_usage") or cast(
        dict[str, Any], getattr(resposta, "usage_metadata", None) or {}
    )
    raciocinio = inteiro(submapa(uso, "completion_tokens_details"), "reasoning_tokens")
    gasto = inteiro(uso, "completion_tokens") or inteiro(uso, "output_tokens")

    numeros = ""
    if gasto:
        numeros = f" Gastou {gasto:,} tokens de saída".replace(",", ".")
        if raciocinio:
            numeros += f", dos quais {raciocinio:,} em raciocínio".replace(",", ".")
        numeros += "."

    raise RespostaTruncada(
        f"o provedor cortou a resposta do estágio {estagio} no recurso {recurso!r} "
        f"por limite de tokens ({motivo}).{numeros}\n"
        "Isto não é erro do modelo e não adianta pedir para ele corrigir: a resposta "
        "estava sendo escrita quando o orçamento acabou. Num modelo de raciocínio, o "
        "pensamento consome o MESMO orçamento de saída que a resposta.\n"
        "O que resolve, em ordem: reduzir o tamanho do que se pede numa tentativa; "
        "usar um modelo com teto de saída maior; ou, se o provedor suportar, limitar "
        "o raciocínio em [estagios.*].max_tokens_de_raciocinio — que é mitigação, "
        "não correção."
    )


class GeradorEstruturado:
    """Uma chamada de LLM que precisa devolver um contrato Pydantic válido."""

    def __init__(
        self,
        *,
        modelo: BaseChatModel,
        estagio: str,
        parametros: ConfigEstagio,
        telemetria: Telemetria,
        registro: RegistradorDeEventos | None = None,
        politica: PoliticaDeRetentativa | None = None,
    ) -> None:
        self.modelo = modelo
        self.estagio = estagio
        self.parametros = parametros
        self.telemetria = telemetria
        self.registro = registro
        # Sem política explícita vale o padrão, que espelha o default de
        # `[openrouter].max_retries`. Quem tem a `Config` na mão passa
        # `PoliticaDeRetentativa.do_config(config)` e a configuração volta a mandar.
        self.politica = politica or PoliticaDeRetentativa()
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
            exigir_resposta_inteira(resposta, estagio=self.estagio, recurso=recurso)
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
            # A tarefa original volta junto. Sem ela, a volta seguinte recebia só
            # o fragmento malformado e perdia o que era para construir — e as
            # tentativas eram gastas num pedido impossível de atender.
            #
            # `limite=LIMITE_PADRAO`, e não o padrão de 2.000 da função: a saída do
            # executor tem dezenas de KB, e o corte a 2.000 entregava ao reparo ~5%
            # do próprio trabalho — medido num artefato real de 35 KB com defeito
            # profundo, o reparo truncado entrou em espiral de raciocínio e não
            # convergiu, enquanto o reparo com o artefato inteiro devolveu os 7
            # arquivos byte-idênticos fora do ponto reclamado, numa volta só. O teto
            # de 60 KB preserva a proteção da janela que o corte existia para dar.
            entrada_atual = montar_entrada_reparo_de_schema(
                entrada, ultimo_texto, delta, limite=LIMITE_PADRAO
            )

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
        # Medido antes de chamar: é o tamanho do que **vai** ser enviado, e uma
        # tentativa que morre no provedor precisa aparecer na telemetria com ele.
        instrucao, entrada = medir_mensagens(mensagens)
        tamanhos = (instrucao, entrada)

        if modo != "prompt":
            try:
                estruturado = self.modelo.with_structured_output(
                    tipo, method=_METODOS[modo], include_raw=True
                )
                retorno = self._com_retentativas(
                    lambda: estruturado.invoke(mensagens),
                    recurso=recurso,
                    tentativa=tentativa,
                    passo=passo,
                    tamanhos=tamanhos,
                )
                # Com `include_raw=True` o LangChain devolve sempre o envelope
                # `{"raw", "parsed", "parsing_error"}`. O `BaseModel` solto que a
                # assinatura também admite é o caso `include_raw=False`, que este
                # código não pede: se aparecer, o que chegou já é o objeto parseado e
                # não existe mensagem crua para a telemetria medir.
                envelope: dict[str, Any] = (
                    retorno if isinstance(retorno, dict) else {"parsed": retorno}
                )
                resposta = envelope.get("raw")
                objeto = envelope.get("parsed")
                if objeto is None:
                    problemas = [
                        Violacao(
                            codigo="QAORQ-011",
                            mensagem=str(envelope.get("parsing_error") or "saída não parseável"),
                        )
                    ]
            except NotImplementedError:
                # Modelo do OpenRouter sem suporte ao mecanismo: cai para prompt e
                # não tenta de novo nesta execução.
                self._modo = "prompt"
                modo = "prompt"

        if modo == "prompt":
            resposta = self._com_retentativas(
                lambda: self.modelo.invoke(mensagens),
                recurso=recurso,
                tentativa=tentativa,
                passo=passo,
                tamanhos=tamanhos,
            )
            objeto, problemas = self._parsear(tipo, texto_da_mensagem(resposta))

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

    def _com_retentativas[R](
        self,
        operacao: Callable[[], R],
        *,
        recurso: str,
        tentativa: int,
        passo: int,
        tamanhos: tuple[int, int],
    ) -> R:
        """Chama o provedor registrando cada volta que falhou.

        A volta perdida vira `RegistroDeChamada` como qualquer outra — mesma
        telemetria, mesmo evento `chamada_llm` —, com o tamanho do que foi enviado e
        a duração até a falha. É o que torna a repetição auditável: enquanto ela
        acontecia dentro do cliente, sumia entre duas linhas do log e reaparecia só
        na fatura.

        O uso de token vai zerado de propósito: a chamada que falha não devolve
        contador, e estimar aqui contaminaria a medida do princípio 2, que é feita
        sobre estes mesmos registros.

        O formato da linha de `detalhe` é de `descrever_volta`, em `llm/cliente.py`:
        o mapeador registra a mesma coisa, e dois formatos para a mesma pergunta
        obrigariam quem lê o relatório a conhecer os dois.
        """

        def registrar(volta: TentativaDeProvedor) -> None:
            self.telemetria.registrar(
                RegistroDeChamada(
                    estagio=self.estagio,
                    recurso=recurso,
                    tentativa=tentativa,
                    modelo=self.parametros.modelo,
                    uso=UsoDeTokens(),
                    duracao_s=volta.duracao_s,
                    simulado=getattr(self.modelo, "simulado", False),
                    detalhe=descrever_volta(volta, self.politica, prefixo=f"schema:{passo}"),
                    caracteres_instrucao=tamanhos[0],
                    caracteres_entrada=tamanhos[1],
                )
            )

        return chamar_com_retentativas(
            operacao,
            politica=self.politica,
            estagio=self.estagio,
            recurso=recurso,
            ao_falhar=registrar,
        )

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
