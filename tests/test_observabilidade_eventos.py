"""O vocabulário de eventos é fechado, e a documentação dele é gerada.

Por que este arquivo existe
---------------------------
O tipo do evento era literal solto em sete módulos, e o catálogo de `docs/referencia/eventos.md`
era mantido à mão. Duas listas, e elas divergiram duas vezes no mesmo dia: o
A tabela, mantida à mão, omitia `execucao_abortada` e `cypress`, prometia um `artefatos_removidos`
que ninguém emitia, e nunca chegou a mencionar `publicacao`, `staging_mantido` e
`schemas_divergentes`.

O enum resolve metade — passa a existir um dono. A outra metade é esta suíte:

* `test_toda_emissao_usa_o_enum` varre a **AST** de `src/` e exige que o primeiro
  argumento de todo `.evento(...)` seja membro de `TipoDeEvento` (ou, na fase de
  transição, literal com valor de um membro). É esta checagem que substitui a
  conversão que `Registro.evento` deliberadamente **não** faz: converter ali
  levantaria dentro de um `finally` num ramo raro, derrubando a execução para
  reportar um erro de digitação. A AST pega o mesmo defeito antes de rodar,
  inclusive nos ramos que nenhum teste exercita.
* `test_o_catalogo_da_referencia_e_o_gerado` compara o bloco da referência com
  `catalogo_markdown()`. É o que impede a terceira divergência.
"""

from __future__ import annotations

import ast
import io
import json
import re
from pathlib import Path

import pytest
from rich.console import Console

from orquestrador.observabilidade.eventos import (
    ESQUEMA_DOS_EVENTOS,
    TipoDeEvento,
    catalogo_markdown,
)
from orquestrador.observabilidade.registro import Registro

pytestmark = pytest.mark.unit

DIR_TESTES = Path(__file__).resolve().parent
RAIZ_DO_REPOSITORIO = DIR_TESTES.parent
PACOTE = RAIZ_DO_REPOSITORIO / "src" / "orquestrador"
CATALOGO = RAIZ_DO_REPOSITORIO / "docs" / "referencia" / "eventos.md"

INICIO_DO_CATALOGO = "<!-- INICIO DO CATALOGO DE EVENTOS: gerado por observabilidade/eventos.py -->"
FIM_DO_CATALOGO = "<!-- FIM DO CATALOGO DE EVENTOS -->"

# O nome do evento vira chave de filtro (`jq 'select(.tipo=="gate")'`) e nome de
# coluna em planilha. Minúsculas com sublinhado é o que não exige aspas em lugar
# nenhum e o que já vale para os vinte e três de hoje.
NOME_DE_EVENTO = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# O enum
# ---------------------------------------------------------------------------


def test_nomes_sao_unicos():
    valores = [tipo.value for tipo in TipoDeEvento]
    repetidos = sorted({valor for valor in valores if valores.count(valor) > 1})
    assert not repetidos, (
        f"valor de evento repetido: {repetidos}.\n"
        "Dois membros com o mesmo valor viram alias em Python: o segundo some de "
        "`list(TipoDeEvento)` e do catálogo da referência de eventos, mas continua sendo emitido — "
        "um evento documentado como uma coisa e emitido como outra.\n"
        "O que fazer: renomeie o valor, ou apague o membro duplicado."
    )
    assert len(valores) == len(TipoDeEvento.__members__), (
        "há membro de TipoDeEvento que virou alias. Veja a mensagem acima."
    )


@pytest.mark.parametrize("tipo", list(TipoDeEvento), ids=lambda t: t.name)
def test_o_valor_segue_o_padrao_de_nome(tipo: TipoDeEvento):
    assert NOME_DE_EVENTO.match(tipo.value), (
        f"{tipo.name} tem valor {tipo.value!r}, fora de {NOME_DE_EVENTO.pattern}.\n"
        "O tipo do evento é chave de filtro em `jq` e nome de coluna em planilha: "
        "maiúscula, hífen, ponto ou espaço obrigam a citar em toda ferramenta que "
        "lê o log.\n"
        "O que fazer: use minúsculas e sublinhado em src/orquestrador/"
        "observabilidade/eventos.py."
    )


@pytest.mark.parametrize("tipo", list(TipoDeEvento), ids=lambda t: t.name)
def test_todo_evento_tem_descricao(tipo: TipoDeEvento):
    assert tipo.descricao.strip(), (
        f"{tipo.name} não tem descrição.\n"
        "A descrição não é enfeite: é a linha que o catálogo da referência de eventos publica, e é "
        "gerada a partir daqui. Sem ela o catálogo sai com célula vazia.\n"
        "O que fazer: acrescente a descrição de uma linha na tupla do membro, em "
        "src/orquestrador/observabilidade/eventos.py."
    )
    assert "\n" not in tipo.descricao and "|" not in tipo.descricao, (
        f"a descrição de {tipo.name} tem quebra de linha ou `|`.\n"
        "Ela vai para uma célula de tabela Markdown: os dois quebram a tabela "
        "gerada. Reescreva em uma linha, sem barra vertical."
    )


