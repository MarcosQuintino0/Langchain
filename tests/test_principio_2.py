"""Teste de regressão do princípio 2 — o corte que troca O(n²) por O(n).

    prompt_reparo = instrucao_fixa_do_estagio + artefato_atual + delta.violacoes

Este módulo existe para quebrar quando alguém, no futuro, resolver "dar mais
contexto" concatenando o histórico das tentativas anteriores. Ele não olha o
`llm/montagem.py` de perto: espiona o que o **modelo efetivamente recebeu**, que é a
única coisa que importa para o custo.

Cobre os dois estágios de LLM: o mapeador (ReAct, com tools) e o executor
(chamada estruturada, sem tools).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import SystemMessage
from pydantic import Field

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.contratos import Delta, Manifesto, Recurso, Violacao
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.raiz import DIR_FIXTURES
from orquestrador.simulacao import ModeloSimulado, Roteiros

FIXTURES = DIR_FIXTURES


class ModeloEspiao(ModeloSimulado):
    """Registra exatamente as mensagens que chegaram ao modelo."""

    capturas: list = Field(default_factory=list)

    def _generate(self, messages, *args: Any, **kwargs: Any):
        self.capturas.append(list(messages))
        return super()._generate(messages, *args, **kwargs)

    @property
    def instrucao(self) -> str:
        return "\n".join(str(m.content) for m in self.capturas[0] if isinstance(m, SystemMessage))

    @property
    def entrada(self) -> str:
        return "\n".join(
            str(m.content) for m in self.capturas[0] if not isinstance(m, SystemMessage)
        )


def espiao_de(estagio: str) -> ModeloEspiao:
    """Espião que devolve o artefato bom da fixture — o foco é a entrada, não a saída."""
    roteiro = Roteiros(FIXTURES / "roteiros").carregar("pedidos", estagio, 2)
    return ModeloEspiao(passos=roteiro["passos"])


def recurso_de(config) -> Recurso:
    caminho = config.caminhos.recurso("pedidos")
    caminho.mkdir(parents=True, exist_ok=True)
    return Recurso(
        nome="pedidos", caminho_testes=caminho, raiz_schemas=config.caminhos.dir_schemas_abs
    )


def manifesto_de_fixture() -> Manifesto:
    roteiro = Roteiros(FIXTURES / "roteiros").carregar("pedidos", "mapeador", 2)
    return Manifesto.model_validate(roteiro["passos"][-1]["artefato"]["manifesto"])


def delta_de(numero: int, codigo: str) -> Delta:
    return Delta(
        estagio="gate_a" if codigo.startswith("QAAPI-02") else "gate_b",
        recurso="pedidos",
        violacoes=[Violacao(codigo=codigo, mensagem=f"violacao {codigo}")],
        tentativa=numero,
    )


def rodar_mapeador(config, *, delta: Delta | None, artefato: str | None) -> ModeloEspiao:
    espiao = espiao_de("mapeador")
    agente_mapeador.executar(
        config,
        recurso_de(config),
        modelo=espiao,
        telemetria=Telemetria(),
        tentativa=delta.tentativa if delta else 1,
        delta=delta,
        artefato_atual=artefato,
    )
    return espiao


def rodar_executor(config, *, delta: Delta | None, artefato: str | None) -> ModeloEspiao:
    espiao = espiao_de("executor")
    agente_executor.executar(
        config,
        recurso_de(config),
        manifesto_de_fixture(),
        modelo=espiao,
        telemetria=Telemetria(),
        tentativa=delta.tentativa if delta else 1,
        delta=delta,
        artefato_atual=artefato,
    )
    return espiao


RODAR = {"mapeador": rodar_mapeador, "executor": rodar_executor}


@pytest.mark.parametrize("estagio", ["mapeador", "executor"])
def test_reparo_nao_leva_nada_da_tentativa_anterior(config_falso, estagio: str):
    rodar = RODAR[estagio]

    primeira = rodar(
        config_falso,
        delta=delta_de(1, "QAAPI-021"),
        artefato="ARTEFATO-DA-TENTATIVA-1",
    )
    segunda = rodar(
        config_falso,
        delta=delta_de(2, "QAAPI-025"),
        artefato="ARTEFATO-DA-TENTATIVA-2",
    )

    # A entrada do reparo tem o artefato ATUAL e a violação ATUAL...
    assert "ARTEFATO-DA-TENTATIVA-2" in segunda.entrada
    assert "QAAPI-025" in segunda.entrada

    # ...e nada da tentativa anterior. Se alguém concatenar histórico, cai aqui.
    assert "ARTEFATO-DA-TENTATIVA-1" not in segunda.entrada
    assert "QAAPI-021" not in segunda.entrada

    # A instrução fixa é a mesma nas duas: é a parcela constante da fórmula.
    assert segunda.instrucao == primeira.instrucao


@pytest.mark.parametrize("estagio", ["mapeador", "executor"])
def test_entrada_nao_cresce_com_o_numero_da_tentativa(config_falso, estagio: str):
    rodar = RODAR[estagio]
    # Mesmo artefato e violações de tamanho comparável em toda tentativa: se o
    # tamanho da entrada subir, o que cresceu foi histórico.
    tamanhos = [
        len(
            rodar(
                config_falso,
                delta=delta_de(numero, "QAAPI-021"),
                artefato="ARTEFATO",
            ).entrada
        )
        for numero in (1, 2, 3, 7)
    ]
    # A única variação legítima é o dígito do número da tentativa no cabeçalho.
    assert max(tamanhos) - min(tamanhos) <= 1, tamanhos


@pytest.mark.parametrize("estagio", ["mapeador", "executor"])
def test_entrada_de_reparo_cresce_com_o_artefato_e_com_mais_nada(config_falso, estagio: str):
    """O tamanho do reparo é função do artefato atual, não do que veio antes.

    Esta é a formulação honesta do princípio 2. "O reparo é menor que a primeira
    tentativa" NÃO é verdade em geral — no mapeador a primeira entrada é só o nome
    do recurso e dos caminhos, enquanto o reparo carrega o manifesto inteiro. O que
    vale sempre é isto: dobre o artefato e a entrada cresce exatamente o tamanho do
    artefato; repita a tentativa e ela não cresce nada.
    """
    rodar = RODAR[estagio]
    pequeno = rodar(config_falso, delta=delta_de(2, "QAAPI-021"), artefato="x" * 100)
    grande = rodar(config_falso, delta=delta_de(2, "QAAPI-021"), artefato="x" * 1_100)

    assert len(grande.entrada) - len(pequeno.entrada) == 1_000


@pytest.mark.parametrize("estagio", ["mapeador", "executor"])
def test_o_tamanho_da_entrada_vai_para_a_telemetria(config_falso, estagio: str):
    """A2: sem esta medida no registro, a tese do projeto não é verificável."""
    telemetria = Telemetria()
    recurso = recurso_de(config_falso)
    if estagio == "mapeador":
        agente_mapeador.executar(
            config_falso,
            recurso,
            modelo=espiao_de("mapeador"),
            telemetria=telemetria,
            tentativa=1,
        )
    else:
        agente_executor.executar(
            config_falso,
            recurso,
            manifesto_de_fixture(),
            modelo=espiao_de("executor"),
            telemetria=telemetria,
            tentativa=1,
        )

    chamada = telemetria.chamadas[0]
    assert chamada.caracteres_instrucao > 0
    assert chamada.caracteres_entrada > 0

    agregado = telemetria.por_tentativa()[("pedidos", estagio, 1)]
    assert agregado.caracteres_entrada == chamada.caracteres_entrada
    assert "caracteres_entrada" in json.dumps(telemetria.resumo_para_log())
