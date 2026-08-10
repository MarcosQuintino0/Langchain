"""O verificador do dossiê: evidência conferível, citação real, checklist cheia.

O dossiê é a fonte com que o planejador afirma códigos exatos sem ler o backend.
Estas invariantes protegem a única coisa que o torna confiável: tudo que ele
afirma é conferível por script, e o que não for vira delta — nunca aprovação.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.veredito import VereditoDeGate
from orquestrador.gates.evidencias import conferir_dossie

pytestmark = pytest.mark.unit

FONTE = "class PedidoService {\n  void criar() {\n  }\n}\n"


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    raiz = tmp_path / "backend"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src" / "PedidoService.java").write_text(FONTE, encoding="utf-8")
    return raiz


def manifesto() -> Manifesto:
    return Manifesto.model_validate(
        {
            "recurso": "pedidos",
            "endpoints": [{"endpoint": "POST /pedidos", "cats": ["CAT-01"]}],
        }
    )


def dossie_valido(**mudancas) -> DossieDoRecurso:
    base = {
        "recurso": "pedidos",
        "regras": [
            {
                "id": "RN-01",
                "resumo": "criação valida o corpo",
                "efeito": "POST inválido responde 400 e nada é gravado",
                "evidencias": [{"arquivo": "src/PedidoService.java", "linha": 2}],
                "endpoints": ["POST /pedidos"],
            }
        ],
        "verificacoesNegativas": [
            {"aspecto": "campos-derivados", "resultado": "nenhum; resposta espelha o gravado"},
            {"aspecto": "maquina-de-estados", "resultado": "nenhuma; sem campo de status"},
            {"aspecto": "regras-condicionais-entre-campos", "resultado": "nenhuma; campo único"},
            {
                "aspecto": "efeitos-colaterais-em-outros-recursos",
                "resultado": "nenhum; não toca outra entidade",
            },
        ],
    }
    base.update(mudancas)
    return DossieDoRecurso.model_validate(base)


def test_dossie_integro_aprova(backend: Path):
    resultado = conferir_dossie(dossie_valido(), manifesto(), backend)

    assert resultado.aprovado


def test_dossie_ausente_reprova_com_qaorq_063(backend: Path):
    resultado = conferir_dossie(None, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-063"]


def test_evidencia_de_arquivo_inexistente_reprova(backend: Path):
    dossie = dossie_valido()
    dossie.regras[0].evidencias[0].arquivo = "src/NaoExiste.java"

    resultado = conferir_dossie(dossie, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-060"]
    assert "NaoExiste.java" in resultado.violacoes[0].mensagem


def test_evidencia_de_linha_alem_do_fim_reprova(backend: Path):
    dossie = dossie_valido()
    dossie.regras[0].evidencias[0].linha = 999

    resultado = conferir_dossie(dossie, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-060"]
    assert "999" in resultado.violacoes[0].mensagem


def test_evidencia_que_escapa_da_raiz_reprova(backend: Path, tmp_path: Path):
    """`..` que sai do backend não pode nem ser lido, quanto mais aprovar."""
    fora = tmp_path / "segredo.txt"
    fora.write_text("conteúdo alheio", encoding="utf-8")
    dossie = dossie_valido()
    dossie.regras[0].evidencias[0].arquivo = "../segredo.txt"
    dossie.regras[0].evidencias[0].linha = None

    resultado = conferir_dossie(dossie, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-060"]


def test_endpoint_citado_fora_do_gabarito_reprova(backend: Path):
    dossie = dossie_valido()
    dossie.regras[0].endpoints.append("PUT /pedidos/{id}")

    resultado = conferir_dossie(dossie, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-061"]


def test_checklist_incompleta_reprova_nomeando_o_que_falta(backend: Path):
    dossie = dossie_valido(verificacoesNegativas=[])

    resultado = conferir_dossie(dossie, manifesto(), backend)

    assert resultado.codigos == ["QAORQ-062"]
    assert "campos-derivados" in resultado.violacoes[0].mensagem


def test_backend_inacessivel_e_erro_de_ferramenta_nao_veredito(tmp_path: Path):
    resultado = conferir_dossie(dossie_valido(), manifesto(), tmp_path / "nao-existe")

    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
