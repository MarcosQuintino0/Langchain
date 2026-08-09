"""Para onde o código-fonte do cliente pode ir, e o que se pede a quem o recebe.

A fronteira de privacidade tem dois lados. `ferramentas/privacidade.py` cuida do
que **sai do disco** — denylist, `.llmignore`, redação. Aqui é o outro: para qual
host, sob qual política de retenção, e com qual conjunto de provedores por baixo.

O OpenRouter é um roteador. O endpoint é um só, e quem de fato executa a
inferência é escolhido por ele, por requisição. Por isso "mandei para o
openrouter.ai" não responde "quem leu o código", e por isso a política viaja no
corpo de toda chamada em vez de morar numa configuração de conta.
"""

from __future__ import annotations

from typing import Any

# `langchain_openai` no topo é o preço de testar o que `criar_modelo` monta: ela
# o importa tarde de propósito (o SDK da OpenAI é pesado e o dry-run não o usa),
# e substituir o `ChatOpenAI` exige o módulo carregado.
import langchain_openai
import pytest
from pydantic import ValidationError

from orquestrador.config import Config, ConfigEstagio, ConfigOpenRouter
from orquestrador.llm.cliente import criar_modelo

pytestmark = pytest.mark.unit


def config_com(**openrouter: Any) -> Config:
    return Config.model_validate(
        {
            "caminhos": {"skill": ".", "backend": ".", "projeto_testes": "."},
            "estagios": {
                "mapeador": {"modelo": "fake/mapeador"},
                "executor": {"modelo": "fake/executor"},
            },
            "gates": {"a": {"flags": []}, "b": {"flags": []}},
            "openrouter": openrouter,
        }
    )


# ---------------------------------------------------------------------------
# A allowlist de host
# ---------------------------------------------------------------------------


def test_o_padrao_e_so_o_openrouter():
    assert ConfigOpenRouter().hosts_permitidos == ["openrouter.ai"]


def test_base_url_fora_da_allowlist_nao_carrega():
    """Erro de configuração, não aviso — e a distinção é o ponto do item.

    Aviso é lido depois que a execução terminou, e nesse momento o código-fonte já
    foi enviado. A recusa acontece na carga, antes da primeira chamada.
    """
    with pytest.raises(ValidationError, match="hosts_permitidos"):
        ConfigOpenRouter(base_url="https://api.exemplo-nao-declarado.com/v1")  # pyright: ignore[reportArgumentType]


def test_host_novo_passa_quando_e_declarado_junto():
    """A allowlist não impede trocar de provedor. Ela impede trocar em silêncio."""
    config = ConfigOpenRouter(
        base_url="https://gateway.interno.exemplo/v1",  # pyright: ignore[reportArgumentType]
        hosts_permitidos=["gateway.interno.exemplo"],
    )
    assert config.base_url.host == "gateway.interno.exemplo"


def test_a_recusa_chega_pela_carga_da_configuracao_inteira():
    with pytest.raises(ValidationError, match="hosts_permitidos"):
        config_com(base_url="https://outro-lugar.exemplo/v1")


# ---------------------------------------------------------------------------
# A política que viaja na chamada
# ---------------------------------------------------------------------------


def test_por_padrao_pede_provedor_que_nao_retem_dado():
    assert ConfigOpenRouter().roteamento() == {"data_collection": "deny"}


def test_lista_de_provedores_desliga_o_fallback():
    """As duas coisas andam juntas, e é por isso que uma função as monta.

    Restringir a lista sem desligar o fallback não restringe nada: o provedor
    escolhido fica indisponível, o roteador cai para outro qualquer, e a política
    vale exatamente até o primeiro momento em que ela importaria.
    """
    roteamento = ConfigOpenRouter(provedores_permitidos=["provedor-a", "provedor-b"]).roteamento()

    assert roteamento["only"] == ["provedor-a", "provedor-b"]
    assert roteamento["allow_fallbacks"] is False


def test_a_politica_declarada_diz_que_e_declaracao():
    """O nome do campo é parte da honestidade do manifesto.

    O provedor que de fato executou cada chamada viria da resposta, e o
    orquestrador ainda não a lê. Um campo chamado "rota efetiva" prometeria
    evidência; `politica_de_privacidade_declarada` promete o que foi pedido, que é
    o que temos.
    """
    declarada = ConfigOpenRouter().politica_declarada()

    assert declarada["host"] == "openrouter.ai"
    assert declarada["retencao_de_dados"] == "deny"
    assert declarada["fallback_permitido"] is True
    assert declarada["provedores_permitidos"] == []

    restrita = ConfigOpenRouter(provedores_permitidos=["provedor-a"]).politica_declarada()
    assert restrita["fallback_permitido"] is False


def test_retencao_permissiva_e_uma_escolha_explicita():
    """`allow` existe, mas não como padrão nem como fallback silencioso."""
    assert ConfigOpenRouter(retencao_de_dados="allow").roteamento() == {"data_collection": "allow"}
    with pytest.raises(ValidationError):
        ConfigOpenRouter(retencao_de_dados="talvez")  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# O teto de raciocínio
# ---------------------------------------------------------------------------


def corpo_extra(config: Config) -> dict[str, Any]:
    """O `extra_body` que `criar_modelo` monta, sem instanciar o cliente real."""
    capturado: dict[str, Any] = {}

    class ModeloFalso:
        def __init__(self, **kwargs: Any) -> None:
            capturado.update(kwargs)

    original = langchain_openai.ChatOpenAI
    langchain_openai.ChatOpenAI = ModeloFalso
    try:
        criar_modelo(config, "executor")
    finally:
        langchain_openai.ChatOpenAI = original
    return capturado["extra_body"]


def test_sem_teto_de_raciocinio_nada_e_enviado(monkeypatch: pytest.MonkeyPatch):
    """`None` deixa o padrão do provedor. Não inventamos um teto que ninguém pediu."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-de-teste")

    assert "reasoning" not in corpo_extra(config_com())


def test_o_teto_de_raciocinio_viaja_na_chamada(monkeypatch: pytest.MonkeyPatch):
    """Medido: 63.172 tokens de raciocínio de um teto de saída de 65.536.

    Num modelo de raciocínio o pensamento consome o MESMO orçamento da resposta, e
    o sintoma de estourar não é erro de provedor — é `QAORQ-011`, porque o que
    chega é o rascunho truncado no lugar do JSON. Por isso o teto viaja no corpo de
    cada chamada, ao lado da política de privacidade.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-de-teste")
    config = config_com()
    config.estagios["executor"].max_tokens_de_raciocinio = 4_000

    corpo = corpo_extra(config)
    assert corpo["reasoning"] == {"max_tokens": 4_000}
    # Convive com a política de privacidade, que usa a mesma porta.
    assert corpo["provider"]["data_collection"] == "deny"


def test_zero_nao_e_teto_de_raciocinio_valido():
    """Desligar o raciocínio troca um defeito por outro.

    Verificado com o mesmo reparo de 30 mil caracteres: sem raciocínio o modelo
    devolveu `{"recurso": "customers", "arquivos": []}` — estrutura válida e vazia,
    que só não passou porque o contrato exige ao menos um arquivo.
    """
    with pytest.raises(ValidationError):
        ConfigEstagio(modelo="fake/m", max_tokens_de_raciocinio=0)
