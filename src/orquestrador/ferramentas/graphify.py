"""Wrappers do Graphify (Bloco 0 e tools do mapeador).

O Bloco 0 chama o `graphify` **direto**, e não mais o `qa-reindex.mjs` da skill.
O `graphify` é um pacote Python (`graphifyy`), declarado como dependência deste
projeto: ele vive no mesmo ambiente virtual do orquestrador, e não numa pasta
externa que o cliente teria de instalar à parte. O que o script da skill fazia e
foi replicado aqui é a linha de comando com `--code-only` e a lista de exclusões;
o resto dele — verificação de versão travada num manifesto de outra skill,
exigência de árvore Git limpa, HTML de visualização — era acoplamento sem função
para este pipeline.

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
* `extract` **sem `--code-only`** faz extração semântica paga por LLM sobre o
  backend inteiro, sem avisar. A flag é obrigatória aqui, e é o motivo de a
  extração ser custo zero de token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.ferramentas.processo import SaidaProcesso, executar

# Segunda camada sobre `--code-only`, herdada do `qa-reindex.mjs`: Dockerfile,
# `.gitlab-ci.yml` e `TODAS_VIEWS` classificam como CÓDIGO no Graphify, então a
# flag não os remove e a exclusão explícita continua necessária. Os formatos de
# documento são redundantes com a flag de propósito — lista de glob erra por
# digitação em silêncio; a flag afirmativa, não.
EXCLUSOES: tuple[str, ...] = (
    "**/*.md",
    "**/*.txt",
    "**/*.pdf",
    "**/*.docx",
    "**/*.xlsx",
    ".gitlab-ci.yml",
    "README.md",
    "Dockerfile",
    "**/TODAS_VIEWS",
)


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

    def mapa_em_cache(self) -> bool:
        """O `graph.json` existe e é JSON legível com nós dentro.

        Substitui o `--check` do `qa-reindex.mjs`. Aquele script também exigia
        árvore Git limpa e versão do Graphify travada num manifesto de outra
        skill; nenhuma das duas coisas protege este pipeline — quem responde por
        grafo defasado é o `extrator_de_endpoints`, que compara o que o grafo
        declara com o que existe em disco.
        """
        if not self.graph.is_file():
            return False
        try:
            with self.graph.open(encoding="utf-8") as arquivo:
                # `graph.json` tem dezenas de MB: basta o começo para saber se é
                # JSON e se tem conteúdo. Ler inteiro só para dizer "existe"
                # custaria segundos e memória a cada execução.
                inicio = arquivo.read(4096)
        except OSError:
            return False
        return inicio.lstrip().startswith("{") and '"' in inicio

    def extrair(self, backend: Path | None = None) -> SaidaProcesso:
        """`graphify extract . --code-only --out <destino>` na raiz do backend.

        Extração puramente estática (AST), zero token. `--code-only` é o que
        impede a extração semântica paga; as `EXCLUSOES` cobrem o que o Graphify
        classifica como código e não é.
        """
        alvo = Path(backend or self.config.caminhos.backend)
        destino = self.graph.parent
        destino.mkdir(parents=True, exist_ok=True)
        argumentos = [
            self.config.execucao.graphify,
            "extract",
            ".",
            "--code-only",
            "--out",
            str(destino),
        ]
        for padrao in EXCLUSOES:
            argumentos += ["--exclude", padrao]
        return executar(argumentos, cwd=alvo, timeout_s=self.config.execucao.timeout_s)

    def preparar(self) -> ResultadoPreparacao:
        """Bloco 0 completo: usa o mapa em cache ou extrai um novo."""
        if self.mapa_em_cache():
            return ResultadoPreparacao(
                ok=True,
                regenerou=False,
                graph=self.graph,
                detalhe="mapa em cache válido; nada a regenerar",
            )

        extracao = self.extrair()
        ok = extracao.codigo == 0 and self.mapa_em_cache()
        detalhe = (
            "mapa regenerado a partir do backend"
            if ok
            else f"falha ao regenerar o mapa (código {extracao.codigo}): {extracao.texto[:800]}"
        )
        return ResultadoPreparacao(
            ok=ok, regenerou=True, graph=self.graph, detalhe=detalhe, saidas=(extracao,)
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
