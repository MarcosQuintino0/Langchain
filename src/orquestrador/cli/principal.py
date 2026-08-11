"""A execução: argumentos, montagem, laço de recursos e apresentação do resumo.

Só isso. Os loops de controle vivem em `aplicacao/pipeline.py`; o que este módulo
faz é traduzir a linha de comando para uma execução e a execução de volta para
texto e um código de saída."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from orquestrador.aplicacao.pipeline import (
    NAO_EXECUTADO,
    Pipeline,
    ResultadoDoRecurso,
)
from orquestrador.aplicacao.reaproveitamento import resolver_execucao
from orquestrador.aplicacao.simulacao import Roteiros, preparar_sandbox
from orquestrador.cli.codigos_de_saida import (
    ERRO_DE_PROVEDOR,
    ERRO_DE_USO,
    FALHA_DE_GATE,
    ORCAMENTO_ESGOTADO,
    REQUER_REVISAO,
    SUCESSO,
)
from orquestrador.cli.doctor import comando_doctor
from orquestrador.cli.estimativa import estimar
from orquestrador.cli.execucoes import comando_execucoes
from orquestrador.cli.init import comando_init
from orquestrador.config import Config
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.veredito import EstadoDoRecurso
from orquestrador.excecoes import (
    ErroDeConfiguracao,
    ErroDeFerramenta,
    ErroDeProvedor,
)
from orquestrador.ferramentas.processo import observar_processos
from orquestrador.ferramentas.publicacao import remover_criados
from orquestrador.observabilidade import manifesto_de_execucao, tabelas
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.exportacao_otlp import ExportadorOtlp
from orquestrador.observabilidade.registro import (
    Registro,
    configurar_console,
    diretorio_de_execucao,
)
from orquestrador.raiz import ARQUIVO_ENV, DIR_FIXTURES, RAIZ_PROJETO

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


def construir_analisador() -> argparse.ArgumentParser:
    """O analisador, separado de `parse_args` para que a ajuda seja consultável.

    `docs/referencia/cli.md` publica `format_help()` num bloco gerado, e um teste
    compara os dois: flag nova sem documentação reprova. Com o analisador
    construído dentro de `parse_args`, a única forma de chegar ao texto seria
    capturar o stdout de um `SystemExit` — que é frágil e não vale a economia de
    uma função.
    """
    analisador = argparse.ArgumentParser(
        prog="orquestrador",
        description="Orquestrador multi-agente de testes de API (skill qa-api).",
        epilog=(
            "comandos: `orquestrador init` escreve o config.toml do projeto; "
            "`orquestrador doctor` diagnostica o ambiente. Sem comando, esta é a "
            "execução do pipeline."
        ),
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
        "--estimar",
        action="store_true",
        help=(
            "conta os endpoints do backend e devolve a faixa de token, sem chamar "
            "modelo nenhum. Roda só o Bloco 0."
        ),
    )
    analisador.add_argument(
        "--reaproveitar",
        metavar="RUN_ID",
        default=None,
        help=(
            "começa no executor, com o gabarito e o plano de uma execução anterior. "
            "Mapeador e planejador não são chamados; os dois gates continuam rodando. "
            "Serve para iterar no Bloco 2 sem pagar o pipeline inteiro."
        ),
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
    return analisador


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return construir_analisador().parse_args(argv)


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


def recusar_dry_run_sem_fixtures(console: Console) -> int | None:
    """Diz que o `--dry-run` precisa do checkout, em vez de morrer sem contexto.

    `fixtures/` fica **fora** do wheel de propósito: são dezenas de artefatos de
    desenvolvimento — roteiros de resposta, backend e projeto Cypress de mentira —
    que só existem para exercitar o pipeline daqui, e empacotá-los faria todo
    usuário baixar o material de teste do projeto. A consequência é que a instalação
    a partir do wheel não tem `--dry-run`, e o certo é dizer isso: sem esta recusa,
    a falha vinha de dentro de `preparar_sandbox`, como um erro de `copytree` sobre
    um caminho que ninguém pediu.

    Quem quer conferir a instalação usa `orquestrador doctor`, que é o comando feito
    para isso e não depende de fixture nenhuma.
    """
    roteiros = DIR_FIXTURES / "roteiros"
    if roteiros.is_dir():
        return None
    console.print(
        f"[red]--dry-run indisponível:[/red] fixtures não encontradas em {DIR_FIXTURES}.\n"
        "Elas são dado de desenvolvimento e não vão no pacote instalável. Para "
        "exercitar o pipeline sem gastar token, rode a partir de um checkout do "
        "repositório (ou aponte ORQUESTRADOR_RAIZ para um).\n"
        "Para conferir se esta instalação está sã, use `orquestrador doctor`."
    )
    return ERRO_DE_USO


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


# Despacho
# ---------------------------------------------------------------------------

# Subcomandos em vez de `add_subparsers` porque a forma histórica da CLI é
# `orquestrador --dry-run --recurso x`, sem verbo. Um subparser obrigaria a
# inventar um `run` e quebrar toda invocação existente; um opcional torna
# ambíguo o argumento posicional. O despacho por primeira palavra preserva as
# duas formas sem ambiguidade: `init` e `doctor` não são valores de flag nenhuma.
COMANDOS = ("init", "doctor", "execucoes")


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if argumentos and argumentos[0] in COMANDOS:
        configurar_console()
        console = Console()
        if argumentos[0] == "init":
            return comando_init(argumentos[1:], console)
        if argumentos[0] == "execucoes":
            return comando_execucoes(argumentos[1:], console)
        return comando_doctor(argumentos[1:], console)
    return executar_pipeline(argumentos)


def executar_pipeline(argv: list[str] | None = None) -> int:
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

    if args.estimar:
        # Antes de criar o diretório de execução: `--estimar` não é uma execução.
        # Ele lê o backend, conta e sai — sem log, sem manifesto, sem staging e sem
        # deixar uma pasta vazia em `.execucoes/` para quem for procurar o resultado.
        return estimar(config, console)

    dir_execucao = diretorio_de_execucao(
        config.caminhos.saida
        if config.caminhos.saida.is_absolute()
        else RAIZ_PROJETO / config.caminhos.saida
    )

    roteiros: Roteiros | None = None
    if args.dry_run:
        if (recusa := recusar_dry_run_sem_fixtures(console)) is not None:
            return recusa
        roteiros = Roteiros(DIR_FIXTURES / "roteiros")
        config = preparar_sandbox(config, dir_execucao / "sandbox")

    recursos_pedidos = args.recursos or (list(roteiros.recursos()) if roteiros else [])
    if not recursos_pedidos:
        console.print("[red]informe ao menos um --recurso.[/red]")
        return ERRO_DE_USO

    with (
        Registro(
            dir_execucao / "execucao.jsonl",
            console,
            intervalo_pulso_s=float(config.observabilidade.intervalo_pulso_s),
            exportador=ExportadorOtlp(config.observabilidade.otlp),
        ) as registro,
        observar_processos(lambda medida: registro.evento(TipoDeEvento.PROCESSO, **asdict(medida))),
    ):
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
            registro.evento(
                TipoDeEvento.EXECUCAO_ABORTADA,
                motivo="configuração de caminhos inválida",
                erro_tipo=type(erro).__name__,
            )
            return ERRO_DE_USO

        try:
            origem = (
                resolver_execucao(config.caminhos.saida, args.reaproveitar)
                if args.reaproveitar
                else None
            )
        except ErroDeConfiguracao as erro:
            registro.falha(str(erro))
            registro.evento(
                TipoDeEvento.EXECUCAO_ABORTADA,
                motivo="execução a reaproveitar não resolvida",
                erro_tipo=type(erro).__name__,
            )
            return ERRO_DE_USO

        pipeline = Pipeline(
            config,
            registro,
            dry_run=args.dry_run,
            roteiros=roteiros,
            dir_execucao=dir_execucao,
            pular_cypress=not args.rodar_cypress,
            reaproveitar=origem,
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
        if pipeline.interrupcao.por_orcamento:
            # Antes do provedor: teto alcançado não é indisponibilidade, e a
            # resposta de quem opera é decidir, não esperar.
            return ORCAMENTO_ESGOTADO
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
