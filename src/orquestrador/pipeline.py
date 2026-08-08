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

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from langchain_core.language_models import BaseChatModel

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.analise_estatica.extrator_de_superficie import extrair as extrair_superficie
from orquestrador.config import Config
from orquestrador.contratos import (
    Classificacao,
    Delta,
    DivergenciaDeSchema,
    EntradaDoDiario,
    EstadoDoRecurso,
    EstagioDelta,
    Manifesto,
    Recurso,
    ResultadoGate,
    SaidaExecutor,
    SaidaMapeador,
    SuperficieDoProjeto,
)
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
)
from orquestrador.ferramentas.arquivos import sob_a_raiz
from orquestrador.ferramentas.graphify import Graphify, ResultadoPreparacao
from orquestrador.ferramentas.processo import VARIAVEIS_DO_CYPRESS
from orquestrador.ferramentas.publicacao import (
    NOME_DO_DIARIO,
    AreaDeStaging,
    Diario,
    criar_area,
    remover_criados,
)
from orquestrador.ferramentas.scripts_qa import Cobertura
from orquestrador.gates import gate_a, gate_b
from orquestrador.gates.saidas import resumo_da_cobertura
from orquestrador.llm.cliente import criar_modelo
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.simulacao import Roteiros

# Estado da suíte em runtime, no Bloco 3. São dois estados e não um booleano
# porque a diferença que importa é entre "os testes passaram" e "ninguém sabe":
# sem relatório desta execução não há evidência de runtime nenhuma, e o resultado
# precisa dizer isso em vez de deixar a cobertura estática passar por prova.
EXECUTADO = "EXECUTADO"
NAO_EXECUTADO = "NAO_EXECUTADO"

# Marca substituída pelo caminho do relatório desta execução no comando do Cypress.
MARCA_RELATORIO = "{relatorio}"

# Os dois gates que têm loop de reparo. O tipo fecha a porta que o `# type: ignore`
# de `_ciclo` mantinha aberta: o estágio do delta era montado por interpolação
# (`f"gate_{gate}"`), e um `gate="c"` produziria a string "gate_c", que nenhum
# consumidor de `EstagioDelta` reconhece.
NomeDeGate = Literal["a", "b"]
_ESTAGIO_DO_GATE: dict[NomeDeGate, EstagioDelta] = {"a": "gate_a", "b": "gate_b"}

# O artefato que atravessa uma volta do loop de reparo: `SaidaMapeador` no Bloco 1,
# `SaidaExecutor` no Bloco 2.
Artefato = TypeVar("Artefato")


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


def _unir_caminhos(atuais: list[Path], novos: list[Path] | None) -> list[Path]:
    """Concatena preservando ordem e sem repetir."""
    unidos = list(atuais)
    for caminho in novos or []:
        if caminho not in unidos:
            unidos.append(caminho)
    return unidos


# Guarda contra schema recursivo, no mesmo espírito do PROFUNDIDADE_MAXIMA de
# `campos/schema.mjs`.
_PROFUNDIDADE_DE_CAMPOS = 4


