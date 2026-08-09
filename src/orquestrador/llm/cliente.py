"""Fronteira com o provedor: cliente OpenRouter, taxonomia de falha e retentativa.

Princípio 6: o nome do modelo de cada estágio vem da configuração. Nenhum nome de
modelo aparece neste arquivo.

Este módulo é dono de **como falar com o provedor e o que fazer quando ele não
responde**. Três decisões moram aqui:

* **A falha do provedor é traduzida antes de subir.** O que o SDK levanta é objeto
  de terceiro, com hierarquia própria; o que o pipeline trata é `ErroDeProvedor`
  com categoria. Sem essa tradução, indisponibilidade de provedor chega ao operador
  como traceback e, pior, chega ao loop de reparo como se o artefato estivesse
  errado.
* **A retentativa é do orquestrador, não do cliente.** `ChatOpenAI` sabe repetir
  sozinho, e repetição que ele faz é invisível: aparece na fatura e não no log. Aqui
  ela é explícita, com número, categoria, espera aplicada e identificador de
  requisição registrados por quem chama.
* **O que este módulo não faz é decidir.** Ele não conhece gate, recurso nem delta;
  quem transforma `ErroDeProvedor` em interrupção é o pipeline, pela herança de
  `ErroDeFerramenta`.

Invariante: nada que saia daqui é violação. `ErroDeProvedor` nunca é `Violacao`, e
resposta que o modelo entregou — mesmo inválida — nunca vira `ErroDeProvedor`: ela
é do mini-loop de schema de `llm/estruturado.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from orquestrador.config import Config
from orquestrador.excecoes import CategoriaDeProvedor, ErroDeConfiguracao, ErroDeProvedor

# Reconhecimento por **código de status**, que é o sinal estável: todo erro de
# resposta do SDK carrega um, e o significado é do protocolo, não da biblioteca.
_POR_STATUS: dict[int, CategoriaDeProvedor] = {
    401: CategoriaDeProvedor.AUTENTICACAO,
    402: CategoriaDeProvedor.AUTENTICACAO,  # sem crédito: mesma ação, mexer na conta
    403: CategoriaDeProvedor.AUTENTICACAO,
    408: CategoriaDeProvedor.TEMPO_ESGOTADO,
    429: CategoriaDeProvedor.LIMITE_DE_TAXA,
}

# Reconhecimento por **nome de classe**, para o que não tem status: timeout e queda
# de conexão acontecem antes de existir resposta. É nome, e não `isinstance`, porque
# importar `openai` aqui no topo desfaz o import tardio de `criar_modelo` — todo
# `--dry-run` e toda coleta de teste pagariam a carga do SDK para classificar um
# erro que não vai acontecer. A tabela é consultada na ordem da MRO, então subclasse
# mais específica (`APITimeoutError`) vence a base (`APIConnectionError`).
_POR_CLASSE: dict[str, CategoriaDeProvedor] = {
    "AuthenticationError": CategoriaDeProvedor.AUTENTICACAO,
    "PermissionDeniedError": CategoriaDeProvedor.AUTENTICACAO,
    "RateLimitError": CategoriaDeProvedor.LIMITE_DE_TAXA,
    "APITimeoutError": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "TimeoutException": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "ReadTimeout": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "ConnectTimeout": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "WriteTimeout": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "PoolTimeout": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "TimeoutError": CategoriaDeProvedor.TEMPO_ESGOTADO,
    "APIConnectionError": CategoriaDeProvedor.TRANSITORIO,
    "InternalServerError": CategoriaDeProvedor.TRANSITORIO,
    "ConnectError": CategoriaDeProvedor.TRANSITORIO,
    "RemoteProtocolError": CategoriaDeProvedor.TRANSITORIO,
    "ReadError": CategoriaDeProvedor.TRANSITORIO,
    "ConnectionError": CategoriaDeProvedor.TRANSITORIO,
    "APIResponseValidationError": CategoriaDeProvedor.CONTRATO_INVALIDO,
    "BadRequestError": CategoriaDeProvedor.CONTRATO_INVALIDO,
    "UnprocessableEntityError": CategoriaDeProvedor.CONTRATO_INVALIDO,
    "NotFoundError": CategoriaDeProvedor.CONTRATO_INVALIDO,
}

ESPERA_MAXIMA_S = 60.0


def criar_modelo(config: Config, estagio: str) -> BaseChatModel:
    """Instancia o modelo do estágio apontando para o OpenRouter."""
    # Import tardio: o dry-run nunca chega aqui, e `langchain_openai` puxa o SDK da
    # OpenAI inteiro. No topo, todo `--dry-run` (e toda coleta de teste unitário)
    # pagaria esse custo de carga para instanciar um modelo que não vai ser usado.
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    parametros = config.estagio(estagio)
    if parametros.modelo.strip().startswith("<"):
        raise ErroDeConfiguracao(
            f"[estagios.{estagio}] ainda está com o placeholder de modelo "
            f"({parametros.modelo!r}). Defina o id do modelo no OpenRouter."
        )

    extras: dict[str, Any] = {}
    if parametros.max_tokens:
        extras["max_tokens"] = parametros.max_tokens
    cabecalhos = config.openrouter.headers()
    if cabecalhos:
        extras["default_headers"] = cabecalhos

    # A política de roteamento viaja no corpo de **toda** chamada, e não numa
    # configuração de conta: o OpenRouter escolhe o provedor por requisição, então
    # uma política que mora em outro lugar não é política — é preferência. O que
    # ela pede está em `ConfigOpenRouter.roteamento`.
    corpo: dict[str, Any] = {"provider": config.openrouter.roteamento()}
    if parametros.max_tokens_de_raciocinio is not None:
        # Num modelo de raciocínio, o pensamento gasta o orçamento de saída. Sem
        # teto, um pedido grande faz o modelo pensar até o limite e devolver a
        # resposta cortada — que chega aqui como `QAORQ-011`, não como erro de
        # provedor. Ver `[estagios.*].max_tokens_de_raciocinio`.
        corpo["reasoning"] = {"max_tokens": parametros.max_tokens_de_raciocinio}
    extras["extra_body"] = corpo
    return ChatOpenAI(
        model=parametros.modelo,
        base_url=str(config.openrouter.base_url),
        # `SecretStr` é o tipo que o `ChatOpenAI` declara. Ele aceita a string crua e
        # embrulha sozinho, mas embrulhar aqui mantém a chave fora de qualquer `repr`
        # intermediário deste módulo — e é a única forma de o verificador conferir.
        api_key=SecretStr(config.openrouter.chave()),
        temperature=parametros.temperatura,
        timeout=config.openrouter.timeout_s,
        # Zero, e não `config.openrouter.max_retries`: repetição feita aqui dentro não
        # passa por telemetria nenhuma — o custo dela aparece na fatura e não no log,
        # e uma indisponibilidade longa vira "chamada demorada" em vez de N tentativas
        # registradas. O orçamento configurado é gasto por `chamar_com_retentativas`,
        # que registra cada volta.
        max_retries=0,
        **extras,
    )


@dataclass(frozen=True)
class PoliticaDeRetentativa:
    """Quantas voltas o orquestrador dá e quanto espera entre elas.

    Os padrões espelham os do `[openrouter]`: quem não passa uma política explícita
    tem o mesmo orçamento de antes, só que visível.
    """

    tentativas: int = 3
    espera_inicial_s: float = 1.0
    fator: float = 2.0
    espera_maxima_s: float = ESPERA_MAXIMA_S

    def __post_init__(self) -> None:
        if self.tentativas < 1:
            raise ErroDeConfiguracao(
                "política de retentativa precisa permitir ao menos uma tentativa "
                f"(recebido {self.tentativas})"
            )

    @classmethod
    def do_config(cls, config: Config) -> PoliticaDeRetentativa:
        """`max_retries` é orçamento de **re**tentativa; a primeira volta não conta."""
        return cls(tentativas=config.openrouter.max_retries + 1)

    def espera(self, numero: int, *, sugerida: float = 0.0) -> float:
        """Recuo exponencial, ou o `Retry-After` do provedor quando ele for maior.

        Ignorar o `Retry-After` é o erro clássico contra limite de taxa: voltar antes
        da janela abrir gasta uma tentativa para receber o mesmo 429.
        """
        recuo = self.espera_inicial_s * (self.fator ** max(0, numero - 1))
        return min(max(recuo, sugerida), self.espera_maxima_s)


@dataclass(frozen=True)
class TentativaDeProvedor:
    """Uma volta que falhou. É o que o chamador registra em telemetria e log."""

    numero: int
    categoria: CategoriaDeProvedor
    detalhe: str
    duracao_s: float
    espera_s: float
    request_id: str = ""
    status: int | None = None
    ultima: bool = False


def descrever_volta(
    volta: TentativaDeProvedor, politica: PoliticaDeRetentativa, *, prefixo: str = ""
) -> str:
    """Linha de `detalhe` de um `RegistroDeChamada` de volta perdida.

    Mora aqui, e não em cada chamador, porque é o formato que se lê no relatório
    para responder "quanto do gasto foi indisponibilidade do provedor?". Dois
    formatos diferentes para a mesma pergunta significam ter de conhecer os dois.

    Categoria, volta, espera, status e request ID viajam num campo de texto porque
    é o único livre de `RegistroDeChamada`. Campo próprio para cada um é mudança de
    `contratos.py` e do formato do JSONL — vale a pena quando alguém precisar
    agregar por categoria, não antes.
    """
    marcas = [
        *([prefixo] if prefixo else []),
        f"provedor:{volta.categoria.value}",
        f"volta:{volta.numero}/{politica.tentativas}",
        f"espera:{volta.espera_s:.1f}s",
    ]
    if volta.status is not None:
        marcas.append(f"status:{volta.status}")
    if volta.request_id:
        marcas.append(f"request_id:{volta.request_id}")
    return "; ".join(marcas)


def status_do_erro(erro: BaseException) -> int | None:
    """Código HTTP que o SDK anexou à exceção, se houver."""
    # Só `status_code`, e não também `code`: no SDK da OpenAI `code` é o código de
    # erro textual ("invalid_api_key"), e em outras bibliotecas é errno. Ler os dois
    # trocaria um sinal preciso por um homônimo.
    valor = getattr(erro, "status_code", None)
    if isinstance(valor, int):
        return valor
    resposta = getattr(erro, "response", None)
    valor = getattr(resposta, "status_code", None)
    return valor if isinstance(valor, int) else None


def identificador_da_requisicao(erro: BaseException) -> str:
    """O id que o suporte do provedor pede — sem ele, o chamado não anda."""
    direto = getattr(erro, "request_id", None)
    if isinstance(direto, str) and direto:
        return direto
    cabecalhos = _cabecalhos(erro)
    for nome in ("x-request-id", "x-openrouter-request-id", "cf-ray"):
        valor = cabecalhos.get(nome)
        if isinstance(valor, str) and valor:
            return valor
    return ""


def espera_pedida(erro: BaseException) -> float:
    """`Retry-After` em segundos, quando o provedor o manda."""
    bruto = _cabecalhos(erro).get("retry-after")
    try:
        return max(0.0, float(str(bruto)))
    except (TypeError, ValueError):
        # `Retry-After` também admite data HTTP. Interpretá-la exigiria confiar no
        # relógio do provedor e no nosso ao mesmo tempo; o recuo exponencial já é um
        # limite superior seguro, então o formato de data cai nele.
        return 0.0


def categorizar(erro: BaseException) -> CategoriaDeProvedor | None:
    """Categoria da falha, ou `None` quando a exceção não é do provedor.

    `None` é parte do contrato: o que não é falha de provedor sobe intacto. É assim
    que o `NotImplementedError` do modelo sem suporte a saída estruturada continua
    chegando a quem faz o fallback para modo `prompt`.
    """
    status = status_do_erro(erro)
    if status is not None:
        if (categoria := _POR_STATUS.get(status)) is not None:
            return categoria
        if status >= 500:
            return CategoriaDeProvedor.TRANSITORIO
        if 400 <= status < 500:
            # 4xx não catalogado é a requisição que montamos sendo recusada pela
            # rota: contrato, não indisponibilidade. Repetir devolveria o mesmo.
            return CategoriaDeProvedor.CONTRATO_INVALIDO
    for classe in type(erro).__mro__:
        if (categoria := _POR_CLASSE.get(classe.__name__)) is not None:
            return categoria
    return None


def chamar_com_retentativas[T](
    operacao: Callable[[], T],
    *,
    politica: PoliticaDeRetentativa,
    estagio: str = "",
    recurso: str = "",
    ao_falhar: Callable[[TentativaDeProvedor], None] | None = None,
    dormir: Callable[[float], None] = time.sleep,
) -> T:
    """Executa `operacao`, repetindo o que é repetível e traduzindo o que não é.

    Categoria que não é retentável sai na primeira volta: insistir num 401 só
    multiplica a espera antes de o operador ler a mensagem que já estava pronta.

    `dormir` é parâmetro porque teste unitário não pode dormir de verdade — e o que
    se quer verificar é a espera **calculada**, não o relógio.
    """
    numero = 0
    while True:
        numero += 1
        inicio = time.perf_counter()
        try:
            return operacao()
        except Exception as erro:
            categoria = categorizar(erro)
            if categoria is None:
                raise
            duracao = time.perf_counter() - inicio
            ultima = numero >= politica.tentativas or not categoria.retentavel
            espera = 0.0 if ultima else politica.espera(numero, sugerida=espera_pedida(erro))
            request_id = identificador_da_requisicao(erro)
            status = status_do_erro(erro)
            if ao_falhar is not None:
                ao_falhar(
                    TentativaDeProvedor(
                        numero=numero,
                        categoria=categoria,
                        detalhe=str(erro),
                        duracao_s=duracao,
                        espera_s=espera,
                        request_id=request_id,
                        status=status,
                        ultima=ultima,
                    )
                )
            if ultima:
                raise ErroDeProvedor(
                    categoria,
                    detalhe=str(erro),
                    estagio=estagio,
                    recurso=recurso,
                    tentativas=numero,
                    request_id=request_id,
                    status=status,
                ) from erro
            dormir(espera)


def _cabecalhos(erro: BaseException) -> Mapping[str, Any]:
    """Cabeçalhos da resposta em minúsculas, ou vazio se a exceção não os tiver."""
    for fonte in (erro, getattr(erro, "response", None)):
        cabecalhos = getattr(fonte, "headers", None)
        if isinstance(cabecalhos, Mapping):
            # `Mapping` sem parâmetro é `Mapping[Unknown, Unknown]` para o
            # verificador: o `isinstance` prova a forma, não os tipos de dentro. O
            # `cast` diz o que a fronteira HTTP garante — chave e valor de terceiro,
            # convertidos para `str` na mesma linha em que saem daqui.
            bruto = cast(Mapping[Any, Any], cabecalhos)
            return {str(chave).lower(): valor for chave, valor in bruto.items()}
    return {}
