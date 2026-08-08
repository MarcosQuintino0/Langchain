"""O nome do recurso vira diretório por concatenação, então ele é tipo, não `str`.

Tudo o que um caminho aceita, um nome de recurso aceitaria: `..`, separador, letra
de unidade e nome de dispositivo do Windows. Cada consumidor concatena por conta
própria, então validar no tipo é a única defesa que vale."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orquestrador.dominio.artefatos import ArquivoGerado, SaidaExecutor
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("nome", ["pedidos", "nota-fiscal", "v2.pedidos", "nf_e", "a", "com"])
def test_nome_de_recurso_aceita_slug(nome: str):
    assert Recurso(nome=nome, caminho_testes=Path("x")).nome == nome


@pytest.mark.parametrize(
    "nome",
    [
        "..",
        ".",
        "../fora",
        "..\\fora",
        "pedidos/sub",
        "pedidos\\sub",
        "C:/pedidos",
        "Pedidos",
        "nota fiscal",
        "-pedidos",
        ".oculto",
        "pedidos.",
        "pedidos ",
        "",
        "pedidos\x00",
    ],
)
def test_nome_de_recurso_recusa_o_que_vira_caminho(nome: str):
    with pytest.raises(ValidationError):
        Recurso(nome=nome, caminho_testes=Path("x"))


@pytest.mark.parametrize("nome", ["con", "nul", "com1", "lpt9", "aux.json", "prn.schema.json"])
def test_nome_de_recurso_recusa_dispositivo_reservado_do_windows(nome: str):
    # Abrir "NUL" não cria arquivo: o Win32 desvia para o dispositivo, e o erro que
    # sai disso não fala de recurso nem de diretório.
    with pytest.raises(ValidationError, match="reservado"):
        Recurso(nome=nome, caminho_testes=Path("x"))


def test_nome_de_recurso_tambem_vale_para_o_que_o_modelo_emite(manifesto_minimo):
    # `manifesto.recurso` volta ao disco como diretório de schema, e quem o preenche
    # é um LLM — a mesma superfície, um estágio depois.
    with pytest.raises(ValidationError):
        Manifesto.model_validate(manifesto_minimo(recurso="../fora"))
    with pytest.raises(ValidationError):
        Inventario.model_validate(
            {
                "recurso": "..",
                "endpoints": [{"metodo": "GET", "rota": "/x", "handler": "a", "arquivo": "x"}],
            }
        )
    with pytest.raises(ValidationError):
        SaidaExecutor(recurso="nul", arquivos=[ArquivoGerado(caminho="crud.cy.js", conteudo="x")])
