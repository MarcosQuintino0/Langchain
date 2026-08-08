"""Entrypoint de linha de comando.

    python -m orquestrador --dry-run --recurso pedidos
    python -m orquestrador --recurso pedidos --config config.toml

Só argumentos, montagem da execução e apresentação: os loops de controle vivem em
`pipeline.py`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from orquestrador.config import Config
from orquestrador.contratos import EstadoDoRecurso, Recurso
from orquestrador.excecoes import ErroDeConfiguracao, ErroDeFerramenta, ErroDeProvedor
from orquestrador.ferramentas.publicacao import remover_criados
from orquestrador.observabilidade import manifesto_de_execucao, tabelas
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import (
    Registro,
    configurar_console,
    diretorio_de_execucao,
)
from orquestrador.pipeline import NAO_EXECUTADO, Pipeline, ResultadoDoRecurso
from orquestrador.raiz import ARQUIVO_ENV, DIR_FIXTURES, RAIZ_PROJETO
from orquestrador.simulacao import Roteiros, preparar_sandbox

# Códigos de saída. `2` cobre tudo que é erro do operador ou indisponibilidade da
# ferramenta — configuração inválida, invocação impossível, comando que ainda não
# existe. O que importa é não ser 0: em CI, 0 é indistinguível de trabalho feito.
#
# `3` é o terceiro estado do recurso: tudo passou, o artefato foi publicado, e
# alguma coisa precisa de olho humano — hoje, schema do consumidor que não declara
# campo que existe no backend. Não é 0 porque um pipeline verde esconderia a
# revisão pendente, e não é 1 porque nenhum gate reprovou e não há nada para o
# modelo consertar.
SUCESSO = 0
FALHA_DE_GATE = 1
ERRO_DE_USO = 2
REQUER_REVISAO = 3
# Indisponibilidade do provedor de LLM tem código próprio porque a resposta do
# operador é outra: `2` pede para arrumar a configuração ou o ambiente, `4` pede
# para esperar e repetir. Num agendamento, é a diferença entre alertar alguém e
# reenfileirar sozinho.
ERRO_DE_PROVEDOR = 4

MARCA_DO_ESTADO: dict[EstadoDoRecurso, str] = {
    EstadoDoRecurso.APROVADO: "[green]OK[/green]",
    EstadoDoRecurso.REPROVADO: "[red]FALHOU[/red]",
    EstadoDoRecurso.REQUER_REVISAO: "[yellow]REVISAR[/yellow]",
}


def montar_recursos(config: Config, nomes: list[str]) -> list[Recurso]:
    return [
        Recurso(
            nome=nome,
            caminho_testes=config.caminhos.recurso(nome),
            raiz_schemas=config.caminhos.dir_schemas_abs,
            caminhos_backend=[config.caminhos.backend],
        )
        for nome in nomes
    ]


def inteiro_positivo(texto: str) -> int:
    """Tipo do argparse para limite que não faz sentido em zero nem negativo.

    Sem ele, `--max-tentativas 0` era aceito pelo parser e depois descartado por um
    `if` de truthiness — o pipeline rodava com o limite do arquivo, silenciosamente
    diferente do que a linha de comando pediu.
    """
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} não é um número inteiro") from None
    if valor < 1:
        raise argparse.ArgumentTypeError(f"precisa ser >= 1 (recebido {valor})")
    return valor


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        prog="python -m orquestrador",
        description="Orquestrador multi-agente de testes de API (skill qa-api).",
    )
    analisador.add_argument("--config", type=Path, default=None, help="arquivo de configuração")
    analisador.add_argument(
        "--recurso",
        action="append",
        default=[],
        dest="recursos",
        help="nome do recurso (repetível). No dry-run, o padrão é todos os das fixtures.",
    )
    analisador.add_argument(
        "--dry-run",
        action="store_true",
        help="roda ponta a ponta sem chamar nenhum modelo, usando fixtures.",
    )
    analisador.add_argument(
        "--max-tentativas",
        type=inteiro_positivo,
        default=None,
        help="sobrescreve max_tentativas de todos os gates (inteiro >= 1).",
    )
    analisador.add_argument(
        "--rodar-cypress",
        action="store_true",
        help="executa o Cypress no Bloco 3 (por padrão é pulado).",
    )
    analisador.add_argument(
        "--auditor",
        action="store_true",
        help="RECUSADO enquanto o auditor semântico for stub: encerra com erro.",
    )
    analisador.add_argument(
        "--remover-reprovados",
        action="store_true",
        help=(
            "apaga o que ESTA ferramenta criou nos recursos que não terminaram "
            "aprovados. Nunca toca em arquivo preexistente nem em arquivo que "
            "mudou desde que o criamos."
        ),
    )
    return analisador.parse_args(argv)


def recusar_indisponiveis(args: argparse.Namespace, console: Console) -> int | None:
    """Recusa, antes de qualquer trabalho, as flags que hoje mentiriam.

    `--auditor` encerrava com código 0 imprimindo a descrição do stub. Código 0 é a
    frase "auditoria feita" na única linguagem que a CI lê; enquanto o auditor não
    existir, a resposta honesta é indisponibilidade.

    Devolve o código de saída quando recusa, ou `None` para seguir.
    """
    if args.auditor:
        console.print(
            "[red]--auditor está indisponível.[/red] O auditor semântico é um stub: ele "
            "devolveria um veredito vazio, e encerrar com código 0 faria a CI registrar "
            "uma auditoria que não aconteceu.\n"
            "Ele continua fora do loop quente; nada no pipeline depende dele."
        )
        return ERRO_DE_USO
    return None


def avisar_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Diz, no fim, o que ficou inválido em disco e por quê."""
    com_lixo = [resultado for resultado in resultados if resultado.arquivos_reprovados]
    if not com_lixo:
        return
    registro.aviso(
        "Artefatos deixados em estado REPROVADO (mantidos de propósito — é o que se "
        "inspeciona para entender a falha). Use --remover-reprovados para apagar o "
        "que foi criado por esta ferramenta:"
    )
    for resultado in com_lixo:
        codigos = ", ".join(resultado.codigos_remanescentes) or "(sem código)"
        onde = "publicado no projeto" if resultado.publicado else "em staging, não publicado"
        registro.info(f"  {resultado.recurso} [{codigos}] — {onde}")
        for caminho in resultado.arquivos_reprovados:
            registro.info(f"    {caminho}")


