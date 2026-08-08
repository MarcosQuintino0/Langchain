"""Bloco 2 — agente executor (LLM, sem tools).

Não é um agente ReAct: é uma chamada de LLM com entrada estruturada. Recebe a
entrada do `cobertura.json` referente a **um recurso** mais a fatia de prompt com
os padrões de código Cypress, e emite os arquivos `.cy.js`.

Aqui está o volume de tokens do pipeline. O trabalho é mecânico se o gabarito for
bom, e o Gate B pega os erros de forma determinística — por isso o modelo deste
estágio pode ser mais barato que o do mapeador.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models import BaseChatModel

from orquestrador.config import Config
from orquestrador.dominio.artefatos import SaidaExecutor
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.superficie import SuperficieDoProjeto
from orquestrador.dominio.veredito import Delta
from orquestrador.ferramentas.publicacao import AreaDeStaging
from orquestrador.llm.cliente import PoliticaDeRetentativa
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.llm.montagem import (
    LIMITE_PADRAO,
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
    recortar_por_violacoes,
)
from orquestrador.observabilidade.registro import RegistradorDeEventos
from orquestrador.observabilidade.telemetria import Telemetria

ESTAGIO = "executor"


def instrucao_do_estagio(
    config: Config, recurso: Recurso, superficie: SuperficieDoProjeto | None = None
) -> str:
    """Instrução fixa do estágio.

    A superfície do projeto entra **aqui**, não na entrada da tentativa: ela é
    constante durante toda a execução, então mantém a instrução idêntica entre
    tentativas (princípio 2 e cache de prompt). Pela entrada, seria reenviada a cada
    reparo — inflando justamente o que o delta existe para enxugar.
    """
    return carregar_prompt(
        ESTAGIO,
        {
            "recurso": recurso.nome,
            "caminho_recurso": str(recurso.caminho_testes),
            "caminho_projeto": str(config.caminhos.projeto_testes),
            "superficie_do_projeto": (
                superficie.render() if superficie else "(superfície não extraída)"
            ),
            "schema_json": esquema_json(SaidaExecutor),
        },
        dir_prompts=config.caminhos.prompts,
    )


def entrada_inicial(recurso: Recurso, manifesto: Manifesto) -> str:
    return montar_entrada_inicial(
        {
            "Recurso alvo": recurso.nome,
            "Diretório do recurso": str(recurso.caminho_testes),
            "Gabarito do recurso (_support/cobertura.json)": (
                f"```json\n{manifesto.para_json().strip()}\n```"
            ),
        }
    )


def executar(
    config: Config,
    recurso: Recurso,
    manifesto: Manifesto,
    *,
    modelo: BaseChatModel,
    telemetria: Telemetria,
    registro: RegistradorDeEventos | None = None,
    tentativa: int = 1,
    delta: Delta | None = None,
    artefato_atual: str | None = None,
    superficie: SuperficieDoProjeto | None = None,
) -> SaidaExecutor:
    """Uma tentativa do executor para um recurso. Sem histórico algum."""
    parametros = config.estagio(ESTAGIO)
    gerador = GeradorEstruturado(
        modelo=modelo,
        estagio=ESTAGIO,
        parametros=parametros,
        telemetria=telemetria,
        registro=registro,
        # Sem isto o orçamento de retentativa configurado em `[openrouter]` não chega
        # ao gerador e vale o padrão da classe — que hoje coincide, mas passaria a
        # divergir em silêncio no dia em que alguém mudasse o arquivo.
        politica=PoliticaDeRetentativa.do_config(config),
    )
    if delta is not None:
        entrada = montar_entrada_reparo(artefato_atual or "(artefato ausente)", delta)
    else:
        entrada = entrada_inicial(recurso, manifesto)

    return gerador.gerar(
        SaidaExecutor,
        instrucao=instrucao_do_estagio(config, recurso, superficie),
        entrada=entrada,
        recurso=recurso.nome,
        tentativa=tentativa,
    )


def escrever(area: AreaDeStaging, saida: SaidaExecutor) -> list[Path]:
    """Materializa os arquivos na área de staging — o handoff é o disco.

    A escrita vai para o staging da execução, nunca para o diretório do recurso: é
    o gate que decide se aquilo chega ao projeto de quem nos contratou, e ele só
    decide depois de rodar. Quem publica é `ferramentas.publicacao`, e é lá que
    mora também o confinamento — `AreaDeStaging.escrever` chama `confinar`, então a
    comparação textual que vivia aqui (e aceitava `.../pedidos-antigos` por
    `.../pedidos`) não voltou por outra porta.
    """
    return [area.escrever(arquivo.caminho, arquivo.conteudo) for arquivo in saida.arquivos]


def artefato_em_disco(
    dir_recurso: Path,
    saida: SaidaExecutor,
    delta: Delta | None = None,
    *,
    limite: int = LIMITE_PADRAO,
) -> str:
    """Texto do artefato atual para o prompt de reparo (o que está no staging).

    Lê do disco, e não da saída do modelo, porque é o disco que o gate mediu. A
    escolha do que cabe é de `llm.montagem.recortar_por_violacoes`: aqui só o I/O.
    """
    arquivos: dict[str, str] = {}
    for arquivo in saida.arquivos:
        caminho = dir_recurso / arquivo.caminho
        arquivos[arquivo.caminho] = (
            caminho.read_text(encoding="utf-8") if caminho.is_file() else arquivo.conteudo
        )
    return recortar_por_violacoes(arquivos, delta.violacoes if delta else [], limite=limite)
