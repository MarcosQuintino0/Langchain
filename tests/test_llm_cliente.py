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

import pytest
from pydantic import ValidationError

from orquestrador.config import Config, ConfigOpenRouter

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
