"""Retomar uma execução anterior a partir dos artefatos que ela deixou em disco.

É o princípio 1 cobrado no próprio produto: se o handoff entre estágios é mesmo
artefato em disco, então os artefatos de ontem bastam para rodar o executor de
hoje, sem re-explorar o backend nem replanejar cenário nenhum.

Para que serve
--------------
Iterar no executor custa o pipeline inteiro — mapeador e planejador incluídos —
quando o que mudou foi só a norma de código ou o prompt do Bloco 2. Reaproveitar
troca dezenas de minutos e o custo dos dois estágios caros por uma execução que
começa direto no Bloco 2, e torna viável o ciclo "melhora, mede, melhora de novo".

O que ele NÃO faz
-----------------
Não reaproveita staging, não reaproveita veredito de gate e não reaproveita
código gerado: o executor roda de novo, do zero, e os dois gates também. O que
volta do disco é só o que foi **decidido** antes — o gabarito, o plano, o dossiê e
o inventário. Reaproveitar o resultado seria carimbar sem verificar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.plano import PlanoDeTestes
from orquestrador.dominio.recurso import Recurso
from orquestrador.excecoes import ErroDeConfiguracao

NOME_DO_MANIFESTO = "manifesto.json"
NOME_DO_PLANO = "plano.json"
NOME_DO_INVENTARIO = "inventario.json"
NOME_DO_DOSSIE = "dossie.json"


@dataclass(frozen=True)
class DecisoesAnteriores:
    """O que uma execução decidiu antes de escrever código.

    `dossie` e `inventario` são opcionais porque o executor funciona sem eles — o
    dossiê enriquece a fatia com as regras citadas, e o inventário alimenta a
    receita de limpeza. Faltando, a execução segue com menos contexto, e o gate
    continua cobrando o que sempre cobrou. O gabarito e o plano, não: sem eles não
    há o que executar.
    """

    manifesto: Manifesto
    plano: PlanoDeTestes
    dossie: DossieDoRecurso | None
    inventario: Inventario | None
    origem: Path


def resolver_execucao(base: Path, run_id: str) -> Path:
    """O diretório de uma execução, recusando run_id que escape da raiz de saída."""
    if not run_id or Path(run_id).name != run_id:
        raise ErroDeConfiguracao(
            f"--reaproveitar recebe só o nome do diretório da execução, não um caminho: {run_id!r}"
        )
    raiz = base.resolve()
    alvo = (raiz / run_id).resolve()
    if not alvo.is_relative_to(raiz) or not alvo.is_dir():
        raise ErroDeConfiguracao(
            f"execução não encontrada em {raiz}: {run_id}\n"
            "`orquestrador execucoes listar` mostra as que existem."
        )
    return alvo


def carregar(dir_execucao: Path, recurso: Recurso) -> DecisoesAnteriores:
    """As decisões daquela execução para este recurso.

    O gabarito tem uma segunda origem possível: até esta versão ele só era escrito
    no `_support/cobertura.json` publicado, então execução antiga não tem a cópia
    no diretório da execução. A cópia é preferida justamente por ser a de então —
    o projeto do cliente pode ter mudado desde ali, e executar um plano contra um
    gabarito diferente do que o produziu é comparar duas coisas.
    """
    origem = dir_execucao / "artefatos" / recurso.nome
    if not origem.is_dir():
        raise ErroDeConfiguracao(
            f"a execução {dir_execucao.name} não tem artefatos do recurso {recurso.nome!r} "
            f"(procurei em {origem}).\n"
            "`orquestrador execucoes mostrar <run_id>` diz quais recursos ela rodou."
        )

    plano = _ler_obrigatorio(origem / NOME_DO_PLANO, PlanoDeTestes)
    manifesto = _ler_opcional(origem / NOME_DO_MANIFESTO, Manifesto)
    if manifesto is None:
        publicado = recurso.caminho_testes / "_support" / "cobertura.json"
        manifesto = _ler_opcional(publicado, Manifesto)
        if manifesto is None:
            raise ErroDeConfiguracao(
                f"não achei o gabarito da execução {dir_execucao.name}: nem "
                f"{origem / NOME_DO_MANIFESTO} nem {publicado}.\n"
                "Execuções anteriores a esta versão só gravavam o gabarito no projeto "
                "publicado; se ele foi removido de lá, essa execução não é reaproveitável."
            )

    if plano.recurso != manifesto.recurso:
        raise ErroDeConfiguracao(
            f"o plano é do recurso {plano.recurso!r} e o gabarito é do recurso "
            f"{manifesto.recurso!r}: são de execuções diferentes."
        )

    return DecisoesAnteriores(
        manifesto=manifesto,
        plano=plano,
        dossie=_ler_opcional(origem / NOME_DO_DOSSIE, DossieDoRecurso),
        inventario=_ler_opcional(origem / NOME_DO_INVENTARIO, Inventario),
        origem=origem,
    )


# Duas funções em vez de um `obrigatorio: bool`: com o parâmetro, o tipo de retorno
# é `T | None` mesmo quando o arquivo é obrigatório, e todo chamador precisaria
# reafirmar ao verificador o que a assinatura já devia ter dito.
def _ler_obrigatorio[T: BaseModel](arquivo: Path, tipo: type[T]) -> T:
    if not arquivo.is_file():
        raise ErroDeConfiguracao(
            f"artefato obrigatório ausente na execução reaproveitada: {arquivo}"
        )
    return _validar(arquivo, tipo)


def _ler_opcional[T: BaseModel](arquivo: Path, tipo: type[T]) -> T | None:
    if not arquivo.is_file():
        return None
    return _validar(arquivo, tipo)


def _validar[T: BaseModel](arquivo: Path, tipo: type[T]) -> T:
    """Arquivo corrompido nunca vira ausência silenciosa: ele levanta."""
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        raise ErroDeConfiguracao(f"{arquivo} não pôde ser lido: {erro}") from erro
    try:
        return tipo.model_validate(bruto)
    except ValidationError as erro:
        raise ErroDeConfiguracao(
            f"{arquivo} não valida contra o contrato de {tipo.__name__}: {erro}\n"
            "O artefato foi escrito por uma versão incompatível do orquestrador."
        ) from erro
