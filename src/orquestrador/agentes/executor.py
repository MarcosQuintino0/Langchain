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
from typing import Any

from orquestrador.config import Config
from orquestrador.contratos import (
    Delta,
    Manifesto,
    Recurso,
    SaidaExecutor,
    SuperficieDoProjeto,
)
from orquestrador.ferramentas.arquivos import confinar
from orquestrador.llm.estruturado import GeradorEstruturado
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.montagem import (
    carregar_prompt,
    esquema_json,
    montar_entrada_inicial,
    montar_entrada_reparo,
)

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
    modelo: Any,
    telemetria: Telemetria,
    registro: Any = None,
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


def escrever(recurso: Recurso, saida: SaidaExecutor) -> list[Path]:
    """Materializa os arquivos no diretório do recurso — o handoff é o disco.

    Cinto e suspensório: o contrato de `ArquivoGerado` já recusa `..` e caminho
    absoluto; `confinar` recusa o que sobra — junction ou symlink dentro do recurso
    apontando para fora dele. Quem decide isso é `ferramentas.arquivos`, dono da
    regra: a comparação textual que vivia aqui aceitava o diretório irmão de prefixo
    comum (`.../pedidos-antigos` passava por `.../pedidos`).
    """
    escritos: list[Path] = []
    raiz = recurso.caminho_testes
    for arquivo in saida.arquivos:
        destino = confinar(raiz, arquivo.caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(arquivo.conteudo, encoding="utf-8", newline="\n")
        escritos.append(destino)
    return escritos


def artefato_em_disco(recurso: Recurso, saida: SaidaExecutor, *, limite: int = 60_000) -> str:
    """Texto do artefato atual para o prompt de reparo (o que está no disco).

    Truncado: o delta precisa do bastante para localizar o erro, não da suíte
    inteira — reenviar tudo é o custo quadrático voltando pela janela.
    """
    partes: list[str] = []
    for arquivo in saida.arquivos:
        caminho = recurso.caminho_testes / arquivo.caminho
        conteudo = (
            caminho.read_text(encoding="utf-8")
            if caminho.is_file()
            else arquivo.conteudo
        )
        partes.append(f"--- {arquivo.caminho} ---\n{conteudo}")
    texto = "\n\n".join(partes)
    if len(texto) > limite:
        texto = texto[:limite] + "\n... (truncado)"
    return texto
