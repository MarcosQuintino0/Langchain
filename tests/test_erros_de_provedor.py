"""A fronteira com o provedor: taxonomia, retentativa e o que nunca vira delta.

Três invariantes são verificadas aqui, e as três são sobre **não confundir**:

1. cada falha do provedor é reconhecida na categoria certa a partir do que o
   cliente levanta — status HTTP quando existe, classe da exceção quando não;
2. falha de provedor não entra em `delta.violacoes` e não consome tentativa do
   mini-loop de schema, enquanto **resposta inválida continua indo** para ele;
3. cada volta de retentativa aparece na telemetria com número, espera e custo.

Nenhum teste daqui fala com a rede nem importa o SDK do provedor. As exceções são
dubles que imitam a **forma** que o `openai` expõe — `status_code`, `response`,
`headers`, nome da classe —, que é exatamente a superfície que `llm/cliente.py`
lê. Importar o SDK de verdade só acoplaria o teste à hierarquia dele sem provar
nada a mais, e o cliente o importa tardiamente justamente para não pagá-lo aqui.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ConfigDict

from orquestrador.config import Config, ConfigEstagio, ModoEstruturado
from orquestrador.excecoes import (
    CategoriaDeProvedor,
    ErroDeConfiguracao,
    ErroDeFerramenta,
    ErroDeProvedor,
    FalhaComArtefatos,
    FalhaDeEstagio,
)
from orquestrador.llm.cliente import (
    PoliticaDeRetentativa,
    categorizar,
    chamar_com_retentativas,
    criar_modelo,
    espera_pedida,
    identificador_da_requisicao,
)
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.pipeline import InterrupcaoDaExecucao

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Dubles
# ---------------------------------------------------------------------------


class RespostaFalsa:
    """O `response` que o SDK pendura na exceção: status e cabeçalhos, só."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class ErroDeStatus(Exception):
    """Imita `openai.APIStatusError`: mensagem, `response` e `request_id`."""

    def __init__(
        self,
        mensagem: str = "falhou",
        *,
        status: int,
        headers: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.response = RespostaFalsa(status, headers)
        self.status_code = status
        self.request_id = request_id


class AuthenticationError(ErroDeStatus):
    pass


class RateLimitError(ErroDeStatus):
    pass


class BadRequestError(ErroDeStatus):
    pass


class InternalServerError(ErroDeStatus):
    pass


class APIConnectionError(Exception):
    """Sem status: a conexão caiu antes de existir resposta."""


class APITimeoutError(APIConnectionError):
    """Subclasse de conexão no SDK — a MRO precisa achar o timeout primeiro."""


class SaidaDeTeste(BaseModel):
    """Contrato mínimo do estágio, para o mini-loop de schema ter o que validar."""

    valor: str


class ModeloRoteirizado(BaseChatModel):
    """Devolve, em ordem, textos ou exceções. Conta quantas vezes foi chamado.

    Não reaproveita `simulacao.ModeloSimulado`: aquele lê roteiro de fixture e nunca
    falha, e o que se exercita aqui é justamente a falha.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    roteiro: Sequence[str | Exception] = ()
    chamadas: int = 0

    @property
    def _llm_type(self) -> str:
        return "roteirizado"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        item = self.roteiro[min(self.chamadas, len(self.roteiro) - 1)]
        self.chamadas += 1
        if isinstance(item, Exception):
            raise item
        mensagem = AIMessage(
            content=item,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return ChatResult(generations=[ChatGeneration(message=mensagem)])


class RegistroFalso:
    """Coleta os eventos emitidos, para provar quais não foram."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict[str, Any]]] = []

    def evento(self, tipo: str, **campos: Any) -> None:
        self.eventos.append((str(tipo), campos))

    def tipos(self) -> list[str]:
        return [tipo for tipo, _ in self.eventos]


@pytest.fixture
def esperas() -> list[float]:
    return []


def gerador(
    modelo: BaseChatModel,
    *,
    registro: RegistroFalso,
    politica: PoliticaDeRetentativa | None = None,
    max_tentativas_schema: int = 3,
    modo: ModoEstruturado = "prompt",
) -> GeradorEstruturado:
    return GeradorEstruturado(
        modelo=modelo,
        estagio="executor",
        parametros=ConfigEstagio(
            modelo="fake/executor",
            modo_estruturado=modo,
            max_tentativas_schema=max_tentativas_schema,
        ),
        telemetria=Telemetria(registro=registro),
        registro=registro,
        politica=politica,
    )


# ---------------------------------------------------------------------------
# 1 — cada categoria é reconhecida a partir do que o cliente levanta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("erro", "esperada"),
    [
        (AuthenticationError(status=401), CategoriaDeProvedor.AUTENTICACAO),
        (ErroDeStatus(status=403), CategoriaDeProvedor.AUTENTICACAO),
        (ErroDeStatus(status=402), CategoriaDeProvedor.AUTENTICACAO),
        (RateLimitError(status=429), CategoriaDeProvedor.LIMITE_DE_TAXA),
        (ErroDeStatus(status=408), CategoriaDeProvedor.TEMPO_ESGOTADO),
        (APITimeoutError("estourou"), CategoriaDeProvedor.TEMPO_ESGOTADO),
        (TimeoutError("estourou"), CategoriaDeProvedor.TEMPO_ESGOTADO),
        (InternalServerError(status=500), CategoriaDeProvedor.TRANSITORIO),
        (ErroDeStatus(status=503), CategoriaDeProvedor.TRANSITORIO),
        (APIConnectionError("sem rota"), CategoriaDeProvedor.TRANSITORIO),
        (ConnectionError("recusada"), CategoriaDeProvedor.TRANSITORIO),
        (BadRequestError(status=400), CategoriaDeProvedor.CONTRATO_INVALIDO),
        (ErroDeStatus(status=422), CategoriaDeProvedor.CONTRATO_INVALIDO),
    ],
)
def test_cada_falha_do_provedor_cai_na_categoria_certa(
    erro: Exception, esperada: CategoriaDeProvedor
) -> None:
    assert categorizar(erro) is esperada


