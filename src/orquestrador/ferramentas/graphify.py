"""Wrappers do Graphify e do `qa-reindex.mjs` (Bloco 0 e tools do mapeador).

Armadilhas do CLI que estes wrappers absorvem (references/descobrir-backend.md):

* `--graph` é **obrigatório** em `query` e `affected`: sem ele o Graphify procura
  `./graphify-out` a partir do diretório atual e falha com `graph file not found`.
* `affected` roda com `--depth 1`; o padrão 2 mistura quem depende do recurso com
  quem depende dos dependentes.
* `No unique node match` significa **nome ambíguo**, não ausência de dependente —
  são respostas opostas, e o comando tem uma frase própria para o vazio real
  (`No affected nodes found`). O wrapper marca isso na resposta.
* O cabeçalho da resposta traz o nome que o Graphify **resolveu**; quando ele
  difere do pedido, os dependentes são de outra classe. O wrapper avisa.
* Nunca fazer grep dentro do `graph.json` — dezenas de MB.
* Nunca chamar `graphify extract` na mão: sem o `--code-only` que o reindex passa,
  ele faz extração semântica paga por LLM sobre o backend inteiro, sem avisar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.ferramentas.processo import SaidaProcesso, executar, executar_node


@dataclass(frozen=True)
class ResultadoPreparacao:
    """Veredito do Bloco 0."""

    ok: bool
    regenerou: bool
    graph: Path
    detalhe: str
    saidas: tuple[SaidaProcesso, ...] = ()


class Graphify:
    """Tudo que fala com o grafo estrutural do backend."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.graph = config.caminhos.graph_abs

    # -- Bloco 0 ------------------------------------------------------------

    def reindex_check(self) -> SaidaProcesso:
        """`qa-reindex.mjs --check`: valida o mapa em cache, sem reindexar.

        Roda com cwd no projeto consumidor: o destino do índice
        (`.agents/state/qa-api/graphify-out`) é relativo ao diretório atual.
        """
        return executar_node(
            self.config.caminhos.script("qa-reindex.mjs"),
            ["--check"],
            node=self.config.execucao.node,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )

    def reindex(self, backend: Path | None = None) -> SaidaProcesso:
        """`qa-reindex.mjs --backend <p>`: (re)gera o mapa. Extração AST local, zero token."""
        alvo = Path(backend or self.config.caminhos.backend)
        return executar_node(
            self.config.caminhos.script("qa-reindex.mjs"),
            ["--backend", str(alvo)],
            node=self.config.execucao.node,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )

    def preparar(self) -> ResultadoPreparacao:
        """Bloco 0 completo: check e, se necessário, reindex."""
        saidas: list[SaidaProcesso] = []
        check = self.reindex_check()
        saidas.append(check)
        if check.codigo == 0 and self.graph.is_file():
            return ResultadoPreparacao(
                ok=True,
                regenerou=False,
                graph=self.graph,
                detalhe="mapa em cache válido; nada a regenerar",
                saidas=tuple(saidas),
            )

        reindex = self.reindex()
        saidas.append(reindex)
        ok = reindex.codigo == 0 and self.graph.is_file()
        detalhe = (
            "mapa regenerado a partir do backend"
            if ok
            else f"falha ao regenerar o mapa (código {reindex.codigo}): {reindex.texto[:800]}"
        )
        return ResultadoPreparacao(
            ok=ok, regenerou=True, graph=self.graph, detalhe=detalhe, saidas=tuple(saidas)
        )

    # -- tools do mapeador --------------------------------------------------

    def query(self, simbolo: str, *, budget: int | None = None) -> str:
        """`graphify query "<Símbolo>" --graph <graph.json>`."""
        alvo = str(simbolo).strip()
        if not alvo:
            return "ERRO: informe o símbolo a consultar (vocabulário do código, não linguagem natural)."
        self._exigir_grafo()
        argumentos = [self.config.execucao.graphify, "query", alvo, "--graph", str(self.graph)]
        if budget:
            argumentos += ["--budget", str(budget)]
        return self._resposta(argumentos, alvo=alvo)

    def affected(self, entidade: str, depth: int = 1, relacao: str | None = None) -> str:
        """`graphify affected "<Entidade>" --depth 1 --graph <graph.json>`."""
        alvo = str(entidade).strip()
        if not alvo:
            return "ERRO: informe a entidade (a classe, não o controller)."
        self._exigir_grafo()
        argumentos = [
            self.config.execucao.graphify,
            "affected",
            alvo,
            "--depth",
            str(int(depth) if depth else 1),
            "--graph",
            str(self.graph),
        ]
        if relacao:
            argumentos += ["--relation", relacao]
        return self._resposta(argumentos, alvo=alvo)

    # -- internos -----------------------------------------------------------

    def _exigir_grafo(self) -> None:
        if not self.graph.is_file():
            raise ErroDeFerramenta(
                f"graph.json não encontrado em {self.graph}. "
                "Rode o Bloco 0 (qa-reindex) antes do mapeador."
            )

    def _resposta(self, argumentos: list[str], *, alvo: str) -> str:
        saida = executar(
            argumentos,
            cwd=self.config.caminhos.projeto_testes,
            timeout_s=self.config.execucao.timeout_s,
        )
        texto = saida.texto or "(sem saída)"
        return "\n".join(filter(None, [texto, avisar_armadilhas(texto, alvo)]))


def avisar_armadilhas(texto: str, alvo: str) -> str:
    """Traduz as duas respostas do Graphify que costumam ser lidas ao contrário."""
    avisos: list[str] = []
    if "No unique node match" in texto:
        avisos.append(
            f'ATENÇÃO: "No unique node match" para "{alvo}" é NOME AMBÍGUO, não ausência '
            "de dependente. Reconsulte com o nome exato da classe alvo ou com o ID: do nó. "
            "Não converta esta recusa em “não há dependente” — o vazio real tem outra frase "
            "(No affected nodes found)."
        )
    resolvido = re.search(r"Affected nodes for\s+([^\s(,]+)", texto)
    if resolvido and resolvido.group(1).strip() != alvo:
        avisos.append(
            f'ATENÇÃO: você pediu "{alvo}" e o Graphify resolveu por aproximação para '
            f'"{resolvido.group(1).strip()}". Os dependentes abaixo são DESSA outra classe. '
            "Confirme o nome antes de usar esta resposta."
        )
    return "\n".join(avisos)
