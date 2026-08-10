"""O JSONL mede, e não transcreve.

O que uma tool do mapeador devolve é código do backend do cliente. Hoje ele não
chega ao log: `observado` grava `len(saida)` e o prefixo `ERRO:`, e o texto morre
ali. Isso não é uma decisão que alguém tomou uma vez — é uma propriedade da forma
de `RegistroDeTool`, e forma muda.

O campo que faltaria é fácil de acrescentar por um bom motivo ("preciso ver o que
a tool devolveu para depurar") e difícil de reverter depois: uma vez que o
conteúdo entra no `execucao.jsonl`, ele está em todo diretório de execução, em
todo artefato de CI e em todo relatório que alguém anexou num chamado.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from orquestrador.observabilidade.medidas import RegistroDeChamada, RegistroDeTool

pytestmark = pytest.mark.unit

# Conjunto fechado, e é o fechamento que faz o teste valer. Campo novo aqui exige
# responder antes: ele carrega conteúdo lido do backend ou do projeto do cliente?
CAMPOS_DE_TOOL = {
    "estagio",
    "recurso",
    "tentativa",
    "ordem",
    "tool_call_id",
    "ordem_solicitada",
    "ordem_inicio",
    "ordem_conclusao",
    "nome",
    "argumentos",
    "caracteres",
    "duracao_s",
    "erro",
}

CAMPOS_DE_CHAMADA = {
    "estagio",
    "recurso",
    "tentativa",
    "modelo",
    "uso",
    "duracao_s",
    "simulado",
    "detalhe",
    "caracteres_instrucao",
    "caracteres_entrada",
    "endpoint",
    "fatia",
    "request_id",
    "status",
    "finish_reason",
    "provedor",
    "custo_reportado",
    "estado",
}


@pytest.mark.parametrize(
    ("modelo", "esperados"),
    [(RegistroDeTool, CAMPOS_DE_TOOL), (RegistroDeChamada, CAMPOS_DE_CHAMADA)],
    ids=["RegistroDeTool", "RegistroDeChamada"],
)
def test_o_registro_mede_e_nao_transcreve(modelo: type[BaseModel], esperados: set[str]):
    """Nenhum campo destes dois registros carrega o texto que atravessou a fronteira.

    `caracteres` responde "quanto" sem responder "o quê", e é o número que importa:
    é ele que entra na próxima volta do ReAct e é reenviado em todas as seguintes.
    `argumentos` carrega o que **pedimos** à tool (caminho, padrão, símbolo), não o
    que ela devolveu.
    """
    reais = set(modelo.model_fields)

    novos = sorted(reais - esperados)
    assert not novos, (
        f"{modelo.__name__} ganhou campo(s): {novos}.\n"
        "Antes de acrescentá-lo à lista deste teste, responda: ele pode carregar "
        "conteúdo lido do backend ou do projeto de testes do cliente?\n"
        "Se puder, ele não pertence a um registro que vai para o `execucao.jsonl` — "
        "que fica em todo diretório de execução, em todo artefato de CI e em todo "
        "relatório anexado num chamado. Meça o tamanho, não transcreva o texto.\n"
        "Se não puder, acrescente-o aqui na mesma mudança, e diga por quê no commit."
    )

    sumidos = sorted(esperados - reais)
    assert not sumidos, (
        f"{modelo.__name__} perdeu campo(s): {sumidos}. Se a remoção é intencional, "
        "atualize esta lista — e confira quem lia esses campos no JSONL."
    )
