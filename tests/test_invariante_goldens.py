"""Saídas congeladas — e a asserção estrutural que sobrevive à regeneração delas.

Por que este arquivo existe
---------------------------
O modo de falha específico de código escrito por IA não é "o teste reprovou": é
**o formato de saída mudar e o teste ser ajustado no mesmo commit**, com a suíte
verde e o contrato quebrado. Nada aqui impedia isso.

Um golden pega a mudança *não intencional*. Ele não pega a mudança *errada* —
quem regenera o golden regenera junto o erro. Por isso cada golden aqui vem em
par com uma **asserção estrutural independente**, que verifica a propriedade em
vez do texto, e que continua reprovando depois de o `.json` ser reescrito.

A ordem importa e é a regra: **a asserção estrutural é escrita primeiro.** Se ela
for difícil de escrever para um artefato, aquele artefato não merece golden —
merece um teste.

O regime de atualização (sem `--update-goldens`, com `.md` ao lado) está em
`tests/goldens/README.md`.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from orquestrador.analise_estatica.extrator_de_superficie import extrair
from orquestrador.config import Config
from orquestrador.dominio.artefatos import (
    ArquivoGerado,
    ArquivoSchema,
    SaidaExecutor,
    SaidaMapeador,
)
from orquestrador.dominio.endpoint import METODOS_HTTP
from orquestrador.dominio.inventario import Endpoint, Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.notas import endpoints_das_notas, rotas_dinamicas_das_notas
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.superficie import SuperficieDoProjeto
from orquestrador.dominio.veredito import Delta, ResultadoGate, Violacao
from orquestrador.observabilidade.registro import configurar_console
from orquestrador.raiz import DIR_FIXTURES

pytestmark = pytest.mark.unit

DIR_GOLDENS = Path(__file__).resolve().parent / "goldens"

# Os contratos que atravessam a fronteira: para o `.mjs` da skill, para o disco do
# consumidor ou para o log. Contrato interno não entra — congelar o que ninguém lê
# de fora só transforma refatoração em churn de golden.
CONTRATOS_PUBLICOS = (
    ArquivoGerado,
    ArquivoSchema,
    Delta,
    Endpoint,
    Inventario,
    Manifesto,
    Recurso,
    ResultadoGate,
    SaidaExecutor,
    SaidaMapeador,
    SuperficieDoProjeto,
    Violacao,
)


# ---------------------------------------------------------------------------
# Comparação
# ---------------------------------------------------------------------------


def conferir(pytestconfig: pytest.Config, nome: str, atual: str) -> None:
    """Compara com o golden e, ao divergir, diz como ver o novo — nunca como gravá-lo."""
    if pytestconfig.getoption("--mostrar-golden") == nome:
        # O console do Windows vem em cp1252, e um golden com acento sairia
        # corrompido justamente no caminho em que ele é copiado para o arquivo.
        # `configurar_console` é o mesmo que a CLI usa, pelo mesmo motivo.
        configurar_console()
        print(f"\n----- {nome} -----\n{atual}----- fim -----")
        pytest.skip(f"--mostrar-golden={nome}: conteúdo impresso, nada gravado")

    arquivo = DIR_GOLDENS / nome
    como_regerar = f"pytest tests/test_invariante_goldens.py -s --mostrar-golden={nome}"
    assert arquivo.is_file(), (
        f"o golden {nome} não existe.\n"
        f"Gere-o com:\n  {como_regerar}\n"
        f"e escreva {arquivo.with_suffix('.md').name} dizendo o que ele prova."
    )
    esperado = arquivo.read_text(encoding="utf-8")
    if atual == esperado:
        return

    diff = list(
        difflib.unified_diff(
            esperado.splitlines(), atual.splitlines(), "golden", "agora", lineterm=""
        )
    )
    corte = "\n  ".join(diff[:60]) + ("\n  … (diff truncado)" if len(diff) > 60 else "")
    pytest.fail(
        f"o golden {nome} divergiu.\n  {corte}\n\n"
        "Se a mudança é intencional:\n"
        f"  1. {como_regerar}\n"
        f"  2. cole a saída em tests/goldens/{nome}\n"
        f"  3. explique a mudança em tests/goldens/{arquivo.stem}.md, no mesmo commit\n"
        "Se você não sabe por que ele mudou, ele está fazendo o trabalho dele: pare aqui.",
        pytrace=False,
    )


@pytest.mark.parametrize("golden", sorted(DIR_GOLDENS.glob("*.json")), ids=lambda p: p.name)
def test_todo_golden_tem_o_md_que_diz_o_que_ele_prova(golden: Path):
    """O `.md` não obriga ninguém a atualizá-lo. Ele faz o esquecimento aparecer.

    `.json` mudou e `.md` não é o padrão que a revisão consegue ver no `git diff` —
    e é o único sinal disponível quando alguém regenera um golden por reflexo.
    """
    explicacao = golden.with_suffix(".md")
    assert explicacao.is_file() and explicacao.read_text(encoding="utf-8").strip(), (
        f"o golden {golden.name} não tem {explicacao.name} ao lado, ou ele está vazio.\n"
        "Escreva de 5 a 15 linhas dizendo o que este golden prova e o que conta como "
        "mudança legítima. Sem isso, a próxima pessoa que o vir divergir não tem "
        "como decidir entre corrigir o código e colar a saída nova."
    )


# ---------------------------------------------------------------------------
# 1 — os contratos públicos
# ---------------------------------------------------------------------------


def sem_descricao(no: Any) -> Any:
    """Remove `description` recursivamente.

    Sem isto, melhorar uma docstring churna o golden — e golden que churna por
    motivo cosmético é golden que se regenera sem ler.
    """
    if isinstance(no, dict):
        return {
            chave: sem_descricao(valor) for chave, valor in no.items() if chave != "description"
        }
    if isinstance(no, list):
        return [sem_descricao(item) for item in no]
    return no


def schemas_do_dominio() -> str:
    documento = {
        modelo.__name__: sem_descricao(modelo.model_json_schema()) for modelo in CONTRATOS_PUBLICOS
    }
    return json.dumps(documento, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def nome_no_schema(nome: str, campo: FieldInfo) -> str:
    """A chave sob a qual este campo aparece no schema JSON."""
    if isinstance(campo.validation_alias, str):
        return campo.validation_alias
    return campo.alias or nome


@pytest.mark.parametrize("modelo", CONTRATOS_PUBLICOS, ids=lambda m: m.__name__)
def test_required_do_schema_e_o_conjunto_de_campos_sem_default(modelo: type[BaseModel]):
    """A asserção estrutural do golden de schemas, e ela vem primeiro.

    `required` é o que o `.mjs` da skill e o próprio Pydantic usam para decidir se
    um documento é aceitável. Derivá-lo aqui do modelo real, em vez de comparar
    texto, é o que sobrevive a alguém regenerar o `.json`: se um campo obrigatório
    virar opcional por descuido, o golden é reescrito junto — esta asserção não.
    """
    esquema = modelo.model_json_schema()
    declarado = set(esquema.get("required", []))

    # O schema JSON sai em modo de **validação**, então a chave é o
    # `validation_alias` quando ele existe — é `code`, e não `codigo`, que o
    # `.mjs` da skill lê. Comparar contra o nome Python daria falso positivo em
    # todo modelo com alias, que são justamente os que atravessam a fronteira.
    obrigatorios = {
        nome_no_schema(nome, campo)
        for nome, campo in modelo.model_fields.items()
        if campo.is_required()
    }
    assert declarado == obrigatorios, (
        f"{modelo.__name__}: o `required` do schema JSON e os campos sem default "
        "discordam.\n"
        f"  só no schema:  {sorted(declarado - obrigatorios)}\n"
        f"  só no modelo:  {sorted(obrigatorios - declarado)}"
    )


def test_golden_dos_contratos_publicos(pytestconfig: pytest.Config):
    conferir(pytestconfig, "schemas-do-dominio.json", schemas_do_dominio())


# ---------------------------------------------------------------------------
# 2 — a superfície do projeto de testes
# ---------------------------------------------------------------------------


@pytest.fixture
def superficie_da_fixture() -> SuperficieDoProjeto:
    config = Config.model_validate(
        {
            "caminhos": {
                "backend": str(DIR_FIXTURES / "backend"),
                "projeto_testes": str(DIR_FIXTURES / "projeto-testes"),
            },
            "estagios": {
                "mapeador": {"modelo": "fake/mapeador"},
                "executor": {"modelo": "fake/executor"},
            },
            "gates": {"a": {}, "b": {}},
        }
    )
    return extrair(config)


EXPORT_JS = re.compile(r"^\s*export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)


def test_todo_export_achado_por_regex_independente_esta_na_superficie(
    superficie_da_fixture: SuperficieDoProjeto,
):
    """A asserção estrutural do golden da superfície, e ela vem primeiro.

    A regex aqui é **outra** implementação, deliberadamente burra: ela só acha
    `export function`, e é justamente por não compartilhar código com
    `exports_javascript.py` que ela serve de segunda opinião. Um export que suma
    do extrator some do golden junto; desta checagem, não.
    """
    raiz = DIR_FIXTURES / "projeto-testes" / "cypress" / "support" / "api"
    esperados = {
        nome
        for arquivo in sorted(raiz.rglob("*.js"))
        for nome in EXPORT_JS.findall(arquivo.read_text(encoding="utf-8"))
    }
    achados = {
        exportado.nome for modulo in superficie_da_fixture.modulos for exportado in modulo.exports
    }

    assert esperados <= achados, (
        f"exports que existem no fixture e sumiram da superfície: "
        f"{sorted(esperados - achados)}.\n"
        "O executor não pode importar o que não lhe foi contado, e o delta do Gate B "
        "('import não resolve') não é acionável."
    )


def test_todo_import_da_superficie_resolve_a_partir_de_onde_sera_escrito(
    superficie_da_fixture: SuperficieDoProjeto,
):
    """Segunda asserção estrutural: a aritmética do `../../..` bate com o disco.

    Os dois campos são caminhos relativos a lugares **diferentes**:
    `import_do_recurso` sai de `cypress/e2e/apis/<recurso>/`, onde ficam os specs, e
    `import_do_support` sai de `cypress/e2e/apis/<recurso>/_support/`, um nível mais
    fundo. Acertar a profundidade de cabeça é a fonte de erro que a superfície
    existe para eliminar — e um `..` a mais ou a menos vira import quebrado no spec
    gerado, com o delta do Gate B dizendo só "não resolve".

    Resolver contra a base documentada é o que torna esta checagem independente do
    golden: se o extrator passar a emitir um nível a menos, o `.json` é reescrito
    junto; esta conta, não.
    """
    projeto = DIR_FIXTURES / "projeto-testes"
    do_recurso = projeto / "cypress" / "e2e" / "apis" / "pedidos"
    do_support = do_recurso / "_support"

    quebrados = [
        f"{modulo.caminho}: {rotulo}={relativo} → {(base / relativo).resolve()}"
        for modulo in superficie_da_fixture.modulos
        for rotulo, base, relativo in (
            ("import_do_recurso", do_recurso, modulo.import_do_recurso),
            ("import_do_support", do_support, modulo.import_do_support),
        )
        if not (base / relativo).resolve().is_file()
    ]
    assert not quebrados, (
        "import da superfície que não resolve a partir de onde o executor o escreve:"
        "\n  " + "\n  ".join(quebrados)
    )


def test_golden_da_superficie(
    pytestconfig: pytest.Config, superficie_da_fixture: SuperficieDoProjeto
):
    conferir(pytestconfig, "superficie-do-projeto.json", superficie_da_fixture.para_json())


# ---------------------------------------------------------------------------
# 3 e 4 — o contrato de fio com a skill
# ---------------------------------------------------------------------------


@pytest.fixture
def saida_do_mapeador() -> SaidaMapeador:
    """A saída aprovada do Bloco 1, montada como o mapeador monta.

    Vem das fixtures de fatia, e não de uma execução, porque o que está sob
    teste é a **serialização**. A montagem espelha a de produção: o inventário
    sai do parse das notas (o mesmo `dominio/notas.py`), e as demais fatias vêm
    dos roteiros aprovados de cada uma.
    """
    base = DIR_FIXTURES / "roteiros" / "pedidos"

    def final_de(fatia: str, tentativa: int) -> dict:
        roteiro = json.loads(
            (base / fatia / f"tentativa-{tentativa:02d}.json").read_text(encoding="utf-8")
        )
        return next(passo for passo in roteiro["passos"] if passo["tipo"] == "final")

    notas = final_de("mapeador", 1)["conteudo"]
    return SaidaMapeador.model_validate(
        {
            "inventario": {
                "recurso": "pedidos",
                "endpoints": [e.model_dump() for e in endpoints_das_notas(notas)],
                "rotas_dinamicas_nao_resolvidas": [
                    r.model_dump() for r in rotas_dinamicas_das_notas(notas)
                ],
            },
            "manifesto": final_de("mapeador-manifesto", 1)["artefato"],
            "schemas": final_de("mapeador-schemas", 1)["artefato"]["schemas"],
            "dossie": final_de("mapeador-dossie", 2)["artefato"],
        }
    )


def test_o_manifesto_serializa_no_dialeto_que_a_skill_le(saida_do_mapeador: SaidaMapeador):
    """A asserção estrutural do golden de cobertura, e ela vem primeiro.

    O `validar-suite-gerada.mjs` lê `camelCase` e não tolera `null` onde espera
    ausência. Estas quatro propriedades são o contrato de fio, e nenhuma delas
    sobrevive a um `by_alias=False` ou a um `exclude_none` esquecido — mudanças que
    o golden pegaria, mas que voltariam junto com ele se fosse regenerado.
    """
    texto = saida_do_mapeador.manifesto.para_json()
    documento = json.loads(texto)

    assert "naoAplica" in json.dumps(documento), "as chaves têm de sair em camelCase (by_alias)"
    assert "null" not in texto, "campo ausente sai omitido, não como null (exclude_none)"
    assert texto.endswith("\n") and "\r" not in texto, "termina em \\n, sem CRLF"
    # Ida e volta: o que o disco carrega é o mesmo objeto que a memória tinha.
    assert Manifesto.model_validate_json(texto) == saida_do_mapeador.manifesto


def test_todo_endpoint_do_inventario_e_unico_e_bem_formado(saida_do_mapeador: SaidaMapeador):
    """A asserção estrutural do golden de inventário."""
    endpoints = saida_do_mapeador.inventario.endpoints
    canonicos = [f"{endpoint.metodo} {endpoint.rota}" for endpoint in endpoints]

    assert len(set(canonicos)) == len(canonicos), f"endpoint repetido: {canonicos}"
    assert all(endpoint.rota.startswith("/") for endpoint in endpoints)
    assert all(endpoint.metodo in METODOS_HTTP for endpoint in endpoints)


def test_golden_do_manifesto(pytestconfig: pytest.Config, saida_do_mapeador: SaidaMapeador):
    conferir(pytestconfig, "cobertura-pedidos.json", saida_do_mapeador.manifesto.para_json())


def test_golden_do_inventario(pytestconfig: pytest.Config, saida_do_mapeador: SaidaMapeador):
    conferir(pytestconfig, "inventario-pedidos.json", saida_do_mapeador.inventario.para_json())