def nomes_de_campos(esquema: Any, *, profundidade: int = _PROFUNDIDADE_DE_CAMPOS) -> set[str]:
    """Todo nome que aparece sob algum `properties` do schema, até uma profundidade.

    Varredura deliberadamente rasa e tolerante: ela serve para responder "este nome
    não aparece em lugar nenhum do arquivo", não para decidir qual nó é a entidade.
    Quem decide isso é `campos/schema.mjs`, e reimplementar a heurística aqui criaria
    uma segunda fonte de verdade para o denominador da cobertura.
    """
    if profundidade <= 0 or not isinstance(esquema, dict):
        return set()
    # `Any` é a anotação certa na entrada: o argumento é um nó qualquer de um JSON
    # Schema escrito por um LLM, e a função existe justamente para atravessá-lo sem
    # exigir forma. O que ela devolve, porém, é `set[str]` — o `Any` para aqui.
    no = cast(dict[str, Any], esquema)
    nomes: set[str] = set()
    propriedades = no.get("properties")
    if isinstance(propriedades, dict):
        for nome, subesquema in cast(dict[str, Any], propriedades).items():
            nomes.add(str(nome))
            nomes |= nomes_de_campos(subesquema, profundidade=profundidade - 1)
    if (itens := no.get("items")) is not None:
        nomes |= nomes_de_campos(itens, profundidade=profundidade - 1)
    return nomes


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
        # Divergências de schema do recurso em curso. Zerada a cada recurso: é o
        # princípio 3 aplicado ao próprio pipeline — nada de um recurso pode
        # decidir o desfecho do seguinte.
        self._divergencias: list[DivergenciaDeSchema] = []

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
        self._extrair_superficie()
        return resultado

    def _extrair_superficie(self) -> None:
        """Lê os módulos compartilhados do projeto. Determinístico, zero token.

        Falha aqui é pré-condição do projeto de testes, não do recurso: ela
        interrompe a execução antes de qualquer chamada de modelo, porque o
        executor não tem como adivinhar nomes de export que ninguém contou a ele —
        e o delta do Gate B ("import não resolve") não é acionável.
        """
        self.superficie = extrair_superficie(self.config)
        destino = self.dir_execucao / "artefatos" / "superficie-do-projeto.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(self.superficie.para_json(), encoding="utf-8", newline="\n")

        self.registro.evento(
            TipoDeEvento.SUPERFICIE,
            raiz=self.superficie.raiz,
            modulos=[
                {
                    "caminho": modulo.caminho,
                    "import_do_recurso": modulo.import_do_recurso,
                    "import_do_support": modulo.import_do_support,
                    "exports": [exportado.nome for exportado in modulo.exports],
                }
                for modulo in self.superficie.modulos
            ],
            artefato=destino,
        )
        self.registro.ok(
            f"superfície do projeto: {len(self.superficie.modulos)} módulo(s), "
            f"{self.superficie.total_de_exports} export(s) em {self.superficie.raiz}"
        )

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
                *self._persistir_schemas(recurso, saida, area),
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

        return self._ciclo(
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

    def _persistir_schemas(
        self, recurso: Recurso, saida: SaidaMapeador, area: AreaDeStaging
    ) -> list[Path]:
        """Grava os schemas do mapeador sem passar por cima do que é do cliente.

        No desenho da skill o schema de entrada é artefato **pré-existente** do
        projeto consumidor — o AJV valida respostas com ele e ele quebra os testes se
        estiver errado (`scripts/cobertura/campos/schema.mjs`). É dessa independência
        que vem a autoridade dele como denominador: ele não foi escrito por quem vai
        ser medido. Sobrescrever em silêncio quebraria suíte alheia e trocaria uma
        régua independente pela régua do próprio modelo.

        Daí a regra: arquivo que já era do consumidor é copiado para o staging e
        preservado; o resto é escrito à vontade a cada tentativa, senão o loop de
        reparo do Gate A nunca convergiria sobre o schema.

        Quem responde "já era do consumidor?" é a área de staging, que fotografou o
        destino no início do recurso. O conjunto de schemas gravados que esta classe
        mantinha respondia à mesma pergunta com estado próprio — e estado próprio
        para uma pergunta sobre o disco erra na primeira vez que o disco muda por
        fora.
        """
        escritos: list[Path] = []
        preservados: list[Path] = []

        for arquivo in saida.schemas:
            alvo = recurso.caminho_schemas / arquivo.caminho
            if area.ja_era_do_consumidor(alvo):
                preservados.append(alvo)
                if divergencia := self._divergencia(recurso, alvo, arquivo.conteudo):
                    self._divergencias.append(divergencia)
                area.preservar_schema(arquivo.caminho)
                continue
            escritos.append(area.escrever_schema(arquivo.caminho, arquivo.conteudo))

        if preservados:
            self.registro.evento(
                TipoDeEvento.SCHEMAS_PRESERVADOS,
                recurso=recurso.nome,
                arquivos=[str(caminho) for caminho in preservados],
            )
        return escritos

    def _divergencia(
        self, recurso: Recurso, alvo: Path, conteudo_emitido: str
    ) -> DivergenciaDeSchema | None:
        """O que o mapeador achou no backend e o schema preservado não declara.

        Não reprova: a autoridade sobre o arquivo é do Gate A, e o arquivo é do
        consumidor. Mas também não pode passar como aviso e o recurso terminar
        aprovado — campo que existe no backend e não está no schema sai do
        denominador sem deixar rastro, e a cobertura sobe porque a régua encolheu.
        O desfecho é `REQUER_REVISAO`; ver `_rodar_recurso`.
        """
        try:
            existente = nomes_de_campos(json.loads(alvo.read_text(encoding="utf-8")))
            emitido = nomes_de_campos(json.loads(conteudo_emitido))
        except (OSError, ValueError):
            # Schema ilegível é caso do gate, que reprova com a mensagem certa. Aqui
            # só desistimos da comparação.
            return None
        ausentes = sorted(emitido - existente)
        if not ausentes:
            return None
        return DivergenciaDeSchema(recurso=recurso.nome, arquivo=alvo, campos_ausentes=ausentes)

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

        return self._ciclo(
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
        # O import tardio é o que mantém `executar` resolvido em tempo de
        # chamada. Ligado no topo, `rodar` viraria uma referência fixada na
        # importação do módulo, e o `monkeypatch.setattr(processo, "executar", ...)`
        # dos testes do Bloco 3 passaria a não ter efeito nenhum — a suíte roda o
        # Cypress de verdade. Verificado: subir este import reprova 4 testes de
        # tests/test_invariante_falhas_isoladas.py.
        from orquestrador.ferramentas.processo import executar as rodar  # noqa: PLC0415

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

        saida = rodar(
            argumentos,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
            # O ambiente do subprocesso é allowlist; sem estas, o `cypress.config.js`
            # do consumidor sobe sem a configuração que ele espera e a suíte falha
            # por um motivo que não tem nada a ver com o teste gerado.
            variaveis_extras=VARIAVEIS_DO_CYPRESS,
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

    # -- loop de reparo -----------------------------------------------------

    def _ciclo(
        self,
        *,
        estagio: str,
        gate: NomeDeGate,
        recurso: Recurso,
        produzir: Callable[[int, Delta | None, str | None], Artefato],
        persistir: Callable[[Artefato], list[Path]],
        avaliar: Callable[[Artefato], ResultadoGate],
        texto_do_artefato: Callable[[Artefato, Delta], str],
    ) -> tuple[Artefato, ResultadoGate, int]:
        """Gera → persiste → avalia → (delta → repete). O coração da arquitetura.

        Genérico em `Artefato` porque o laço é o mesmo para o `SaidaMapeador` do
        Bloco 1 e o `SaidaExecutor` do Bloco 2, e a única coisa que ele faz com o
        artefato é passá-lo adiante para os quatro callbacks. Com `Any` no lugar do
        parâmetro de tipo, ninguém conferia que os quatro falam do mesmo artefato — e
        o tipo devolvido a `bloco1`/`bloco2` era `Any`, o que apagava a checagem de
        tudo que eles fazem com a saída depois.
        """
        maximo = self.config.gate(gate).max_tentativas
        delta: Delta | None = None
        artefato_atual: str | None = None
        resultado: ResultadoGate | None = None
        persistidos: list[Path] = []

        for tentativa in range(1, maximo + 1):
            marca = len(self.telemetria.chamadas)
            marca_tools = len(self.telemetria.tools)
            try:
                artefato = produzir(tentativa, delta, artefato_atual)
            except FalhaDeEstagio as erro:
                # O que já foi escrito em disco continua lá; quem falha precisa dizer
                # o que deixou para trás (A3).
                erro.arquivos = list(persistidos)
                raise
            finally:
                # No `finally` de propósito: a tentativa fica registrada mesmo quando
                # o estágio explode, e é dela que sai a medida de entrada (A2).
                self._registrar_tentativa(
                    estagio=estagio,
                    recurso=recurso,
                    tentativa=tentativa,
                    delta=delta,
                    desde=marca,
                    desde_tools=marca_tools,
                )
            persistidos = _unir_caminhos(persistidos, persistir(artefato))
            resultado = avaliar(artefato)

            self.registro.evento(
                TipoDeEvento.GATE,
                gate=f"gate_{gate}",
                estagio=estagio,
                recurso=recurso.nome,
                tentativa=tentativa,
                aprovado=resultado.aprovado,
                violacoes=[v.model_dump() for v in resultado.violacoes],
                avisos=[v.model_dump() for v in resultado.avisos],
            )
            for aviso in resultado.avisos:
                self.registro.aviso(aviso.render())

            if resultado.aprovado:
                self.registro.ok(
                    f"gate_{gate} aprovou {recurso.nome} na tentativa {tentativa}/{maximo}"
                )
                return artefato, resultado, tentativa

            self.registro.falha(
                f"gate_{gate} reprovou {recurso.nome} na tentativa {tentativa}/{maximo}: "
                f"{len(resultado.violacoes)} violação(ões) "
                f"[{', '.join(sorted({v.codigo for v in resultado.violacoes}))}]"
            )
            for violacao in resultado.violacoes[:10]:
                self.registro.info(f"    {violacao.render()}")

            delta = Delta(
                estagio=_ESTAGIO_DO_GATE[gate],
                recurso=recurso.nome,
                violacoes=resultado.violacoes,
                tentativa=tentativa,
            )
            # O delta entra na projeção do artefato: é ele que diz quais arquivos e
            # quais linhas precisam estar à vista. Ver `llm.montagem`.
            artefato_atual = texto_do_artefato(artefato, delta)
            self.registro.evento(
                TipoDeEvento.DELTA,
                estagio=f"gate_{gate}",
                de=estagio,
                recurso=recurso.nome,
                tentativa=tentativa,
                codigos=[v.codigo for v in resultado.violacoes],
                bytes_do_artefato=len(artefato_atual),
            )

        violacoes = list(resultado.violacoes) if resultado else []
        codigos = sorted({v.codigo for v in violacoes})
        raise FalhaDeGate(
            f"gate_{gate} reprovou o recurso {recurso.nome!r} em {maximo} tentativa(s). "
            f"Códigos remanescentes: {', '.join(codigos) or '(nenhum)'}",
            arquivos=persistidos,
            violacoes=violacoes,
        )

    def _registrar_tentativa(
        self,
        *,
        estagio: str,
        recurso: Recurso,
        tentativa: int,
        delta: Delta | None,
        desde: int,
        desde_tools: int = 0,
    ) -> None:
        """Evento `estagio_tentativa` com o que foi efetivamente enviado ao modelo.

        Os tamanhos vêm das chamadas registradas durante esta tentativa: a
        instrução fixa (constante, linha de base) e a entrada. É o par que torna o
        princípio 2 verificável a partir do log, sem reexecutar nada.

        O resumo de tools responde a outra pergunta, do mesmo log: a exploração
        seguiu a instrução do estágio? `primeira_tool` é o teste mais direto — a
        instrução manda consultar o grafo antes de procurar no backend, então
        qualquer coisa diferente de `graphify_query` aqui é desvio.
        """
        chamadas = self.telemetria.chamadas[desde:]
        tools = self.telemetria.tools[desde_tools:]
        por_nome: dict[str, int] = {}
        for tool in tools:
            por_nome[tool.nome] = por_nome.get(tool.nome, 0) + 1
        self.registro.evento(
            TipoDeEvento.ESTAGIO_TENTATIVA,
            estagio=estagio,
            recurso=recurso.nome,
            tentativa=tentativa,
            com_delta=delta is not None,
            violacoes_no_delta=[v.codigo for v in delta.violacoes] if delta else [],
            chamadas=len(chamadas),
            caracteres_instrucao=max((c.caracteres_instrucao for c in chamadas), default=0),
            caracteres_entrada=sum(c.caracteres_entrada for c in chamadas),
            tools=len(tools),
            tools_por_nome=por_nome,
            caracteres_de_tools=sum(tool.caracteres for tool in tools),
            tools_com_erro=sum(tool.erro for tool in tools),
            primeira_tool=tools[0].nome if tools else None,
        )

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
                )
                self.registro.falha(
                    f"execução interrompida em {recurso.nome}: ferramenta indisponível. {erro}"
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
        self._divergencias = []
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
            self._publicar(recurso, area, resultado)

            execucao_de_testes = self.bloco3(recurso)
            resultado.cobertura = execucao_de_testes.contadores
            resultado.execucao_de_testes = execucao_de_testes.estado
            resultado.motivo_da_execucao_de_testes = execucao_de_testes.motivo
            resultado.divergencias = list(self._divergencias)
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
            resultado.divergencias = list(self._divergencias)
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

    def _publicar(
        self, recurso: Recurso, area: AreaDeStaging, resultado: ResultadoDoRecurso
    ) -> None:
        """Leva o staging aprovado para o projeto do consumidor e registra o diário."""
        anteriores = [
            entrada
            for entrada in self.diario.carregar().entradas
            if entrada.recurso == recurso.nome
            and entrada.classificacao is Classificacao.CRIADO
            and sob_a_raiz(entrada.destino, recurso.caminho_testes)
        ]

        entradas = area.publicar()
        resultado.diario = entradas
        resultado.publicado = True

        publicados = {entrada.destino for entrada in entradas}
        obsoletos = [entrada for entrada in anteriores if entrada.destino not in publicados]
        removidos, recusados = remover_criados(obsoletos, sob=recurso.caminho_testes)

        self.diario.registrar(entradas, esquecer=set(removidos))
        self.registro.evento(
            TipoDeEvento.PUBLICACAO,
            recurso=recurso.nome,
            arquivos=[
                {
                    "destino": str(entrada.destino),
                    "classificacao": entrada.classificacao.value,
                    "hash_anterior": entrada.hash_anterior,
                    "hash_novo": entrada.hash_novo,
                }
                for entrada in entradas
            ],
            obsoletos_removidos=[str(caminho) for caminho in removidos],
            obsoletos_mantidos=[str(caminho) for caminho in recusados],
        )
        criados = sum(1 for e in entradas if e.classificacao is Classificacao.CRIADO)
        modificados = sum(1 for e in entradas if e.classificacao is Classificacao.MODIFICADO)
        self.registro.ok(
            f"{recurso.nome} publicado: {criados} arquivo(s) criado(s), "
            f"{modificados} modificado(s), {len(removidos)} obsoleto(s) removido(s)"
        )
        if recusados:
            # Spec que nasceu conosco e alguém editou depois. Não é nosso para
            # apagar, e o silêncio faria parecer que a limpeza foi completa.
            self.registro.aviso(
                f"{len(recusados)} arquivo(s) que criamos em execução anterior "
                "mudaram desde então e NÃO foram removidos: "
                + ", ".join(str(caminho) for caminho in recusados)
            )
        for divergencia in self._divergencias:
            self.registro.aviso(divergencia.render())
        if self._divergencias:
            destino = self.dir_execucao / "artefatos" / recurso.nome / "divergencias-de-schema.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    [d.model_dump(mode="json") for d in self._divergencias],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self.registro.evento(
                TipoDeEvento.SCHEMAS_DIVERGENTES,
                recurso=recurso.nome,
                artefato=destino,
                divergencias=[d.model_dump(mode="json") for d in self._divergencias],
            )