# ---------------------------------------------------------------------------
# Toda emissão usa o enum
# ---------------------------------------------------------------------------


def _emissoes(arquivo: Path) -> list[tuple[int, ast.expr | None]]:
    """`(linha, primeiro argumento)` de cada chamada a `.evento(...)` no módulo."""
    modulo = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    achados: list[tuple[int, ast.expr | None]] = []
    for no in ast.walk(modulo):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        if isinstance(alvo, ast.Attribute) and alvo.attr == "evento":
            achados.append((no.lineno, no.args[0] if no.args else None))
    return achados


def _e_membro_do_enum(argumento: ast.expr | None) -> bool:
    if isinstance(argumento, ast.Attribute):
        return isinstance(argumento.value, ast.Name) and argumento.value.id == "TipoDeEvento"
    if isinstance(argumento, ast.Constant) and isinstance(argumento.value, str):
        # Fase de transição: `agentes/` e `llm/` ainda passam a string literal.
        # Aceita, desde que o valor exista no enum — o que a torna renomeável.
        return argumento.value in {tipo.value for tipo in TipoDeEvento}
    return False


@pytest.mark.parametrize(
    "arquivo",
    sorted(PACOTE.rglob("*.py")),
    ids=lambda p: p.relative_to(PACOTE).as_posix(),
)
def test_toda_emissao_usa_o_enum(arquivo: Path):
    fora = [
        (linha, ast.dump(argumento) if argumento is not None else "(sem argumento)")
        for linha, argumento in _emissoes(arquivo)
        if not _e_membro_do_enum(argumento)
    ]
    assert not fora, (
        f"{arquivo.relative_to(PACOTE).as_posix()} emite evento com tipo que não "
        f"vem de TipoDeEvento: {fora}.\n"
        "String solta é como o catálogo da referência de eventos divergiu duas vezes: o nome nasce "
        "num módulo, ninguém documenta, e um `grep` por aspas não acha o dono.\n"
        "O que fazer: importe TipoDeEvento de "
        "src/orquestrador/observabilidade/eventos.py e passe o membro. Evento novo "
        "entra como membro novo lá — o catálogo da referência de eventos é gerado a partir dele."
    )


# ---------------------------------------------------------------------------
# O catálogo da referência de eventos é gerado
# ---------------------------------------------------------------------------


def test_o_catalogo_da_referencia_e_o_gerado():
    texto = CATALOGO.read_text(encoding="utf-8")
    assert INICIO_DO_CATALOGO in texto and FIM_DO_CATALOGO in texto, (
        "não achei os marcadores do catálogo de eventos no README.md.\n"
        f"Ele fica entre {INICIO_DO_CATALOGO} e {FIM_DO_CATALOGO}. Sem eles esta "
        "checagem fica cega e a lista volta a ser mantida à mão."
    )
    inicio = texto.index(INICIO_DO_CATALOGO) + len(INICIO_DO_CATALOGO)
    publicado = texto[inicio : texto.index(FIM_DO_CATALOGO)].strip()

    assert publicado == catalogo_markdown(), (
        "o catálogo de eventos do README.md divergiu de TipoDeEvento.\n"
        "O que fazer: **não edite a tabela**. Ajuste o enum em "
        "src/orquestrador/observabilidade/eventos.py e cole a saída de:\n"
        '  python -c "from orquestrador.observabilidade.eventos import '
        'catalogo_markdown; print(catalogo_markdown())"\n'
        "entre os marcadores do README."
    )


# ---------------------------------------------------------------------------
# A linha do JSONL
# ---------------------------------------------------------------------------


def test_a_linha_do_jsonl_carrega_versao_e_o_valor_do_enum(tmp_path: Path):
    """`schema_version` na linha, e `tipo` como a string de sempre.

    O enum não pode mudar a forma do log: quem já tem parser de `execucao.jsonl`
    continua lendo `"tipo": "gate"`, não `"TipoDeEvento.GATE"`.
    """
    destino = tmp_path / "execucao.jsonl"
    with Registro(destino, Console(file=io.StringIO())) as registro:
        registro.evento(TipoDeEvento.GATE, recurso="pedidos")

    linha = json.loads(destino.read_text(encoding="utf-8").strip())
    assert linha["tipo"] == "gate"
    assert linha["schema_version"] == ESQUEMA_DOS_EVENTOS
    assert linha["recurso"] == "pedidos"
