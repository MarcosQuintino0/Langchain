"""Os loops de controle do pipeline.

    Bloco 0  qa-reindex (Graphify, AST)            determinístico, zero token
    Bloco 1  mapeador (LLM + tools)   → Gate A     um recurso por vez
    Bloco 2  executor (LLM, sem tools) → Gate B    um recurso por vez
    Bloco 3  Cypress + qa-cobertura                determinístico

Os seis princípios que o desenho serve estão no README. Os dois que aparecem
literalmente neste arquivo:

* princípio 1 — o handoff entre estágios é **artefato em disco**. O manifesto, os
  specs e os schemas vão para a área de staging da execução; o inventário, para o
  diretório da execução. Nenhum estágio recebe conversa.
* princípio 2 — o loop de reparo envia **só o delta**: instrução fixa do estágio +
  artefato atual + violações. Sem histórico de tentativas.

O disco do consumidor só é tocado **uma vez por recurso**, depois que os dois
gates aprovaram, e por `ferramentas/publicacao.py`. Enquanto o loop roda, cada
tentativa reescreve o staging; o que uma tentativa ruim destrói é a tentativa
anterior, nunca o projeto de quem nos contratou.

A CLI que dirige tudo isto vive em `cli.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.agentes import planejador as agente_planejador
from orquestrador.analise_estatica.ci_do_projeto import detectar
from orquestrador.aplicacao.ciclo_de_reparo import CicloDeReparo
from orquestrador.aplicacao.persistencia import PersistenciaDeArtefatos
from orquestrador.aplicacao.reaproveitamento import DecisoesAnteriores, carregar
from orquestrador.aplicacao.simulacao import Roteiros
from orquestrador.config import Config
from orquestrador.dominio.artefatos import SaidaExecutor, SaidaMapeador
from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.limpeza import render_ausencias, render_limpeza
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.plano import PlanoDeTestes
from orquestrador.dominio.propriedade import (
    Classificacao,
    DivergenciaDeSchema,
    EntradaDoDiario,
)
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.superficie import SuperficieDoProjeto
from orquestrador.dominio.veredito import Delta, EstadoDoRecurso, ResultadoGate
from orquestrador.excecoes import (
    CategoriaDeProvedor,
    ErroDeConfiguracao,
    ErroDeFerramenta,
    ErroDeProvedor,
    FalhaDaExecucaoDeTestes,
    FalhaDeEstagio,
    FalhaDeGate,
    FalhaDePublicacao,
    GrafoNaoPreparado,
    OrcamentoEsgotado,
)
from orquestrador.ferramentas import processo
from orquestrador.ferramentas.graphify import Graphify, ResultadoPreparacao
from orquestrador.ferramentas.pipeline_ci import (
    PipelineGerada,
    escritas_no_projeto,
    gerar,
)
from orquestrador.ferramentas.publicacao import (
    NOME_DO_DIARIO,
    AreaDeStaging,
    Diario,
    criar_area,
    hash_do_arquivo,
)
from orquestrador.gates import gate_a, gate_b
from orquestrador.llm.cliente import criar_modelo
from orquestrador.observabilidade.artefatos import medir_arquivos
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.rastreamento import Rastreador
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria

# Estado da suíte em runtime, no Bloco 3. São dois estados e não um booleano
# porque a diferença que importa é entre "os testes passaram" e "ninguém sabe":
# sem relatório desta execução não há evidência de runtime nenhuma, e o resultado
# precisa dizer isso em vez de deixar a cobertura estática passar por prova.
EXECUTADO = "EXECUTADO"
NAO_EXECUTADO = "NAO_EXECUTADO"

# Marca substituída pelo caminho do relatório desta execução no comando do Cypress.
MARCA_RELATORIO = "{relatorio}"


@dataclass(frozen=True)
class ResultadoDaExecucaoDeTestes:
    """O que o Bloco 3 apurou: os contadores e de onde eles vieram."""

    estado: str
    contadores: dict[str, Any] = field(default_factory=dict[str, Any])
    relatorio: Path | None = None
    motivo: str = ""


@dataclass(frozen=True)
class InterrupcaoDaExecucao:
    """A execução parou no meio porque uma ferramenta ou o provedor não respondeu.

    `categoria_do_provedor` distingue as duas: só ela decide se a resposta certa é
    arrumar o ambiente ou esperar e repetir, e é dela que sai o código de saída. A
    exceção em si não chega à CLI — o laço de recursos a captura para não perder o
    resultado de quem já terminou —, então o que sobrevive precisa carregar isso.
    """

    motivo: str
    recurso: str
    recursos_nao_executados: list[str] = field(default_factory=list[str])
    categoria_do_provedor: CategoriaDeProvedor | None = None
    # Teto alcançado não é falha, é obediência — e o código de saída é outro.
    por_orcamento: bool = False


def _copiar_decisoes(decisoes: DecisoesAnteriores, destino: Path) -> list[Path]:
    """Reescreve os artefatos reaproveitados nesta execução, já validados.

    Reserializar em vez de copiar byte a byte é deliberado: o que vai para o disco
    é o que o contrato aceitou, então um artefato de versão antiga chega aqui na
    forma de hoje, e não como cópia que só falharia na próxima leitura.
    """
    escritos = [
        _gravar(destino / "manifesto.json", decisoes.manifesto.para_json()),
        _gravar(destino / "plano.json", decisoes.plano.model_dump_json(by_alias=True, indent=1)),
        _gravar(destino / "plano.md", decisoes.plano.render() + "\n"),
    ]
    if decisoes.inventario is not None:
        escritos.append(_gravar(destino / "inventario.json", decisoes.inventario.para_json()))
    if decisoes.dossie is not None:
        escritos.append(
            _gravar(
                destino / "dossie.json",
                decisoes.dossie.model_dump_json(by_alias=True, exclude_none=True, indent=1) + "\n",
            )
        )
    return escritos


def _gravar(arquivo: Path, conteudo: str) -> Path:
    arquivo.write_text(conteudo, encoding="utf-8", newline="\n")
    return arquivo


@dataclass
class ResultadoDoRecurso:
    recurso: str
    # Nunca começa aprovado: o padrão de um campo é o que vale quando o recurso
    # falha antes de chegar ao fim, e o padrão errado aqui é um falso sucesso.
    estado: EstadoDoRecurso = EstadoDoRecurso.REPROVADO
    tentativas_mapeador: int = 0
    tentativas_executor: int = 0
    gate_a: ResultadoGate | None = None
    gate_b: ResultadoGate | None = None
    cobertura: dict[str, Any] = field(default_factory=dict[str, Any])
    # Nunca começa como "executado": mesmo raciocínio do estado acima.
    execucao_de_testes: str = NAO_EXECUTADO
    motivo_da_execucao_de_testes: str = "o Bloco 3 não chegou a rodar para este recurso"
    motivo: str = ""
    # A3: o que ficou em disco em estado reprovado. Não é apagado por padrão.
    arquivos_reprovados: list[Path] = field(default_factory=list[Path])
    codigos_remanescentes: list[str] = field(default_factory=list[str])
    # O que a publicação fez no projeto do consumidor, arquivo a arquivo.
    diario: list[EntradaDoDiario] = field(default_factory=list[EntradaDoDiario])
    publicado: bool = False
    # Só sobrevive ao fim do recurso quando ele falhou: é o que se inspeciona.
    staging: Path | None = None
    divergencias: list[DivergenciaDeSchema] = field(default_factory=list[DivergenciaDeSchema])

    @property
    def sucesso(self) -> bool:
        """Aprovado, e só isso.

        Propriedade e não campo: com os dois, `sucesso=True, estado=REPROVADO` seria
        escrevível, e a pergunta "o recurso passou?" passaria a ter duas respostas.
        `REQUER_REVISAO` responde `False` aqui de propósito — o recurso não terminou
        aprovado —, e quem precisa distinguir revisão de reprovação lê `estado`.
        """
        return self.estado is EstadoDoRecurso.APROVADO


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        config: Config,
        registro: Registro,
        *,
        dry_run: bool = False,
        roteiros: Roteiros | None = None,
        dir_execucao: Path,
        pular_cypress: bool = True,
        reaproveitar: Path | None = None,
    ) -> None:
        self.config = config
        self.registro = registro
        self.telemetria = Telemetria(registro)
        self.dry_run = dry_run
        self.roteiros = roteiros
        self.dir_execucao = dir_execucao
        self.pular_cypress = pular_cypress
        # A execução cujos artefatos substituem os Blocos 1 e o plano. Ela não é
        # tocada: o que sai de lá é lido, e tudo que se escreve continua nesta.
        self.reaproveitar = reaproveitar
        self._modelos_reais: dict[str, BaseChatModel] = {}
        # O diário mora na raiz do diretório de saída — acima desta execução, porque
        # a pergunta que ele responde ("este arquivo é nosso?") atravessa execuções.
        # Ver `ferramentas/publicacao.py`.
        self.diario = Diario(dir_execucao.parent / NOME_DO_DIARIO)
        # Preenchida no Bloco 0: é do projeto, não do recurso, então é extraída uma
        # vez e viaja pela instrução fixa do executor.
        self.superficie: SuperficieDoProjeto | None = None
        # Preenchida quando o laço de recursos para no meio. Fica no pipeline, e não
        # no retorno de `rodar`, para que quem já lê a lista de resultados continue
        # lendo a lista de resultados — inclusive a parcial.
        self.interrupcao: InterrupcaoDaExecucao | None = None
        # Preenchida no fim de `rodar`: é do projeto, não do recurso, e o relatório
        # final precisa dizer o que foi escrito e o que o dono ainda tem de fazer.
        self.pipelines_de_ci: list[PipelineGerada] = []
        # As duas colaborações que este objeto coordena, e não implementa: o loop
        # de reparo e as escritas em disco. Ver a docstring de `aplicacao/`.
        self.persistencia = PersistenciaDeArtefatos(
            config, registro, dir_execucao=dir_execucao, diario=self.diario
        )
        self.ciclo = CicloDeReparo(config, registro, self.telemetria)

    # -- modelos ------------------------------------------------------------

    def modelo(self, estagio: str, recurso: str, tentativa: int) -> BaseChatModel:
        """Modelo do estágio: fixture no dry-run, OpenRouter na execução real."""
        if self.dry_run:
            # `assert` de propósito, diferente do caso de config.py: isto é
            # invariante da nossa própria montagem (a CLI sempre passa `roteiros`
            # junto de `dry_run=True`), não entrada de quem opera. Sob `python -O`
            # a garantia some, mas o que sobra é um AttributeError em `None.modelo`
            # na linha seguinte — barulhento e imediato. Nenhum gate passa a
            # aprovar por causa disso, que é o risco que motivaria uma exceção real.
            assert self.roteiros is not None  # noqa: S101
            return self.roteiros.modelo(recurso, estagio, tentativa)
        if estagio not in self._modelos_reais:
            self._modelos_reais[estagio] = criar_modelo(self.config, estagio)
        return self._modelos_reais[estagio]

    # -- Bloco 0 ------------------------------------------------------------

    def bloco0(self) -> ResultadoPreparacao:
        self.registro.titulo("Bloco 0 — preparação (determinística, zero token)")
        grafo = Graphify(self.config)
        if self.dry_run:
            existe = grafo.graph.is_file()
            resultado = ResultadoPreparacao(
                ok=existe,
                regenerou=False,
                graph=grafo.graph,
                detalhe=(
                    "dry-run: qa-reindex não é executado; usando o graph.json de fixture"
                    if existe
                    else f"dry-run: graph.json de fixture ausente em {grafo.graph}"
                ),
            )
        else:
            resultado = grafo.preparar()

        self.registro.evento(
            TipoDeEvento.BLOCO0,
            ok=resultado.ok,
            regenerou=resultado.regenerou,
            graph=resultado.graph,
            detalhe=resultado.detalhe,
        )
        (self.registro.ok if resultado.ok else self.registro.aviso)(resultado.detalhe)
        self.superficie = self.persistencia.extrair_superficie()
        return resultado

    # -- Bloco 1 + Gate A ---------------------------------------------------

    def bloco1(
        self, recurso: Recurso, area: AreaDeStaging
    ) -> tuple[SaidaMapeador, ResultadoGate, int]:
        self.registro.titulo(f"Bloco 1 — mapeador · {recurso.nome}")
        dir_artefatos = self.dir_execucao / "artefatos" / recurso.nome
        inventario_path = dir_artefatos / "inventario.json"
        dossie_path = dir_artefatos / "dossie.json"
        notas_path = dir_artefatos / "notas-de-descoberta.md"
        manifesto_path = dir_artefatos / "manifesto.json"

        # A memória entre tentativas do MESMO recurso: o reparo parte das notas e
        # da saída da tentativa anterior em vez de re-explorar o backend. Morre com
        # o bloco (princípio 3) — o próximo recurso começa do zero.
        ultimo: agente_mapeador.MapeamentoProduzido | None = None

        def modelo_da_fatia(tentativa: int) -> Callable[[str], BaseChatModel]:
            def fabrica(fatia: str) -> BaseChatModel:
                if self.dry_run:
                    # Cada fatia tem o próprio roteiro: o ModeloSimulado escolhe o
                    # passo contando a conversa, e as fatias começam conversa nova.
                    return self.modelo(f"mapeador-{fatia}", recurso.nome, tentativa)
                return self.modelo(agente_mapeador.ESTAGIO, recurso.nome, tentativa)

            return fabrica

        def produzir(
            tentativa: int, delta: Delta | None, atual: str | None
        ) -> agente_mapeador.MapeamentoProduzido:
            nonlocal ultimo
            ultimo = agente_mapeador.executar(
                self.config,
                recurso,
                modelo=self.modelo(agente_mapeador.ESTAGIO, recurso.nome, tentativa),
                telemetria=self.telemetria,
                registro=self.registro,
                tentativa=tentativa,
                delta=delta,
                artefato_atual=atual,
                notas_anteriores=ultimo.notas if ultimo else None,
                saida_anterior=ultimo.saida if ultimo else None,
                modelo_da_fatia=modelo_da_fatia(tentativa),
            )
            return ultimo

        def persistir(produzido: agente_mapeador.MapeamentoProduzido) -> list[Path]:
            saida = produzido.saida
            escritos = [
                area.escrever("_support/cobertura.json", saida.manifesto.para_json()),
                # Os schemas de entrada ficam fora do diretório do recurso: são o
                # denominador da cobertura por campo e o Gate A os lê do disco, não
                # da saída do modelo.
                *self.persistencia.persistir_schemas(recurso, saida, area),
            ]
            self.registro.evento(
                TipoDeEvento.ARTEFATOS,
                estagio=agente_mapeador.ESTAGIO,
                recurso=recurso.nome,
                arquivos=medir_arquivos(escritos),
            )

            inventario_path.parent.mkdir(parents=True, exist_ok=True)
            # As notas são artefato de auditoria e a memória do reparo: é nelas que
            # se lê o que o modelo leu no backend para decidir o resto.
            notas_path.write_text(produzido.notas.rstrip() + "\n", encoding="utf-8", newline="\n")
            inventario_path.write_text(saida.inventario.para_json(), encoding="utf-8", newline="\n")
            # O gabarito também fica no diretório da execução, e não só no `_support/`
            # publicado: sem esta cópia a execução não é auto-contida, e reaproveitá-la
            # depois obrigaria a ler o projeto do cliente — que pode ter mudado desde
            # então, e aí o plano estaria sendo executado contra outro gabarito.
            manifesto_path.write_text(saida.manifesto.para_json(), encoding="utf-8", newline="\n")
            # O inventário e o dossiê ficam no diretório da execução, que é nosso,
            # e por isso não entram na conta do que precisa ser publicado.
            if saida.dossie is not None:
                dossie_path.write_text(
                    saida.dossie.model_dump_json(by_alias=True, exclude_none=True, indent=1) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                dossie_path.with_suffix(".md").write_text(
                    "\n\n".join(
                        [
                            saida.dossie.render() or "(dossiê vazio)",
                            render_limpeza(saida.inventario, saida.dossie),
                            render_ausencias(saida.inventario),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            else:
                # Tentativa sem dossiê apaga o da anterior: o bundle de reparo lê o
                # disco, e um dossiê velho ao lado de um QAORQ-063 seria contradição.
                dossie_path.unlink(missing_ok=True)
                dossie_path.with_suffix(".md").unlink(missing_ok=True)
            return escritos

        def avaliar(produzido: agente_mapeador.MapeamentoProduzido) -> ResultadoGate:
            return gate_a.executar(
                self.config,
                recurso,
                dir_recurso=area.dir_recurso,
                dir_schemas=area.dir_schemas,
                inventario=produzido.saida.inventario,
                manifesto=produzido.saida.manifesto,
                dossie=produzido.saida.dossie,
            )

        produzido, resultado, tentativas = self.ciclo.executar(
            estagio=agente_mapeador.ESTAGIO,
            gate="a",
            recurso=recurso,
            produzir=produzir,
            persistir=persistir,
            avaliar=avaliar,
            texto_do_artefato=lambda _produzido, _delta: agente_mapeador.artefato_em_disco(
                manifesto=area.dir_recurso / "_support" / "cobertura.json",
                inventario=inventario_path,
                dir_schemas=area.dir_schemas,
                recurso=recurso.nome,
                dossie=dossie_path,
                notas=notas_path,
            ),
        )
        return produzido.saida, resultado, tentativas

    # -- Plano de cenários (entre o Gate A e o executor) ----------------------

    def bloco_plano(self, recurso: Recurso, saida_mapeador: SaidaMapeador) -> PlanoDeTestes:
        """Expande o gabarito aprovado em cenários concretos, endpoint a endpoint.

        Vem DEPOIS do Gate A de propósito: planejar sobre gabarito reprovado seria
        gastar cenário em endpoint que o diff vai mandar reescrever. O plano é
        persistido no diretório da execução — é artefato de auditoria (um humano
        revisa os casos antes de existir código), não parte da suíte publicada.
        """
        self.registro.titulo(f"Plano de cenários · {recurso.nome}")
        plano = agente_planejador.executar(
            self.config,
            recurso,
            saida_mapeador.manifesto,
            saida_mapeador.schemas,
            modelo=self.modelo(agente_planejador.ESTAGIO, recurso.nome, 1),
            telemetria=self.telemetria,
            registro=self.registro,
            dossie=saida_mapeador.dossie,
        )
        destino = self.dir_execucao / "artefatos" / recurso.nome
        destino.mkdir(parents=True, exist_ok=True)
        arquivos = [destino / "plano.json", destino / "plano.md"]
        arquivos[0].write_text(
            plano.model_dump_json(by_alias=True, indent=1), encoding="utf-8", newline="\n"
        )
        arquivos[1].write_text(plano.render() + "\n", encoding="utf-8", newline="\n")
        self.registro.evento(
            TipoDeEvento.ARTEFATOS,
            estagio=agente_planejador.ESTAGIO,
            recurso=recurso.nome,
            arquivos=medir_arquivos(arquivos),
        )
        self.registro.ok(
            f"plano com {plano.total_de_cenarios()} cenário(s) em "
            f"{len(plano.endpoints)} endpoint(s)"
        )
        return plano

    # -- Reaproveitamento (no lugar do Bloco 1 e do plano) --------------------

    def bloco_reaproveitado(self, recurso: Recurso, area: AreaDeStaging) -> DecisoesAnteriores:
        """O gabarito, o plano, o dossiê e o inventário de uma execução anterior.

        Substitui o Bloco 1 e o plano, não o Bloco 2: o executor roda inteiro e os
        dois gates também. Reaproveitar veredito seria carimbar sem verificar.
        """
        # Mesmo caso do `roteiros` acima: invariante da nossa própria montagem — a
        # CLI resolve o run_id antes de construir o Pipeline —, não entrada de quem
        # opera. Quem digita run_id errado recebe ErroDeConfiguracao lá, com o texto
        # que diz onde procurei.
        assert self.reaproveitar is not None  # noqa: S101
        self.registro.titulo(f"Decisões reaproveitadas · {recurso.nome}")
        decisoes = carregar(self.reaproveitar, recurso)

        # O gabarito precisa voltar ao staging: a publicação mede o diretório do
        # recurso e remove o que ficou de fora. Sem esta linha, o
        # `_support/cobertura.json` publicado seria dado por obsoleto e apagado do
        # projeto de quem nos contratou — por causa de uma execução que nem tinha
        # mapeador.
        escritos = [area.escrever("_support/cobertura.json", decisoes.manifesto.para_json())]

        # E uma cópia dos artefatos aqui dentro. Ponteiro para o diretório de origem
        # envelheceria: a retenção pode apagar aquela execução, e comparar duas
        # execuções exige que cada uma diga, por si, contra o que rodou.
        destino = self.dir_execucao / "artefatos" / recurso.nome
        destino.mkdir(parents=True, exist_ok=True)
        copiados = _copiar_decisoes(decisoes, destino)

        self.registro.evento(
            TipoDeEvento.ARTEFATOS,
            estagio="reaproveitamento",
            recurso=recurso.nome,
            arquivos=medir_arquivos(escritos + copiados),
            origem=str(decisoes.origem),
        )
        self.registro.ok(
            f"reaproveitado de {self.reaproveitar.name}: gabarito com "
            f"{len(decisoes.manifesto.endpoints)} endpoint(s) e plano com "
            f"{decisoes.plano.total_de_cenarios()} cenário(s). "
            "Mapeador e planejador NÃO foram chamados."
        )
        return decisoes

    # -- Bloco 2 + Gate B ---------------------------------------------------

    def bloco2(
        self,
        recurso: Recurso,
        manifesto: Manifesto,
        area: AreaDeStaging,
        plano: PlanoDeTestes | None = None,
        dossie: DossieDoRecurso | None = None,
        inventario: Inventario | None = None,
    ) -> tuple[SaidaExecutor, ResultadoGate, int]:
        self.registro.titulo(f"Bloco 2 — executor · {recurso.nome}")

        def produzir(tentativa: int, delta: Delta | None, atual: str | None) -> SaidaExecutor:
            return agente_executor.executar(
                self.config,
                recurso,
                manifesto,
                modelo=self.modelo(agente_executor.ESTAGIO, recurso.nome, tentativa),
                telemetria=self.telemetria,
                registro=self.registro,
                tentativa=tentativa,
                delta=delta,
                artefato_atual=atual,
                superficie=self.superficie,
                plano=plano,
                dossie=dossie,
                inventario=inventario,
                # O staging desta execução: é contra ele que o reparo confere se
                # devolveu o arquivo com menos cobertura do que tinha.
                dir_recurso=area.dir_recurso,
            )

        def persistir(saida: SaidaExecutor) -> list[Path]:
            escritos = agente_executor.escrever(area, saida)
            self.registro.evento(
                TipoDeEvento.ARTEFATOS,
                estagio=agente_executor.ESTAGIO,
                recurso=recurso.nome,
                arquivos=medir_arquivos(escritos),
            )
            return escritos

        return self.ciclo.executar(
            estagio=agente_executor.ESTAGIO,
            gate="b",
            recurso=recurso,
            produzir=produzir,
            persistir=persistir,
            # O manifesto vai junto para o gate poder nomear a categoria que ficou
            # sem teste; o `out` mantém o HTML de cada tentativa dentro da execução,
            # em vez de sujar o projeto do usuário a cada volta do loop.
            avaliar=lambda _saida: gate_b.executar(
                self.config,
                recurso,
                dir_recurso=area.dir_recurso,
                dir_schemas=area.dir_schemas,
                manifesto=manifesto,
                out_cobertura=self.dir_execucao / "cobertura" / recurso.nome / "gate.html",
                inventario=inventario,
                dossie=dossie,
            ),
            texto_do_artefato=lambda saida, delta: agente_executor.artefato_em_disco(
                area.dir_recurso, saida, delta
            ),
        )

    # -- Bloco 3 ------------------------------------------------------------

    def bloco3(self, recurso: Recurso) -> ResultadoDaExecucaoDeTestes:
        self.registro.titulo(f"Bloco 3 — execução e relatório · {recurso.nome}")

        if self.config.execucao.cypress and not self.pular_cypress:
            report = self._rodar_cypress(recurso)
            estado, motivo = EXECUTADO, ""
        else:
            report = None
            estado = NAO_EXECUTADO
            motivo = (
                "sem --rodar-cypress"
                if self.config.execucao.cypress
                else "[execucao].cypress não configurado"
            )
            self.registro.evento(
                TipoDeEvento.CYPRESS, recurso=recurso.nome, estado=NAO_EXECUTADO, motivo=motivo
            )
            # Aviso, não info: sem relatório o que sai adiante é cobertura de
            # forma — as tags que os specs declaram — e não prova de runtime.
            self.registro.aviso(
                f"Cypress NÃO EXECUTADO ({motivo}): a cobertura abaixo é estática, "
                "nenhum teste foi rodado."
            )

        # O relatório de cobertura por categoria e por campo vinha do
        # `qa-cobertura.mjs` e saiu com o desacoplamento da skill (2026-08-10).
        # Contadores vazios, e o aviso diz isso com todas as letras: número que
        # não existe não pode virar zero silencioso num relatório de cobertura.
        contadores: dict[str, Any] = {}
        self.registro.evento(
            TipoDeEvento.COBERTURA,
            recurso=recurso.nome,
            contadores=contadores,
            execucao_de_testes=estado,
            saida="relatório de cobertura pendente: ver docs/arquitetura/pendencias.md",
        )
        self.registro.aviso(
            "sem relatório de cobertura por categoria/campo — a checagem saiu com a "
            "skill e os gates novos ainda não foram desenhados "
            "(docs/arquitetura/pendencias.md)."
        )
        return ResultadoDaExecucaoDeTestes(
            estado=estado, contadores=contadores, relatorio=report, motivo=motivo
        )

    def _rodar_cypress(self, recurso: Recurso) -> Path:
        """Roda a suíte e devolve o relatório **desta** execução.

        Três coisas que faltavam e que, juntas, faziam o Bloco 3 aprovar sem prova:

        1. o relatório mora no diretório da execução, um por recurso — antes era um
           `report.json` fixo na raiz do projeto de testes, então o de ontem servia
           para hoje;
        2. o caminho é apagado antes de rodar (é **nosso** diretório: apagar aqui não
           toca em arquivo do consumidor) e exigido depois, então sobra só relatório
           que esta execução produziu;
        3. código de saída diferente de zero interrompe o recurso. Antes ele virava
           campo de evento e o recurso terminava como sucesso.
        """
        configurado = list(self.config.execucao.cypress)
        if not any(MARCA_RELATORIO in argumento for argumento in configurado):
            raise ErroDeConfiguracao(
                f"[execucao].cypress precisa conter {MARCA_RELATORIO} no argumento que "
                "diz ao repórter onde escrever o JSON — é assim que o orquestrador sabe "
                "que o relatório é desta execução, e não de uma anterior. Exemplo:\n"
                '  cypress = ["npx", "--no-install", "cypress", "run", "--reporter", '
                '"json", "--reporter-options", "output=' + MARCA_RELATORIO + '"]'
            )

        destino = self.dir_execucao / "cypress" / recurso.nome / "report.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.unlink(missing_ok=True)
        argumentos = [argumento.replace(MARCA_RELATORIO, str(destino)) for argumento in configurado]

        saida = processo.executar(
            argumentos,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
            # O ambiente do subprocesso é allowlist; sem estas, o `cypress.config.js`
            # do consumidor sobe sem a configuração que ele espera e a suíte falha
            # por um motivo que não tem nada a ver com o teste gerado.
            variaveis_extras=processo.VARIAVEIS_DO_CYPRESS,
        )
        self.registro.evento(
            TipoDeEvento.CYPRESS,
            recurso=recurso.nome,
            codigo=saida.codigo,
            relatorio=destino,
            relatorio_existe=destino.is_file(),
            saida=saida.texto[:4000],
        )

        if saida.codigo != 0:
            raise FalhaDaExecucaoDeTestes(
                f"o Cypress reprovou o recurso {recurso.nome!r} (código {saida.codigo}). "
                f"comando: {saida.comando}\n{saida.texto[:2000]}"
            )
        if not destino.is_file() or not destino.stat().st_size:
            raise FalhaDaExecucaoDeTestes(
                f"o Cypress saiu com código 0 mas não deixou relatório em {destino}. "
                f"Confira o repórter em [execucao].cypress: sem o JSON desta execução não "
                "há prova de runtime, e aceitar o relatório de outra execução é justamente "
                "o que este caminho existe para impedir."
            )
        self.registro.ok(f"Cypress executado; relatório desta execução em {destino}")
        return destino

    # -- orquestração -------------------------------------------------------

    def rodar(self, recursos: list[Recurso]) -> list[ResultadoDoRecurso]:
        preparacao = self.bloco0()
        if not preparacao.ok:
            # Falha fechada, e antes da primeira chamada de modelo: o mapeador
            # consultando um grafo inválido não produz erro — produz exploração cara
            # sobre um mapa errado, e nenhum gate reprova por esse motivo.
            raise GrafoNaoPreparado(
                f"Bloco 0 não deixou um graph.json utilizável em {preparacao.graph}: "
                f"{preparacao.detalhe}\n"
                "Rode o qa-reindex sobre o backend configurado antes de tentar de novo. "
                "Nenhum modelo foi chamado."
            )
        resultados: list[ResultadoDoRecurso] = []
        rastreador = Rastreador(self.registro)
        for indice, recurso in enumerate(recursos):
            try:
                with rastreador.operacao("recurso", recurso=recurso.nome):
                    resultados.append(self._rodar_recurso(recurso))
            except (ErroDeFerramenta, ErroDeConfiguracao) as erro:
                # Indisponibilidade — de ferramenta ou de provedor, que é subclasse —
                # não é falha do recurso, e por isso não é isolada como se fosse: o
                # script que não rodou aqui não vai rodar no próximo, e insistir só
                # queima token repetindo a mesma falha. Mas também não pode subir cru:
                # a exceção atravessando `rodar` levaria junto o resultado dos recursos
                # que já tinham terminado.
                restantes = [seguinte.nome for seguinte in recursos[indice + 1 :]]
                self.interrupcao = InterrupcaoDaExecucao(
                    motivo=str(erro),
                    recurso=recurso.nome,
                    recursos_nao_executados=restantes,
                    categoria_do_provedor=(
                        erro.categoria if isinstance(erro, ErroDeProvedor) else None
                    ),
                    por_orcamento=isinstance(erro, OrcamentoEsgotado),
                )
                self.registro.falha(
                    f"execução interrompida em {recurso.nome}: "
                    + (
                        "teto de orçamento"
                        if isinstance(erro, OrcamentoEsgotado)
                        else "ferramenta indisponível"
                    )
                    + f". {erro}"
                )
                self.registro.evento(
                    TipoDeEvento.EXECUCAO_INTERROMPIDA,
                    recurso=recurso.nome,
                    motivo=str(erro),
                    recursos_nao_executados=restantes,
                )
                break
        self.pipelines_de_ci = self._gerar_pipeline_de_ci(resultados)
        return resultados

    def _gerar_pipeline_de_ci(self, resultados: list[ResultadoDoRecurso]) -> list[PipelineGerada]:
        """A pipeline da suíte, depois de publicar — e só se o projeto já tiver CI.

        Vem no fim porque o `--spec` precisa apontar para o que existe de verdade:
        pipeline gerada antes da publicação apontaria para recurso que o gate
        ainda podia reprovar, e verde apontando para nada é o pior desfecho de
        todos.
        """
        publicados = [resultado.recurso for resultado in resultados if resultado.publicado]
        if not publicados:
            return []
        ci = detectar(self.config.caminhos.projeto_testes)
        if not ci.tem_ci:
            self.registro.aviso(
                "nenhuma integração contínua detectada no projeto: pipeline não gerada. "
                "Quem não tem CI não pediu uma, e um .yml depositado num repositório "
                "que nunca rodou nada é palpite, não entrega."
            )
            return []

        base = self.config.caminhos.dir_recursos.strip("/")
        gerados = gerar(
            ci,
            specs=[f"{base}/{nome}/**/*.cy.js" for nome in publicados],
            dir_execucao=self.dir_execucao,
        )
        no_projeto = escritas_no_projeto(gerados)
        if no_projeto:
            self.diario.registrar(
                [
                    EntradaDoDiario(
                        destino=caminho,
                        classificacao=Classificacao.CRIADO,
                        hash_novo=hash_do_arquivo(caminho),
                        execucao=self.dir_execucao.name,
                    )
                    for caminho in no_projeto
                ]
            )
        for gerada in gerados:
            self.registro.ok(f"pipeline · {gerada.render()}")
        self.registro.evento(
            TipoDeEvento.ARTEFATOS,
            estagio="pipeline_ci",
            arquivos=medir_arquivos([g.escrita for g in gerados if g.escrita is not None]),
            plataformas=[g.plataforma.chave for g in gerados],
        )
        return gerados

    def _rodar_recurso(self, recurso: Recurso) -> ResultadoDoRecurso:
        resultado = ResultadoDoRecurso(recurso=recurso.nome)
        self.persistencia.iniciar_recurso()
        area = criar_area(
            recurso=recurso.nome,
            destino_recurso=recurso.caminho_testes,
            destino_schemas=recurso.caminho_schemas,
            dir_execucao=self.dir_execucao,
            criados_antes=self.diario.criados(),
        )
        try:
            if self.reaproveitar is not None:
                decisoes = self.bloco_reaproveitado(recurso, area)
                manifesto, plano = decisoes.manifesto, decisoes.plano
                dossie, inventario = decisoes.dossie, decisoes.inventario
            else:
                saida_mapeador, gate_a_ok, tentativas_a = self.bloco1(recurso, area)
                resultado.gate_a = gate_a_ok
                resultado.tentativas_mapeador = tentativas_a

                plano = self.bloco_plano(recurso, saida_mapeador)
                manifesto = saida_mapeador.manifesto
                dossie, inventario = saida_mapeador.dossie, saida_mapeador.inventario

            _saida_executor, gate_b_ok, tentativas_b = self.bloco2(
                recurso,
                manifesto,
                area,
                plano,
                dossie=dossie,
                inventario=inventario,
            )
            resultado.gate_b = gate_b_ok
            resultado.tentativas_executor = tentativas_b

            # Só aqui o projeto do consumidor é tocado. Antes desta linha, uma queda
            # em qualquer ponto o deixa exatamente como estava.
            resultado.diario = self.persistencia.publicar(recurso, area)
            resultado.publicado = True

            execucao_de_testes = self.bloco3(recurso)
            resultado.cobertura = execucao_de_testes.contadores
            resultado.execucao_de_testes = execucao_de_testes.estado
            resultado.motivo_da_execucao_de_testes = execucao_de_testes.motivo
            resultado.divergencias = list(self.persistencia.divergencias)
            resultado.estado = (
                EstadoDoRecurso.REQUER_REVISAO
                if resultado.divergencias
                else EstadoDoRecurso.APROVADO
            )
            area.descartar()
        except (FalhaDeGate, FalhaDeEstagio, FalhaDePublicacao) as erro:
            resultado.estado = EstadoDoRecurso.REPROVADO
            resultado.staging = area.dir_recurso
            resultado.motivo = str(erro)
            resultado.arquivos_reprovados = list(erro.arquivos)
            resultado.codigos_remanescentes = erro.codigos
            resultado.divergencias = list(self.persistencia.divergencias)
            self.registro.falha(str(erro))
            self.registro.evento(
                TipoDeEvento.RECURSO_FALHOU, recurso=recurso.nome, motivo=str(erro)
            )
            if erro.arquivos:
                # A3: os arquivos ficam em disco de propósito (é o que se inspeciona
                # para entender a falha), mas o efeito não pode ser silencioso. Com o
                # staging, "em disco" passou a significar "no diretório da execução"
                # quando a falha veio antes da publicação.
                self.registro.evento(
                    TipoDeEvento.ARTEFATOS_REPROVADOS,
                    recurso=recurso.nome,
                    codigos=erro.codigos,
                    publicado=resultado.publicado,
                    arquivos=medir_arquivos(erro.arquivos),
                )
        finally:
            # O staging é o único diretório nosso que fica dentro do projeto do
            # consumidor. Quando sobra, ou está vazio — e aí é só lixo, some — ou
            # tem o artefato reprovado, e aí precisa ser dito onde ele está. O
            # `finally` cobre também a saída por `ErroDeFerramenta`, que não passa
            # pelo `except` acima e antes deixava o diretório sem menção nenhuma.
            if area.dir_recurso.is_dir():
                if any(area.dir_recurso.rglob("*")):
                    self.registro.evento(
                        TipoDeEvento.STAGING_MANTIDO,
                        recurso=recurso.nome,
                        diretorio=area.dir_recurso,
                    )
                else:
                    area.descartar()
        self.registro.evento(
            TipoDeEvento.RECURSO_CONCLUIDO,
            recurso=recurso.nome,
            estado=resultado.estado.value,
            sucesso=resultado.sucesso,
            tentativas_mapeador=resultado.tentativas_mapeador,
            tentativas_executor=resultado.tentativas_executor,
            execucao_de_testes=resultado.execucao_de_testes,
        )
        return resultado