def test_o_que_nao_e_falha_de_provedor_nao_e_categorizado() -> None:
    """`None` é o que deixa o `NotImplementedError` seguir para o fallback de modo."""
    assert categorizar(NotImplementedError("sem structured output")) is None
    assert categorizar(ValueError("json inválido")) is None


def test_toda_categoria_diz_o_que_o_operador_faz() -> None:
    for categoria in CategoriaDeProvedor:
        assert len(categoria.orientacao) > 40, categoria
        assert categoria.rotulo


def test_a_mensagem_carrega_categoria_orientacao_e_identificacao() -> None:
    erro = ErroDeProvedor(
        CategoriaDeProvedor.LIMITE_DE_TAXA,
        detalhe="429 Too Many Requests",
        estagio="executor",
        recurso="pedidos",
        tentativas=3,
        request_id="req_123",
        status=429,
    )
    texto = str(erro)
    assert "limite de taxa" in texto
    assert "executor" in texto and "pedidos" in texto
    assert "3 tentativa(s)" in texto
    assert "request_id=req_123" in texto and "HTTP 429" in texto
    assert CategoriaDeProvedor.LIMITE_DE_TAXA.orientacao in texto


def test_erro_de_provedor_e_operacional_e_nao_carrega_violacao() -> None:
    """A herança é o que faz `Pipeline.rodar` interromper em vez de isolar o recurso.

    E o que ela **não** é importa igual: `FalhaComArtefatos` é o ramo que carrega
    `violacoes`, e é por ele que uma falha vira delta. Provedor fora do ar não tem
    onde escrever violação porque não deve produzir nenhuma.
    """
    erro = ErroDeProvedor(CategoriaDeProvedor.TRANSITORIO, detalhe="502")
    assert isinstance(erro, ErroDeFerramenta)
    assert not isinstance(erro, FalhaComArtefatos)
    assert not isinstance(erro, FalhaDeEstagio)


def test_request_id_e_retry_after_saem_dos_cabecalhos() -> None:
    erro = ErroDeStatus(
        status=429, headers={"X-Request-Id": "req_abc", "Retry-After": "7"}, request_id=None
    )
    assert identificador_da_requisicao(erro) == "req_abc"
    assert espera_pedida(erro) == 7.0


