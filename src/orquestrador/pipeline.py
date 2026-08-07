"""Os loops de controle do pipeline.

    Bloco 0  qa-reindex (Graphify, AST)            determinístico, zero token
    Bloco 1  mapeador (LLM + tools)   → Gate A     um recurso por vez
    Bloco 2  executor (LLM, sem tools) → Gate B    um recurso por vez
    Bloco 3  Cypress + qa-cobertura                determinístico

Os seis princípios que o desenho serve estão no README. Os dois que aparecem
literalmente neste arquivo:

* princípio 1 — o handoff entre estágios é **artefato em disco**. O manifesto vai
  para `_support/cobertura.json`, os specs para o diretório do recurso, o
  inventário para o diretório da execução. Nenhum estágio recebe conversa.
* princípio 2 — o loop de reparo envia **só o delta**: instrução fixa do estágio +
  artefato atual + violações. Sem histórico de tentativas.

A CLI que dirige tudo isto vive em `cli.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from orquestrador.agentes import executor as agente_executor
from orquestrador.agentes import mapeador as agente_mapeador
from orquestrador.config import Config
from orquestrador.contratos import (
    Delta,
    Manifesto,
    Recurso,
    ResultadoGate,
    SaidaExecutor,
    SaidaMapeador,
)
from orquestrador.excecoes import FalhaDeEstagio, FalhaDeGate
from orquestrador.ferramentas.graphify import Graphify, ResultadoPreparacao
from orquestrador.ferramentas.scripts_qa import Cobertura
from orquestrador.gates import gate_a, gate_b
from orquestrador.gates.parser import resumo_da_cobertura
from orquestrador.llm.cliente import criar_modelo
from orquestrador.observabilidade.registro import Registro
from orquestrador.observabilidade.telemetria import Telemetria
from orquestrador.simulacao import Roteiros


@dataclass
class ResultadoDoRecurso:
    recurso: str
    sucesso: bool = False
    tentativas_mapeador: int = 0
    tentativas_executor: int = 0
    gate_a: ResultadoGate | None = None
    gate_b: ResultadoGate | None = None
    cobertura: dict[str, Any] = field(default_factory=dict)
    motivo: str = ""
    # A3: o que ficou em disco em estado reprovado. Não é apagado por padrão.
    arquivos_reprovados: list[Path] = field(default_factory=list)
    codigos_remanescentes: list[str] = field(default_factory=list)


def _unir_caminhos(atuais: list[Path], novos: list[Path] | None) -> list[Path]:
    """Concatena preservando ordem e sem repetir."""
    unidos = list(atuais)
    for caminho in novos or []:
        if caminho not in unidos:
            unidos.append(caminho)
    return unidos


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
        self._modelos_reais: dict[str, Any] = {}

    # -- modelos ------------------------------------------------------------

    def modelo(self, estagio: str, recurso: str, tentativa: int) -> Any:
        """Modelo do estágio: fixture no dry-run, OpenRouter na execução real."""
        if self.dry_run:
            assert self.roteiros is not None
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
            "bloco0",
            ok=resultado.ok,
            regenerou=resultado.regenerou,
            graph=resultado.graph,
            detalhe=resultado.detalhe,
        )
        (self.registro.ok if resultado.ok else self.registro.aviso)(resultado.detalhe)
        return resultado

    # -- Bloco 1 + Gate A ---------------------------------------------------

    def bloco1(self, recurso: Recurso) -> tuple[SaidaMapeador, ResultadoGate, int]:
        self.registro.titulo(f"Bloco 1 — mapeador · {recurso.nome}")

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
            recurso.manifesto_path.parent.mkdir(parents=True, exist_ok=True)
            recurso.manifesto_path.write_text(
                saida.manifesto.para_json(), encoding="utf-8", newline="\n"
            )
            destino = self.dir_execucao / "artefatos" / recurso.nome
            destino.mkdir(parents=True, exist_ok=True)
            (destino / "inventario.json").write_text(
                saida.inventario.para_json(), encoding="utf-8", newline="\n"
            )
            # Só o manifesto entra na conta de "reprovado": ele fica no projeto do
            # usuário. O inventário fica no diretório da execução, que é nosso.
            return [recurso.manifesto_path]

        def avaliar(saida: SaidaMapeador) -> ResultadoGate:
            return gate_a.executar(
                self.config,
                recurso,
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
            texto_do_artefato=lambda saida: saida.manifesto.para_json(),
        )

    # -- Bloco 2 + Gate B ---------------------------------------------------

    def bloco2(
        self, recurso: Recurso, manifesto: Manifesto
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
            )

        def persistir(saida: SaidaExecutor) -> list[Path]:
            escritos = agente_executor.escrever(recurso, saida)
            self.registro.evento(
                "artefatos",
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
            avaliar=lambda _saida: gate_b.executar(self.config, recurso),
            texto_do_artefato=lambda saida: agente_executor.artefato_em_disco(recurso, saida),
        )

    # -- Bloco 3 ------------------------------------------------------------

    def bloco3(self, recurso: Recurso) -> dict[str, Any]:
        self.registro.titulo(f"Bloco 3 — execução e relatório · {recurso.nome}")
        report: Path | None = None

        if self.config.execucao.cypress and not self.pular_cypress:
            from orquestrador.ferramentas.processo import executar as rodar

            saida = rodar(
                self.config.execucao.cypress,
                cwd=self.config.caminhos.projeto_testes,
                timeout_s=self.config.execucao.timeout_s,
            )
            self.registro.evento(
                "cypress", recurso=recurso.nome, codigo=saida.codigo, saida=saida.texto[:4000]
            )
            candidato = self.config.caminhos.projeto_testes / "report.json"
            report = candidato if candidato.is_file() else None
        else:
            self.registro.info("execução do Cypress pulada (--pular-cypress ou não configurada)")

        saida = Cobertura(self.config).executar(
            recurso.caminho_testes,
            report=report,
            out=self.dir_execucao / "cobertura" / recurso.nome / "cobertura.html",
        )
        contadores = resumo_da_cobertura(saida)
        self.registro.evento(
            "cobertura", recurso=recurso.nome, contadores=contadores, saida=saida.texto[:2000]
        )
        if contadores:
            self.registro.ok(f"relatório de cobertura: {contadores}")
        else:
            self.registro.aviso(
                f"qa-cobertura.mjs não produziu contadores: {saida.texto[:400] or '(sem saída)'}"
            )
        return contadores

    # -- loop de reparo -----------------------------------------------------

    def _ciclo(
        self,
        *,
        estagio: str,
        gate: str,
        recurso: Recurso,
        produzir: Callable[[int, Delta | None, str | None], Any],
        persistir: Callable[[Any], list[Path]],
        avaliar: Callable[[Any], ResultadoGate],
        texto_do_artefato: Callable[[Any], str],
    ) -> tuple[Any, ResultadoGate, int]:
        """Gera → persiste → avalia → (delta → repete). O coração da arquitetura."""
        maximo = self.config.gate(gate).max_tentativas
        delta: Delta | None = None
        artefato_atual: str | None = None
        resultado: ResultadoGate | None = None
        persistidos: list[Path] = []

        for tentativa in range(1, maximo + 1):
            marca = len(self.telemetria.chamadas)
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
                )
            persistidos = _unir_caminhos(persistidos, persistir(artefato))
            resultado = avaliar(artefato)

            self.registro.evento(
                "gate",
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
                estagio=f"gate_{gate}",  # type: ignore[arg-type]
                recurso=recurso.nome,
                violacoes=resultado.violacoes,
                tentativa=tentativa,
            )
            artefato_atual = texto_do_artefato(artefato)
            self.registro.evento(
                "delta",
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
    ) -> None:
        """Evento `estagio_tentativa` com o que foi efetivamente enviado ao modelo.

        Os tamanhos vêm das chamadas registradas durante esta tentativa: a
        instrução fixa (constante, linha de base) e a entrada. É o par que torna o
        princípio 2 verificável a partir do log, sem reexecutar nada.
        """
        chamadas = self.telemetria.chamadas[desde:]
        self.registro.evento(
            "estagio_tentativa",
            estagio=estagio,
            recurso=recurso.nome,
            tentativa=tentativa,
            com_delta=delta is not None,
            violacoes_no_delta=[v.codigo for v in delta.violacoes] if delta else [],
            chamadas=len(chamadas),
            caracteres_instrucao=max((c.caracteres_instrucao for c in chamadas), default=0),
            caracteres_entrada=sum(c.caracteres_entrada for c in chamadas),
        )

    # -- orquestração -------------------------------------------------------

    def rodar(self, recursos: list[Recurso]) -> list[ResultadoDoRecurso]:
        self.bloco0()
        resultados: list[ResultadoDoRecurso] = []
        for recurso in recursos:
            resultados.append(self._rodar_recurso(recurso))
        return resultados

    def _rodar_recurso(self, recurso: Recurso) -> ResultadoDoRecurso:
        resultado = ResultadoDoRecurso(recurso=recurso.nome)
        try:
            saida_mapeador, gate_a_ok, tentativas_a = self.bloco1(recurso)
            resultado.gate_a = gate_a_ok
            resultado.tentativas_mapeador = tentativas_a

            _saida_executor, gate_b_ok, tentativas_b = self.bloco2(
                recurso, saida_mapeador.manifesto
            )
            resultado.gate_b = gate_b_ok
            resultado.tentativas_executor = tentativas_b

            resultado.cobertura = self.bloco3(recurso)
            resultado.sucesso = True
        except (FalhaDeGate, FalhaDeEstagio) as erro:
            resultado.motivo = str(erro)
            resultado.arquivos_reprovados = list(erro.arquivos)
            resultado.codigos_remanescentes = erro.codigos
            self.registro.falha(str(erro))
            self.registro.evento("recurso_falhou", recurso=recurso.nome, motivo=str(erro))
            if erro.arquivos:
                # A3: os arquivos ficam em disco de propósito (é o que se inspeciona
                # para entender a falha), mas o efeito não pode ser silencioso.
                self.registro.evento(
                    "artefatos_reprovados",
                    recurso=recurso.nome,
                    codigos=erro.codigos,
                    arquivos=[str(caminho) for caminho in erro.arquivos],
                )
        self.registro.evento(
            "recurso_concluido",
            recurso=recurso.nome,
            sucesso=resultado.sucesso,
            tentativas_mapeador=resultado.tentativas_mapeador,
            tentativas_executor=resultado.tentativas_executor,
        )
        return resultado