def remover_reprovados(resultados: list[ResultadoDoRecurso], registro: Registro) -> None:
    """Apaga o que **esta ferramenta criou** nos recursos que não foram aprovados.

    A versão anterior desta função foi desligada porque chamava `unlink()` na lista
    inteira, sem distinguir arquivo nosso de arquivo do consumidor. O que a traz de
    volta é o diário: `remover_criados` recusa tudo que não é `criado` e tudo cujo
    hash mudou desde que o gravamos. Um spec que nasceu conosco e o desenvolvedor
    editou à mão deixa de ser descartável no instante em que ele o salva.

    O staging é caso à parte e é apagado inteiro: ele nasceu nesta execução, é
    nosso do primeiro ao último byte, e é o único diretório nosso que fica dentro
    do projeto de quem nos contratou.
    """
    for resultado in resultados:
        if resultado.sucesso:
            continue
        removidos, recusados = remover_criados(resultado.diario)
        if resultado.staging is not None and resultado.staging.is_dir():
            shutil.rmtree(resultado.staging, ignore_errors=True)
            registro.info(f"  {resultado.recurso}: staging removido ({resultado.staging})")
        for caminho in removidos:
            registro.info(f"  {resultado.recurso}: removido {caminho}")
        for caminho in recusados:
            registro.aviso(
                f"  {resultado.recurso}: MANTIDO {caminho} — não fomos nós que o "
                "criamos, ou ele mudou depois que o criamos"
            )


