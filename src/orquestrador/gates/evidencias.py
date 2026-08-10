"""Verificação determinística do dossiê: evidência, citações e checklist.

Nada aqui prova que uma regra do dossiê é *verdadeira* — prova que ela é
**conferível**: o arquivo citado existe dentro do backend e a linha citada
existe no arquivo. É a defesa barata contra o modo de falha mais provável do
dossiê, a evidência inventada, e segue o princípio 4: LLM cria, script reprova.

As outras duas checagens são de coerência interna e custam zero I/O: endpoint
citado que o gabarito não declara (a fatia por endpoint nunca o entregaria, e a
regra sumiria em silêncio) e aspecto da checklist negativa sem resposta ("não
procurei" fantasiado de "não encontrei"). As funções puras que respondem isso
moram em `dominio/dossie.py`; aqui elas viram violação com código, na moeda que
o loop de reparo do mapeador sabe gastar.
"""

from __future__ import annotations

from pathlib import Path

from orquestrador.dominio.dossie import (
    DossieDoRecurso,
    aspectos_nao_verificados,
    endpoints_desconhecidos,
)
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.veredito import ResultadoGate, Violacao

NOME = "gate_a"

CODIGO_EVIDENCIA_INVALIDA = "QAORQ-060"
CODIGO_ENDPOINT_DESCONHECIDO = "QAORQ-061"
CODIGO_CHECKLIST_INCOMPLETA = "QAORQ-062"
CODIGO_DOSSIE_AUSENTE = "QAORQ-063"

# O rótulo do dossiê no bundle de reparo do mapeador (`artefato_em_disco`). As
# violações apontam para ele porque é esse o arquivo que o modelo reescreve.
ARQUIVO_DO_DOSSIE = "dossie.json"


def conferir_dossie(
    dossie: DossieDoRecurso | None,
    manifesto: Manifesto,
    backend: Path,
) -> ResultadoGate:
    """O veredito determinístico sobre o dossiê de um recurso.

    Backend inacessível não reprova o artefato: vira `ERRO_DA_FERRAMENTA`, como
    toda checagem que não rodou — o mapeador não conserta um diretório que sumiu.
    """
    if dossie is None:
        return ResultadoGate.reprovado_por(
            [
                Violacao(
                    codigo=CODIGO_DOSSIE_AUSENTE,
                    arquivo=ARQUIVO_DO_DOSSIE,
                    mensagem=(
                        "a saída não contém `dossie`. Emita o dossiê do recurso conforme "
                        "o contrato: regras de negócio com evidência arquivo:linha, "
                        "contrato de erro, parâmetros de consulta, incertezas e a "
                        "checklist negativa."
                    ),
                )
            ],
            gate=NOME,
        )

    raiz = backend.resolve()
    if not raiz.is_dir():
        return ResultadoGate.erro_da_ferramenta(
            f"verificação de evidência sem backend: {backend} não é diretório. "
            "Confira [caminhos].backend.",
            gate=NOME,
        )

    violacoes = [
        *_evidencias_invalidas(dossie, raiz),
        *_endpoints_fantasmas(dossie, manifesto),
        *_checklist_incompleta(dossie),
    ]
    if violacoes:
        return ResultadoGate.reprovado_por(violacoes, gate=NOME)
    return ResultadoGate.aprovado_por(gate=NOME)


def _evidencias_invalidas(dossie: DossieDoRecurso, raiz: Path) -> list[Violacao]:
    # Cache por arquivo: um dossiê cita o mesmo serviço em várias regras, e contar
    # linhas é o custo dominante da checagem.
    linhas_por_arquivo: dict[str, int | None] = {}

    def total_de_linhas(relativo: str) -> int | None:
        if relativo not in linhas_por_arquivo:
            alvo = (raiz / relativo).resolve()
            # Confinamento pelo caminho resolvido, nunca por comparação textual —
            # evidência com `..` que escapasse da raiz leria arquivo alheio.
            if not alvo.is_relative_to(raiz) or not alvo.is_file():
                linhas_por_arquivo[relativo] = None
            else:
                conteudo = alvo.read_text(encoding="utf-8", errors="replace")
                linhas_por_arquivo[relativo] = conteudo.count("\n") + 1
        return linhas_por_arquivo[relativo]

    violacoes: list[Violacao] = []
    for regra in dossie.regras:
        for evidencia in regra.evidencias:
            total = total_de_linhas(evidencia.arquivo)
            if total is None:
                mensagem = (
                    f"a evidência {evidencia.render()} da regra {regra.id} aponta um "
                    "arquivo que não existe no backend. Cite o caminho relativo à raiz "
                    "do backend, exatamente como no inventário — ou remova a regra se "
                    "ela não foi lida da fonte."
                )
            elif evidencia.linha is not None and evidencia.linha > total:
                mensagem = (
                    f"a evidência {evidencia.render()} da regra {regra.id} aponta a "
                    f"linha {evidencia.linha}, mas o arquivo tem {total} linha(s). "
                    "Corrija a linha para onde a regra realmente está."
                )
            else:
                continue
            violacoes.append(
                Violacao(
                    codigo=CODIGO_EVIDENCIA_INVALIDA,
                    arquivo=ARQUIVO_DO_DOSSIE,
                    mensagem=mensagem,
                )
            )
    return violacoes


def _endpoints_fantasmas(dossie: DossieDoRecurso, manifesto: Manifesto) -> list[Violacao]:
    return [
        Violacao(
            codigo=CODIGO_ENDPOINT_DESCONHECIDO,
            arquivo=ARQUIVO_DO_DOSSIE,
            mensagem=(
                f'o dossiê cita "{endpoint}" e o gabarito não declara esse endpoint. '
                "Corrija a grafia para um endpoint de `endpoints` do gabarito, ou "
                "remova a citação: regra sobre rota que não existe não vira teste."
            ),
        )
        for endpoint in endpoints_desconhecidos(dossie, manifesto.endpoints_canonicos())
    ]


def _checklist_incompleta(dossie: DossieDoRecurso) -> list[Violacao]:
    faltantes = aspectos_nao_verificados(dossie)
    if not faltantes:
        return []
    return [
        Violacao(
            codigo=CODIGO_CHECKLIST_INCOMPLETA,
            arquivo=ARQUIVO_DO_DOSSIE,
            mensagem=(
                "a checklist negativa não respondeu: "
                + ", ".join(faltantes)
                + ". Cada aspecto exige uma entrada em `verificacoesNegativas` dizendo "
                "o que foi vasculhado e o que se encontrou — inclusive quando a "
                'resposta é "nenhum".'
            ),
        )
    ]
