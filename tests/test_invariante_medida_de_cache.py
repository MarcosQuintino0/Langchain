"""O que o provedor reaproveitou do prefixo tem que aparecer no log.

Por que este arquivo existe
---------------------------
Numa execução real, o mapeador gastou **1.042.623 tokens de entrada** num único
recurso, e 58% disso era resposta de tool reenviada a cada volta do ReAct. A
pergunta óbvia — "quanto disso o cache do provedor cobriu?" — não tinha resposta:
`UsoDeTokens` só tinha `entrada` e `saida`, e o `cached_tokens` que o OpenRouter
devolve era descartado em silêncio na leitura da mensagem.

O único número que chegou a aparecer veio de um *stack trace* de erro:
`prompt_tokens=15229, cached_tokens=1024` — 6,7%. Otimizar prefixo sem esse
contador é escolher no escuro: não dá para distinguir "o cache não pegou" de "o
cache pegou e o custo é outro".

Os dois contadores, e não só o lido: gravar cache custa **mais** que entrada
normal em vários provedores. Um agente que escreve cache toda volta e nunca o lê
paga a mais para não economizar nada, e sem `cache_escrito` esse caso se parece
com sucesso.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from orquestrador.llm.mensagens import uso_da_mensagem, uso_das_mensagens
from orquestrador.observabilidade.medidas import UsoDeTokens
from orquestrador.observabilidade.tabelas import cache_da_entrada

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Os três dialetos de provedor
# ---------------------------------------------------------------------------


def test_rota_compativel_com_openai_dentro_de_prompt_tokens_details():
    """O formato real medido em produção, com os números reais medidos.

    `cached_tokens` vem aninhado em `prompt_tokens_details`, não solto no uso.
    Ler só o nível de cima devolvia zero e passava por "sem cache".
    """
    mensagem = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 15_229,
                "completion_tokens": 65_536,
                "prompt_tokens_details": {"cached_tokens": 1_024, "cache_write_tokens": 0},
            }
        },
    )

    uso = uso_da_mensagem(mensagem)

    assert uso.entrada == 15_229
    assert uso.saida == 65_536
    assert uso.cache_lido == 1_024
    assert uso.cache_escrito == 0


def test_dialeto_anthropic():
    """`cache_read_input_tokens` e `cache_creation_input_tokens`, soltos no uso."""
    mensagem = AIMessage(
        content="ok",
        response_metadata={
            "usage": {
                "input_tokens": 900,
                "output_tokens": 100,
                "cache_read_input_tokens": 700,
                "cache_creation_input_tokens": 200,
            }
        },
    )

    uso = uso_da_mensagem(mensagem)

    assert (uso.entrada, uso.saida) == (900, 100)
    assert (uso.cache_lido, uso.cache_escrito) == (700, 200)


def test_usage_metadata_normalizado_do_langchain():
    """Quando o LangChain normaliza, o detalhe vira `input_token_details`.

    É o caminho preferido — e era justamente o que ignorava o cache, porque lia
    só `input_tokens` e `output_tokens` do `TypedDict`.
    """
    mensagem = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 1_000,
            "output_tokens": 50,
            "total_tokens": 1_050,
            "input_token_details": {"cache_read": 800, "cache_creation": 100},
        },
    )

    uso = uso_da_mensagem(mensagem)

    assert (uso.entrada, uso.cache_lido, uso.cache_escrito) == (1_000, 800, 100)


# ---------------------------------------------------------------------------
# Ausência de cache continua sendo zero, não ruído
# ---------------------------------------------------------------------------


def test_provedor_que_nao_reporta_cache_da_zero():
    """Silêncio do provedor é zero medido, nunca `None` nem estimativa.

    Um valor inventado aqui viraria "melhoramos o cache" num relatório.
    """
    mensagem = AIMessage(
        content="ok",
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 2}},
    )

    uso = uso_da_mensagem(mensagem)

    assert (uso.cache_lido, uso.cache_escrito) == (0, 0)
    assert uso.taxa_de_cache == 0.0


def test_resposta_sem_uso_nenhum_nao_explode():
    assert uso_da_mensagem(AIMessage(content="ok")) == UsoDeTokens()


# ---------------------------------------------------------------------------
# Cache é subconjunto da entrada, não parcela
# ---------------------------------------------------------------------------


def test_cache_nao_entra_no_total():
    """Somá-lo contaria a mesma entrada duas vezes.

    Um prefixo reaproveitado continua sendo entrada enviada; o que muda é o preço
    dela, não a quantidade.
    """
    uso = UsoDeTokens(entrada=1_000, saida=100, cache_lido=800, cache_escrito=50)

    assert uso.total == 1_100


def test_a_soma_acumula_os_quatro_contadores():
    """`__add__` é como a telemetria agrega por estágio — esquecer um campo ali
    zeraria a medida no relatório, mesmo com a leitura correta na fronteira."""
    a = UsoDeTokens(entrada=10, saida=1, cache_lido=6, cache_escrito=2)
    b = UsoDeTokens(entrada=20, saida=3, cache_lido=15, cache_escrito=0)

    assert a + b == UsoDeTokens(entrada=30, saida=4, cache_lido=21, cache_escrito=2)


def test_uso_das_mensagens_soma_o_cache_da_conversa():
    mensagens: list[BaseMessage] = [
        AIMessage(
            content="a",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "input_token_details": {"cache_read": 0},
            },
        ),
        AIMessage(
            content="b",
            usage_metadata={
                "input_tokens": 200,
                "output_tokens": 20,
                "total_tokens": 220,
                "input_token_details": {"cache_read": 90},
            },
        ),
    ]

    assert uso_das_mensagens(mensagens).cache_lido == 90


def test_taxa_de_cache_sem_entrada_nao_divide_por_zero():
    assert UsoDeTokens().taxa_de_cache == 0.0


# ---------------------------------------------------------------------------
# A tabela mostra o número, senão medir não serviu de nada
# ---------------------------------------------------------------------------


def test_a_celula_traz_token_e_porcentagem():
    """A porcentagem sozinha esconde a escala; o token sozinho esconde a fração."""
    celula = cache_da_entrada(UsoDeTokens(entrada=15_229, cache_lido=1_024))

    assert "1,024" in celula
    assert "7%" in celula


def test_a_celula_denuncia_escrita_de_cache():
    """Escrever muito e ler pouco é o caso que se parece com sucesso no total."""
    celula = cache_da_entrada(UsoDeTokens(entrada=1_000, cache_lido=0, cache_escrito=900))

    assert "900" in celula


def test_estagio_sem_entrada_nao_finge_taxa():
    assert cache_da_entrada(UsoDeTokens()) == "—"