def escrever_manifesto(
    config: Config,
    registro: Registro,
    *,
    dir_execucao: Path,
    dry_run: bool,
    recursos: list[str],
    com_artefatos: bool,
) -> None:
    """Grava o `manifesto-execucao.json` e diz no log o que não deu para coletar.

    Chamado **duas** vezes: no início, para que uma execução que morra no meio
    ainda deixe o cabeçalho do chamado de suporte; e no fim, quando existem
    artefatos para hashear. Escrever só no fim faria o diagnóstico faltar
    exatamente nas execuções que mais precisam dele.

    O que falhou é dito no console porque `campos_ausentes` dentro de um JSON é
    exatamente o tipo de coisa que ninguém abre: quem vai precisar do commit do
    backend descobre no chamado, meses depois, que ele nunca foi coletado.
    """
    manifesto = manifesto_de_execucao.escrever(
        config,
        dir_execucao / manifesto_de_execucao.NOME_DO_MANIFESTO,
        run_id=dir_execucao.name,
        dry_run=dry_run,
        recursos=recursos,
        dir_artefatos=(dir_execucao / "artefatos") if com_artefatos else None,
    )
    ausentes: dict[str, str] = manifesto["campos_ausentes"]
    registro.evento(
        TipoDeEvento.MANIFESTO_DE_EXECUCAO,
        arquivo=dir_execucao / manifesto_de_execucao.NOME_DO_MANIFESTO,
        com_artefatos=com_artefatos,
        campos_ausentes=ausentes,
    )
    if ausentes:
        registro.aviso(
            "manifesto de execução incompleto (a execução segue): " + ", ".join(sorted(ausentes))
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configurar_console()
    console = Console()

    if (recusa := recusar_indisponiveis(args, console)) is not None:
        return recusa

    try:
        config = Config.carregar(args.config)
    except ErroDeConfiguracao as erro:
        console.print(f"[red]configuração inválida:[/red] {erro}")
        return ERRO_DE_USO

    if not args.dry_run:
        load_dotenv(ARQUIVO_ENV)

    if args.max_tentativas is not None:
        # Cópia revalidada, não mutação: o valor da linha de comando atravessa o
        # mesmo tipo que o arquivo atravessou. `is not None` porque `0` é um pedido
        # explícito e inválido, não ausência de pedido — o parser já o recusou, e
        # este `if` não pode reintroduzir a diferença.
        try:
            config = config.com_max_tentativas(args.max_tentativas)
        except ErroDeConfiguracao as erro:
            console.print(f"[red]configuração inválida:[/red] {erro}")
            return ERRO_DE_USO

    dir_execucao = diretorio_de_execucao(
        config.caminhos.saida
        if config.caminhos.saida.is_absolute()
        else RAIZ_PROJETO / config.caminhos.saida
    )

    roteiros: Roteiros | None = None
    if args.dry_run:
        roteiros = Roteiros(DIR_FIXTURES / "roteiros")
        config = preparar_sandbox(config, dir_execucao / "sandbox")

    recursos_pedidos = args.recursos or (list(roteiros.recursos()) if roteiros else [])
    if not recursos_pedidos:
        console.print("[red]informe ao menos um --recurso.[/red]")
        return ERRO_DE_USO

    with Registro(dir_execucao / "execucao.jsonl", console) as registro:
        registro.info(f"log estruturado: {registro.caminho}")
        registro.evento(
            TipoDeEvento.EXECUCAO_INICIADA,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            config=str(config.origem),
            projeto_testes=config.caminhos.projeto_testes,
            backend=config.caminhos.backend,
        )
        escrever_manifesto(
            config,
            registro,
            dir_execucao=dir_execucao,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            com_artefatos=False,
        )

        try:
            config.validar_caminhos(exigir_backend=True)
        except ErroDeConfiguracao as erro:
            registro.falha(str(erro))
            return ERRO_DE_USO

        pipeline = Pipeline(
            config,
            registro,
            dry_run=args.dry_run,
            roteiros=roteiros,
            dir_execucao=dir_execucao,
            pular_cypress=not args.rodar_cypress,
        )

        try:
            resultados = pipeline.rodar(montar_recursos(config, recursos_pedidos))
        except ErroDeProvedor as erro:
            # Antes de `ErroDeFerramenta`, de quem herda: a ordem dos `except` é o
            # que separa "arrume o ambiente" de "espere e repita".
            registro.falha(str(erro))
            registro.evento(
                TipoDeEvento.EXECUCAO_ABORTADA,
                motivo=str(erro),
                categoria=erro.categoria.value,
                retentavel=erro.categoria.retentavel,
            )
            return ERRO_DE_PROVEDOR
        except (ErroDeFerramenta, ErroDeConfiguracao) as erro:
            registro.falha(f"erro de invocação do pipeline: {erro}")
            registro.evento(TipoDeEvento.EXECUCAO_ABORTADA, motivo=str(erro))
            return ERRO_DE_USO

        registro.titulo("Telemetria")
        console.print(tabelas.tabela_por_estagio(pipeline.telemetria))
        console.print(tabelas.tabela_por_recurso(pipeline.telemetria))
        console.print(tabelas.tabela_entrada_por_tentativa(pipeline.telemetria))
        # Só o mapeador tem tools; sem elas a tabela seria uma moldura vazia.
        if pipeline.telemetria.tools:
            console.print(tabelas.tabela_de_tools(pipeline.telemetria))
        registro.evento(TipoDeEvento.TELEMETRIA, **pipeline.telemetria.resumo_para_log())

        registro.titulo("Resumo")
        for resultado in resultados:
            marca = MARCA_DO_ESTADO[resultado.estado]
            console.print(
                f"{marca} {resultado.recurso}: "
                f"mapeador {resultado.tentativas_mapeador} tentativa(s), "
                f"executor {resultado.tentativas_executor} tentativa(s)"
                + (f" — {resultado.motivo}" if resultado.motivo else "")
            )
            # O terceiro estado precisa dizer o que revisar, e onde. Sem isto ele
            # seria só uma palavra diferente de OK, e quem lê trataria como falha.
            for divergencia in resultado.divergencias:
                console.print(f"     [yellow]{escape(divergencia.render())}[/yellow]")
                console.print(
                    "     o schema do seu projeto tem precedência e NÃO foi alterado: "
                    "declare os campos nele ou confirme que a ausência é intencional"
                )
            # Dito recurso a recurso, e não uma vez no rodapé: é a diferença entre
            # "os testes passaram" e "os testes não rodaram", e ela precisa estar
            # onde alguém lê o veredito daquele recurso.
            if resultado.execucao_de_testes == NAO_EXECUTADO:
                # `escape`: o motivo cita blocos do TOML, e o Rich leria
                # "[execucao]" como tag de estilo e o engoliria.
                console.print(
                    f"     [yellow]testes NÃO EXECUTADOS[/yellow] "
                    f"({escape(resultado.motivo_da_execucao_de_testes)}) — "
                    "a cobertura acima é estática, não é prova de runtime"
                )
        avisar_reprovados(resultados, registro)
        if args.remover_reprovados:
            registro.titulo("Remoção do que esta ferramenta criou")
            remover_reprovados(resultados, registro)
        interrupcao = pipeline.interrupcao
        if interrupcao is not None:
            # O resumo acima é dos recursos que terminaram. Sem esta linha ele
            # pareceria a execução inteira, e os recursos que nunca rodaram sumiriam
            # sem deixar rastro na tela.
            console.print(
                f"[red]EXECUÇÃO INTERROMPIDA[/red] em {interrupcao.recurso}: "
                f"{escape(interrupcao.motivo)}"
            )
            if interrupcao.recursos_nao_executados:
                console.print(
                    "     não chegaram a rodar: " + ", ".join(interrupcao.recursos_nao_executados)
                )
        escrever_manifesto(
            config,
            registro,
            dir_execucao=dir_execucao,
            dry_run=args.dry_run,
            recursos=recursos_pedidos,
            com_artefatos=True,
        )
        registro.evento(
            TipoDeEvento.EXECUCAO_CONCLUIDA,
            sucesso=interrupcao is None and all(resultado.sucesso for resultado in resultados),
            interrompida=interrupcao is not None,
            recursos_nao_executados=(interrupcao.recursos_nao_executados if interrupcao else []),
            resultados=[
                {
                    "recurso": resultado.recurso,
                    "estado": resultado.estado.value,
                    "sucesso": resultado.sucesso,
                    "tentativas_mapeador": resultado.tentativas_mapeador,
                    "tentativas_executor": resultado.tentativas_executor,
                    "execucao_de_testes": resultado.execucao_de_testes,
                    "motivo": resultado.motivo,
                }
                for resultado in resultados
            ],
        )
        registro.info(f"artefatos e log desta execução: {dir_execucao}")

    if pipeline.interrupcao is not None:
        # Distinto do 1 de gate esgotado: ali o pipeline funcionou e o artefato não
        # passou; aqui o pipeline não conseguiu emitir veredito nenhum. E provedor é
        # distinto de ferramenta porque a resposta do operador é outra — esperar e
        # repetir, em vez de arrumar o ambiente.
        if pipeline.interrupcao.categoria_do_provedor is not None:
            return ERRO_DE_PROVEDOR
        return ERRO_DE_USO
    return codigo_de_saida(resultados)


def codigo_de_saida(resultados: list[ResultadoDoRecurso]) -> int:
    """O pior desfecho manda, e revisão pendente nunca vira 0.

    A ordem é reprovado > requer revisão > aprovado. Um recurso reprovado e outro
    pedindo revisão saem como reprovação: o código de saída é um número só, e o
    número precisa apontar para a coisa mais grave que aconteceu.
    """
    estados = {resultado.estado for resultado in resultados}
    if EstadoDoRecurso.REPROVADO in estados:
        return FALHA_DE_GATE
    if EstadoDoRecurso.REQUER_REVISAO in estados:
        return REQUER_REVISAO
    return SUCESSO


if __name__ == "__main__":
    sys.exit(main())