def test_retry_after_em_formato_de_data_cai_no_recuo_exponencial() -> None:
    erro = ErroDeStatus(status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert espera_pedida(erro) == 0.0


def test_sem_cabecalho_nenhum_o_id_e_vazio() -> None:
    assert identificador_da_requisicao(APIConnectionError("sem rota")) == ""
    assert espera_pedida(APIConnectionError("sem rota")) == 0.0


# ---------------------------------------------------------------------------
# 2 — retentativa centralizada: número, espera e custo registrados
# ---------------------------------------------------------------------------


def test_categoria_retentavel_repete_e_a_espera_cresce(esperas: list[float]) -> None:
    tentativas: list[int] = []

    def operacao() -> str:
        tentativas.append(len(tentativas) + 1)
        if len(tentativas) < 3:
            raise InternalServerError(status=503)
        return "ok"

    resultado = chamar_com_retentativas(
        operacao,
        politica=PoliticaDeRetentativa(tentativas=3, espera_inicial_s=1.0, fator=2.0),
        dormir=esperas.append,
    )

    assert resultado == "ok"
    assert len(tentativas) == 3
    assert esperas == [1.0, 2.0]


def test_categoria_nao_retentavel_desiste_na_primeira(esperas: list[float]) -> None:
    """401 repetido é só demora antes de o operador ler a mensagem que já existia."""
    chamadas: list[int] = []

    def operacao() -> str:
        chamadas.append(1)
        raise AuthenticationError(status=401, request_id="req_z")

    with pytest.raises(ErroDeProvedor) as capturado:
        chamar_com_retentativas(
            operacao, politica=PoliticaDeRetentativa(tentativas=5), dormir=esperas.append
        )

    assert len(chamadas) == 1
    assert esperas == []
    assert capturado.value.categoria is CategoriaDeProvedor.AUTENTICACAO
    assert capturado.value.tentativas == 1
    assert capturado.value.request_id == "req_z"


def test_retry_after_do_provedor_vence_o_recuo_menor(esperas: list[float]) -> None:
    def operacao() -> str:
        raise RateLimitError(status=429, headers={"retry-after": "30"})

    with pytest.raises(ErroDeProvedor):
        chamar_com_retentativas(
            operacao,
            politica=PoliticaDeRetentativa(tentativas=2, espera_inicial_s=1.0),
            dormir=esperas.append,
        )

    assert esperas == [30.0]


def test_a_espera_tem_teto() -> None:
    politica = PoliticaDeRetentativa(espera_inicial_s=1.0, fator=10.0, espera_maxima_s=5.0)
    assert politica.espera(4) == 5.0
    assert politica.espera(1, sugerida=900.0) == 5.0


def test_excecao_que_nao_e_do_provedor_sobe_intacta(esperas: list[float]) -> None:
    def operacao() -> str:
        raise NotImplementedError("modelo sem structured output")

    with pytest.raises(NotImplementedError):
        chamar_com_retentativas(
            operacao, politica=PoliticaDeRetentativa(tentativas=3), dormir=esperas.append
        )
    assert esperas == []


def test_politica_sem_tentativa_nenhuma_e_configuracao_invalida() -> None:
    with pytest.raises(ErroDeConfiguracao):
        PoliticaDeRetentativa(tentativas=0)


def test_a_politica_sai_do_orcamento_configurado(config_falso: Config) -> None:
    esperado = config_falso.openrouter.max_retries + 1
    assert PoliticaDeRetentativa.do_config(config_falso).tentativas == esperado


def test_cada_volta_perdida_aparece_na_telemetria(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry invisível é o defeito que esta etapa corrige: tem que estar no log."""
    monkeypatch.setattr("orquestrador.llm.cliente.time.sleep", lambda _s: None)
    registro = RegistroFalso()
    modelo = ModeloRoteirizado(
        roteiro=[
            RateLimitError(status=429, headers={"x-request-id": "req_1", "retry-after": "0"}),
            InternalServerError(status=502, request_id="req_2"),
            '{"valor": "pronto"}',
        ]
    )
    gerado = gerador(
        modelo, registro=registro, politica=PoliticaDeRetentativa(tentativas=3, fator=1.0)
    )

    saida = gerado.gerar(
        SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos"
    )

    assert saida.valor == "pronto"
    chamadas = gerado.telemetria.chamadas
    assert len(chamadas) == 3, "as duas voltas perdidas precisam estar registradas"
    perdidas = [c for c in chamadas if "provedor:" in c.detalhe]
    assert [c.detalhe for c in perdidas] == [
        "schema:1; provedor:limite_de_taxa; volta:1/3; espera:1.0s; status:429; request_id:req_1",
        "schema:1; provedor:transitorio; volta:2/3; espera:1.0s; status:502; request_id:req_2",
    ]
    # Custo: a volta perdida não devolve contador, e a bem-sucedida devolve o dela.
    assert all(c.uso.total == 0 for c in perdidas)
    assert all(c.caracteres_instrucao > 0 for c in perdidas)
    assert sum(c.uso.total for c in chamadas) == 15
    assert registro.tipos().count("chamada_llm") == 3


# ---------------------------------------------------------------------------
# 3 — a fronteira: indisponibilidade não é resposta inválida
# ---------------------------------------------------------------------------


def test_falha_de_provedor_nao_vira_delta_nem_gasta_tentativa_de_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orquestrador.llm.cliente.time.sleep", lambda _s: None)
    registro = RegistroFalso()
    modelo = ModeloRoteirizado(roteiro=[InternalServerError(status=503)])
    gerado = gerador(
        modelo,
        registro=registro,
        politica=PoliticaDeRetentativa(tentativas=2, fator=1.0),
        max_tentativas_schema=3,
    )

    with pytest.raises(ErroDeProvedor) as capturado:
        gerado.gerar(SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos")

    assert capturado.value.categoria is CategoriaDeProvedor.TRANSITORIO
    # Duas voltas de retentativa, e **não** três passos de schema: a falha do
    # provedor interrompe o mini-loop em vez de realimentá-lo.
    assert modelo.chamadas == 2
    assert "delta" not in registro.tipos()
    assert not isinstance(capturado.value, FalhaDeEstagio)


def test_resposta_invalida_continua_indo_para_o_mini_loop_de_schema() -> None:
    """O modelo respondeu, respondeu errado: reenviar só o delta é barato e converge."""
    registro = RegistroFalso()
    modelo = ModeloRoteirizado(roteiro=["não sou json", '{"valor": "pronto"}'])
    gerado = gerador(modelo, registro=registro, max_tentativas_schema=3)

    saida = gerado.gerar(
        SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos"
    )

    assert saida.valor == "pronto"
    assert modelo.chamadas == 2
    deltas = [campos for tipo, campos in registro.eventos if tipo == "delta"]
    assert len(deltas) == 1
    assert deltas[0]["estagio"] == "schema"
    assert deltas[0]["codigos"] == ["QAORQ-011"]


def test_modelo_sem_saida_estruturada_cai_para_prompt_em_vez_de_falhar() -> None:
    """`NotImplementedError` não é falha de provedor, e a retentativa não pode engoli-lo.

    O fallback para modo `prompt` é o que mantém o orquestrador portátil entre rotas
    do OpenRouter. Ele mora do lado de fora de `chamar_com_retentativas` justamente
    porque `categorizar` devolve `None` aqui.
    """
    registro = RegistroFalso()
    modelo = ModeloRoteirizado(roteiro=['{"valor": "pronto"}'])
    gerado = gerador(modelo, registro=registro, modo="json_schema")

    saida = gerado.gerar(
        SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos"
    )

    assert saida.valor == "pronto"
    assert modelo.chamadas == 1
    assert "delta" not in registro.tipos()


def test_falha_de_provedor_no_modo_estruturado_tambem_e_traduzida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retentativa vale para os dois mecanismos, não só para o modo `prompt`."""
    monkeypatch.setattr("orquestrador.llm.cliente.time.sleep", lambda _s: None)

    class ModeloEstruturadoQueCai(ModeloRoteirizado):
        def with_structured_output(self, schema: Any = None, **kwargs: Any) -> Runnable[Any, Any]:
            def cair(_entrada: Any) -> Any:
                self.chamadas += 1
                raise RateLimitError(status=429)

            return RunnableLambda(cair)

    registro = RegistroFalso()
    modelo = ModeloEstruturadoQueCai()
    gerado = gerador(
        modelo,
        registro=registro,
        modo="json_schema",
        politica=PoliticaDeRetentativa(tentativas=2, fator=1.0),
    )

    with pytest.raises(ErroDeProvedor) as capturado:
        gerado.gerar(SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos")

    assert capturado.value.categoria is CategoriaDeProvedor.LIMITE_DE_TAXA
    assert modelo.chamadas == 2
    assert "delta" not in registro.tipos()


def test_resposta_invalida_ate_o_fim_e_falha_de_estagio_nao_de_provedor() -> None:
    """O limite do mini-loop continua produzindo `FalhaDeEstagio` — falha do recurso."""
    registro = RegistroFalso()
    modelo = ModeloRoteirizado(roteiro=["{}"])
    gerado = gerador(modelo, registro=registro, max_tentativas_schema=2)

    with pytest.raises(FalhaDeEstagio) as capturado:
        gerado.gerar(SaidaDeTeste, instrucao="instrução fixa", entrada="entrada", recurso="pedidos")

    assert not isinstance(capturado.value, ErroDeProvedor)
    assert modelo.chamadas == 2
    assert registro.tipos().count("delta") == 2


# ---------------------------------------------------------------------------
# 4 — o cliente subjacente não pode repetir escondido
# ---------------------------------------------------------------------------


def test_o_cliente_nao_faz_retry_por_conta_propria(
    config_falso: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_retries=0` no SDK: a repetição é do orquestrador, e por isso é medida.

    O módulo `langchain_openai` é injetado em vez de importado: ele carrega o SDK da
    OpenAI inteiro, e `criar_modelo` o importa tardiamente justamente para nenhum
    teste unitário pagar isso.
    """
    capturado: dict[str, Any] = {}

    class ChatOpenAIFalso(BaseChatModel):
        model_config = ConfigDict(extra="allow")

        def __init__(self, **kwargs: Any) -> None:
            capturado.update(kwargs)
            super().__init__()

        @property
        def _llm_type(self) -> str:
            return "falso"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs: Any,
        ) -> ChatResult:
            raise NotImplementedError

    modulo = types.ModuleType("langchain_openai")
    modulo.ChatOpenAI = ChatOpenAIFalso  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "langchain_openai", modulo)
    monkeypatch.setenv(config_falso.openrouter.api_key_env, "chave-de-teste")

    criar_modelo(config_falso, "executor")

    assert capturado["max_retries"] == 0
    assert capturado["timeout"] == config_falso.openrouter.timeout_s
    # A chave não pode aparecer em `repr` nenhum deste caminho.
    assert "chave-de-teste" not in repr(capturado)


def test_placeholder_de_modelo_falha_antes_de_qualquer_chamada(config_falso: Config) -> None:
    config = config_falso.model_copy(
        update={"estagios": {"executor": ConfigEstagio(modelo="<preencha>")}}
    )
    with pytest.raises(ErroDeConfiguracao, match="placeholder"):
        criar_modelo(config, "executor")


# ---------------------------------------------------------------------------
# O caminho até a CLI
# ---------------------------------------------------------------------------
#
# A exceção não chega à CLI: o laço de recursos a captura para não perder o
# resultado de quem já terminou. Então o que distingue provedor de ferramenta
# precisa sobreviver na interrupção — senão o código de saída mente.


def test_a_interrupcao_carrega_a_categoria_do_provedor():
    erro = ErroDeProvedor(
        CategoriaDeProvedor.LIMITE_DE_TAXA, detalhe="429 rate limit", estagio="mapeador"
    )
    interrupcao = InterrupcaoDaExecucao(
        motivo=str(erro),
        recurso="pedidos",
        categoria_do_provedor=erro.categoria if isinstance(erro, ErroDeProvedor) else None,
    )

    assert interrupcao.categoria_do_provedor is CategoriaDeProvedor.LIMITE_DE_TAXA
    assert interrupcao.categoria_do_provedor.retentavel is True


def test_ferramenta_indisponivel_nao_vira_categoria_de_provedor():
    # A herança faz `ErroDeProvedor` passar pelo mesmo `except` de `ErroDeFerramenta`.
    # Sem o `isinstance`, toda indisponibilidade viraria "espere e repita".
    erro = ErroDeFerramenta("qa-cobertura.mjs não produziu contadores")
    interrupcao = InterrupcaoDaExecucao(
        motivo=str(erro),
        recurso="pedidos",
        categoria_do_provedor=erro.categoria if isinstance(erro, ErroDeProvedor) else None,
    )

    assert interrupcao.categoria_do_provedor is None
