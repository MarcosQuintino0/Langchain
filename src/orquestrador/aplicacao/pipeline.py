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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.aplicacao.ciclo_de_reparo import CicloDeReparo
from orquestrador.aplicacao.persistencia import PersistenciaDeArtefatos
from orquestrador.aplicacao.simulacao import Roteiros
from orquestrador.config import Config
from orquestrador.dominio.artefatos import SaidaExecutor, SaidaMapeador
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.propriedade import DivergenciaDeSchema, EntradaDoDiario
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
from orquestrador.ferramentas.publicacao import (
    NOME_DO_DIARIO,
    AreaDeStaging,
    Diario,
    criar_area,
)
from orquestrador.ferramentas.scripts_qa import Cobertura
from orquestrador.gates import gate_a, gate_b
from orquestrador.gates.saidas import resumo_da_cobertura
from orquestrador.llm.cliente import criar_modelo
from orquestrador.observabilidade.eventos import TipoDeEvento
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
    ) -> None:
        self.config = config
        self.registro = registro
        self.telemetria = Telemetria(registro)
        self.dry_run = dry_run
        self.roteiros = roteiros
        self.dir_execucao = dir_execucao
        self.pular_cypress = pular_cypress
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
        inventario_path = self.dir_execucao / "artefatos" / recurso.nome / "inventario.json"

        def produzir(tentativa: int, delta: Delta | None, atual: str | None) -> SaidaMapeador:
            return agente_mapeador.executar(
                self.config,
                recurso,
                modelo=self.modelo(agente_mapeador.ESTAGIO, recurso.nome, tentativa),
                telemetria=self.telemetria,
                registro=self.registro,
                tentativa=tentativa,
                delta=delta,
                artefato_atual=atual,
            )

        def persistir(saida: SaidaMapeador) -> list[Path]:
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
                arquivos=[str(caminho) for caminho in escritos],
            )

            inventario_path.parent.mkdir(parents=True, exist_ok=True)
            inventario_path.write_text(saida.inventario.para_json(), encoding="utf-8", newline="\n")
            # O inventário fica no diretório da execução, que é nosso, e por isso
            # não entra na conta do que precisa ser publicado.
            return escritos

        def avaliar(saida: SaidaMapeador) -> ResultadoGate:
            return gate_a.executar(
                self.config,
                recurso,
                dir_recurso=area.dir_recurso,
                dir_schemas=area.dir_schemas,
                inventario=saida.inventario,
                manifesto=saida.manifesto,
            )

        return self.ciclo.executar(
            estagio=agente_mapeador.ESTAGIO,
            gate="a",
            recurso=recurso,
            produzir=produzir,
            persistir=persistir,
            avaliar=avaliar,
            texto_do_artefato=lambda _saida, _delta: agente_mapeador.artefato_em_disco(
                manifesto=area.dir_recurso / "_support" / "cobertura.json",
                inventario=inventario_path,
                dir_schemas=area.dir_schemas,
                recurso=recurso.nome,
            ),
        )

    # -- Bloco 2 + Gate B ---------------------------------------------------

    def bloco2(
        self, recurso: Recurso, manifesto: Manifesto, area: AreaDeStaging
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
            )

        def persistir(saida: SaidaExecutor) -> list[Path]:
            escritos = agente_executor.escrever(area, saida)
            self.registro.evento(
                TipoDeEvento.ARTEFATOS,
                estagio=agente_executor.ESTAGIO,
                recurso=recurso.nome,
                arquivos=[str(caminho) for caminho in escritos],
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

        saida = Cobertura(self.config).executar(
            recurso.caminho_testes,
            report=report,
            out=self.dir_execucao / "cobertura" / recurso.nome / "cobertura.html",
        )
        contadores = resumo_da_cobertura(saida)
        self.registro.evento(
            TipoDeEvento.COBERTURA,
            recurso=recurso.nome,
            contadores=contadores,
            execucao_de_testes=estado,
            saida=saida.texto[:2000],
        )
        if contadores:
            self.registro.ok(f"relatório de cobertura ({estado}): {contadores}")
        else:
            self.registro.aviso(
                f"qa-cobertura.mjs não produziu contadores: {saida.texto[:400] or '(sem saída)'}"
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
        for indice, recurso in enumerate(recursos):
            try:
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
        return resultados

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
            saida_mapeador, gate_a_ok, tentativas_a = self.bloco1(recurso, area)
            resultado.gate_a = gate_a_ok
            resultado.tentativas_mapeador = tentativas_a

            _saida_executor, gate_b_ok, tentativas_b = self.bloco2(
                recurso, saida_mapeador.manifesto, area
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
                    arquivos=[str(caminho) for caminho in erro.arquivos],
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
