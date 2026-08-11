"""O contrato de forma das notas de descoberta.

O parser é o inverso do render da semente estática: o que `_semente_estatica`
escreve, `endpoints_das_notas` lê de volta. É esse espelho que permite ao código
montar o inventário sem pagar o modelo para copiar a própria entrada — e é por
isso que os testes daqui usam linhas no formato exato da semente.
"""

from __future__ import annotations

import pytest

from orquestrador.dominio.notas import (
    endpoints_das_notas,
    endpoints_nao_citados,
    rotas_dinamicas_das_notas,
    schemas_das_notas,
)

pytestmark = pytest.mark.unit

NOTAS = """# Notas de descoberta — pedidos

## Endpoints do recurso
- GET /pedidos | handler PedidoController.listar | src/PedidoController.java:14
- POST /pedidos | handler PedidoController.criar | src/PedidoController.java:31
prosa no meio da seção não vira endpoint
- POST /pedidos | handler Duplicado.criar | src/Outro.java:1

## Rotas dinâmicas não resolvidas
- registrarRotas(prefixo) | src/rotas.js:12 | prefixo calculado em runtime
- nenhuma outra

## Regras de negócio
- RN-01: unicidade de código em POST /pedidos (src/PedidoService.java:45)
"""


def test_parser_le_de_volta_o_que_a_semente_escreve():
    endpoints = endpoints_das_notas(NOTAS)

    assert [e.canonico for e in endpoints] == ["GET /pedidos", "POST /pedidos"]
    assert endpoints[0].handler == "PedidoController.listar"
    assert endpoints[0].arquivo == "src/PedidoController.java"
    assert endpoints[0].linha == 14
    # A duplicata fica de fora aqui; quem reclama de duplicata com mensagem
    # própria é o Inventario, não o parser.
    assert endpoints[1].handler == "PedidoController.criar"


def test_parser_tolera_a_linha_sem_a_palavra_handler():
    """A forma que o modelo real escreveu na primeira execução (2026-08-11).

    Ele copiou as linhas da semente normalizando `handler` para fora, e o parse
    estrito mandava o inventário para o fallback — uma chamada de modelo para
    reobter o que o texto já dizia. O parse aceita liberal; quem valida é o
    `Inventario` e quem reprova é o Gate A.
    """
    notas = (
        "## Endpoints do recurso\n"
        "- POST /api/v1/customers | CustomerController.create | "
        "src/main/java/com/orderflow/api/CustomerController.java:32\n"
        "- GET /api/v1/customers/{id} | `CustomerController.get` | "
        "src/main/java/com/orderflow/api/CustomerController.java:50\n"
    )

    endpoints = endpoints_das_notas(notas)

    assert [e.canonico for e in endpoints] == [
        "POST /api/v1/customers",
        "GET /api/v1/customers/{id}",
    ]
    assert endpoints[0].handler == "CustomerController.create"
    assert endpoints[1].handler == "CustomerController.get"
    assert endpoints[0].linha == 32


def test_secao_ausente_devolve_vazio_para_o_fallback_decidir():
    assert endpoints_das_notas("## Outra seção\n- GET /x | handler h | a.py:1") == []


def test_linha_sem_evidencia_de_linha_ainda_vale():
    notas = "## Endpoints do recurso\n- DELETE /pedidos/{id} | handler remover | src/P.java"
    (endpoint,) = endpoints_das_notas(notas)
    assert endpoint.linha is None
    assert endpoint.arquivo == "src/P.java"


def test_rotas_dinamicas_ignoram_o_marcador_de_nenhuma():
    rotas = rotas_dinamicas_das_notas(NOTAS)

    assert len(rotas) == 1
    assert rotas[0].expressao == "registrarRotas(prefixo)"
    assert rotas[0].arquivo == "src/rotas.js"
    assert rotas[0].linha == 12
    assert rotas[0].motivo == "prefixo calculado em runtime"


SECAO_DE_SCHEMAS = """## Schemas de entrada

### pedidos/entidade.schema.json (POST — PedidoDto.Create)
```json
{"type": "object", "properties": {"situacao": {"type": "string"}}}
```

### `pedidos/patch.schema.json`
```json
{"type": "object"}
```

## Regras de negócio
- RN-01: irrelevante para esta seção
"""


def test_schemas_colados_nas_notas_saem_por_codigo():
    """O conteúdo é copiado byte a byte — redigitar por modelo era o desperdício."""
    schemas = schemas_das_notas(SECAO_DE_SCHEMAS)

    assert schemas is not None
    assert [s.caminho for s in schemas] == [
        "pedidos/entidade.schema.json",
        "pedidos/patch.schema.json",
    ]
    assert '"situacao"' in schemas[0].conteudo
    assert schemas[0].conteudo.endswith("\n")


def test_secao_de_schemas_ausente_manda_ao_fallback():
    assert schemas_das_notas("## Regras de negócio\n- RN-01") is None


def test_json_quebrado_invalida_o_parse_inteiro():
    """Tudo-ou-nada: lista parcial passaria por completa e viraria gate tardio."""
    quebrado = SECAO_DE_SCHEMAS.replace('{"type": "object"}', "{type: quebrado")
    assert schemas_das_notas(quebrado) is None


def test_secao_presente_e_vazia_e_resultado_legitimo():
    """Recurso sem endpoint de escrita não emite schema — vazio não é falha."""
    assert schemas_das_notas("## Schemas de entrada\n\nnenhum\n\n## Erros por endpoint") == []


def test_guarda_de_citacao_aceita_mencao_em_qualquer_secao():
    """O endpoint pode aparecer numa regra em vez da seção própria: menção conta."""
    notas = "## Regras de negócio\n- RN-01 vale para PATCH /pedidos/{id} apenas"

    assert endpoints_nao_citados(notas, ["PATCH /pedidos/{id}", "GET /pedidos"]) == ["GET /pedidos"]
