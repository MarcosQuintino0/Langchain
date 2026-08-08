"""A guarda interrompe antes da chamada, e o código de saída diz que foi o teto.

Três peças com donos diferentes — o domínio decide, a telemetria mede, o agente
interrompe. Estes testes cobrem a costura: que o agente **consulta** antes de
chamar, que a telemetria devolve os dois escopos, e que a interrupção chega à CLI
como `5` e não como erro de ambiente.
"""

from __future__ import annotations

import pytest

from orquestrador.agentes.guarda_de_orcamento import exigir_folga
from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, ORCAMENTO_ESGOTADO
from orquestrador.excecoes import ErroDeFerramenta, OrcamentoEsgotado
from orquestrador.observabilidade.medidas import RegistroDeChamada, RegistroDeTool, UsoDeTokens
from orquestrador.observabilidade.telemetria import Telemetria

pytestmark = pytest.mark.unit


def telemetria_com(*, recurso: str = "pedidos", chamadas: int = 0, tokens: int = 0) -> Telemetria:
    telemetria = Telemetria()
    for _ in range(chamadas):
        telemetria.registrar(
            RegistroDeChamada(
                estagio="mapeador",
                recurso=recurso,
                tentativa=1,
                modelo="fake/modelo",
                uso=UsoDeTokens(entrada=tokens // max(chamadas, 1)),
            )
        )
    return telemetria


def test_sem_orcamento_configurado_a_guarda_nao_faz_nada(config_falso):
    exigir_folga(config_falso, telemetria_com(chamadas=99), estagio="mapeador", recurso="pedidos")


def test_teto_alcancado_interrompe_antes_de_chamar(config_falso):
    config = config_falso.model_copy(
        update={"orcamento": config_falso.orcamento.model_copy(deep=True)}
    )
    config.orcamento.por_recurso.chamadas = 2

    with pytest.raises(OrcamentoEsgotado) as erro:
        exigir_folga(config, telemetria_com(chamadas=2), estagio="mapeador", recurso="pedidos")

    mensagem = str(erro.value)
    assert "por recurso" in mensagem
    # A mensagem precisa dizer que isto não é falha, senão quem lê procura um defeito
    # que não existe.
    assert "não é falha" in mensagem
    assert "--estimar" in mensagem


def test_o_consumo_de_um_recurso_nao_conta_o_do_outro(config_falso):
    """O escopo por recurso existe justamente para isso.

    Sem a separação, o segundo recurso herdaria o gasto do primeiro e o teto por
    recurso viraria um teto por execução com outro nome.
    """
    telemetria = Telemetria()
    for nome in ("pedidos", "clientes"):
        telemetria.registrar(
            RegistroDeChamada(
                estagio="mapeador",
                recurso=nome,
                tentativa=1,
                modelo="fake/m",
                uso=UsoDeTokens(entrada=100),
            )
        )

    assert telemetria.consumo("pedidos").chamadas == 1
    assert telemetria.consumo().chamadas == 2
    assert telemetria.consumo("pedidos").tokens == 100
    assert telemetria.consumo().tokens == 200


def test_o_consumo_soma_o_que_as_tools_devolveram():
    """Resposta de tool é multiplicador, não parcela: ela é reenviada a cada volta."""
    telemetria = Telemetria()
    telemetria.registrar_tool(
        RegistroDeTool(
            estagio="mapeador",
            recurso="pedidos",
            tentativa=1,
            ordem=1,
            nome="ler_arquivo",
            caracteres=4_000,
            duracao_s=0.5,
        )
    )

    assert telemetria.consumo("pedidos").caracteres_de_tools == 4_000
    assert telemetria.consumo("clientes").caracteres_de_tools == 0


def test_orcamento_esgotado_interrompe_a_execucao_como_ferramenta_indisponivel():
    """Herda de `ErroDeFerramenta` para o laço de recursos parar em vez de isolar.

    Insistir no recurso seguinte é ainda mais claramente inútil aqui do que numa
    indisponibilidade: o teto não se recupera sozinho.
    """
    assert issubclass(OrcamentoEsgotado, ErroDeFerramenta)


def test_o_codigo_de_saida_do_teto_e_proprio():
    """`5`, e não `2`. A resposta de quem opera é decidir, não arrumar o ambiente."""
    assert ORCAMENTO_ESGOTADO == 5
    assert ORCAMENTO_ESGOTADO != ERRO_DE_USO
