"""As cinco tools que o mapeador oferece ao modelo, e a medição de cada chamada.

O que muda este módulo é **o que o modelo consegue perguntar sobre o backend** —
o vocabulário das tools, o texto que ensina a usá-las e o adaptador para
`Graphify` e `ferramentas/arquivos`. Não muda quando o contrato de saída do
estágio muda, nem quando o loop de reparo muda: essas são razões de
`agentes/mapeador.py`.

Boa parte daqui é `description` de tool, e isso é deliberado: **é prompt**. Cada
frase existe porque uma execução real gastou voltas por falta dela — o aviso de
que `relacao` adivinhada devolve vazio com cara de resposta legítima, o de que
consultar o controller abstrato desce em todos os irmãos, o de que resposta maior
é reenviada em toda volta seguinte. Reescrever esse texto é mudar comportamento,
não estilo.

As tools **nunca levantam** para o modelo: erro vira a string `"ERRO: ..."`, que
ele lê e pode contornar. É por isso que `observado` detecta falha pelo prefixo do
texto, e é o único lugar do projeto onde falha vira dado em vez de exceção.
"""

from __future__ import annotations

import functools
import inspect
import itertools
import time
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.ferramentas import arquivos as fa
from orquestrador.ferramentas.graphify import Graphify
from orquestrador.observabilidade.medidas import RegistroDeTool
from orquestrador.observabilidade.telemetria import Telemetria


class ArgsGraphifyQuery(BaseModel):
    simbolo: str = Field(
        description=(
            "Símbolo no vocabulário do CÓDIGO (CancellationController, Cancellation), "
            "não linguagem natural: o matcher é literal, sem stemming nem sinônimos."
        )
    )
    budget: int | None = Field(
        default=None,
        description=(
            "Orçamento da resposta em tokens (padrão ~2000). Use APENAS quando a "
            "resposta avisar que truncou E o alvo não tiver aparecido. Prefira "
            "reconsultar o símbolo específico: resposta maior é reenviada em toda "
            "volta seguinte, então o custo dela se multiplica."
        ),
    )


class ArgsGraphifyAffected(BaseModel):
    entidade: str = Field(
        description=(
            "Nome da ENTIDADE (Cancellation), não do controller: a dependência que "
            "impede uma exclusão mora em outra entidade que aponta para esta."
        )
    )
    depth: int = Field(
        default=1,
        description="Profundidade da travessia. Use 1: o padrão 2 traz alcance transitivo.",
    )
    relacao: str | None = Field(
        default=None,
        description=(
            "Filtro de tipo de aresta. NUNCA use na primeira consulta: o vocabulário "
            "depende do extrator da linguagem, e um nome adivinhado devolve 'No "
            "affected nodes found' — vazio com cara de resposta legítima, que se lê "
            "como 'não há dependente'. Rode sem filtro, leia os rótulos entre "
            "colchetes da saída, e só então filtre por um deles. Em Java a herança "
            "sai como 'inherits', e é assim que se confirma quem herda de um "
            "controller abstrato sem depender do 'extends' da primeira linha."
        ),
    )


class ArgsLerArquivo(BaseModel):
    caminho: str = Field(description="Caminho do arquivo, relativo à raiz do backend.")
    offset: int = Field(default=0, description="Primeira linha (0 = início do arquivo).")
    limit: int = Field(default=500, description="Quantas linhas ler a partir do offset.")


class ArgsListarDiretorio(BaseModel):
    caminho: str = Field(description="Caminho do diretório, relativo à raiz do backend.")


class ArgsBuscar(BaseModel):
    padrao: str = Field(description="Expressão regular procurada no conteúdo dos arquivos.")
    glob: str | None = Field(
        default=None, description='Filtro de nome de arquivo, ex.: "*.java", "*.ts".'
    )


def criar_ferramentas(
    config: Config,
    *,
    estagio: str,
    telemetria: Telemetria | None = None,
    recurso: str = "",
    tentativa: int = 0,
) -> list[BaseTool]:
    """As cinco tools do mapeador, já ligadas à configuração desta execução.

    Com `telemetria`, cada chamada vira um `RegistroDeTool`. A instrumentação é
    opcional para que as tools continuem construtíveis isoladamente em teste, mas o
    pipeline sempre a liga: é dela que sai a resposta para "o grafo foi consultado
    antes de ler arquivo?".

    `estagio` chega como argumento em vez de constante do módulo porque ele é o
    rótulo da telemetria, ao lado de `recurso` e `tentativa` — e porque a
    alternativa, importar `ESTAGIO` de `mapeador.py`, seria o ciclo que este
    arquivo existe para desfazer.
    """
    grafo = Graphify(config)
    confinamento = fa.Confinamento(config.caminhos.backend)
    # Escopo por invocação de `criar_ferramentas`, que é por tentativa do estágio —
    # inclusive as voltas do mini-loop de schema, que são a mesma tentativa.
    contador = itertools.count(1)

    def observado(nome: str, funcao: Callable[..., str]) -> Callable[..., str]:
        """Envolve uma tool para medi-la sem tocar no que ela devolve ao modelo."""
        assinatura = inspect.signature(funcao)

        @functools.wraps(funcao)
        def envolvida(*posicionais: Any, **nomeados: Any) -> str:
            inicio = time.perf_counter()
            saida = funcao(*posicionais, **nomeados)
            if telemetria is not None:
                argumentos = assinatura.bind(*posicionais, **nomeados)
                argumentos.apply_defaults()
                telemetria.registrar_tool(
                    RegistroDeTool(
                        estagio=estagio,
                        recurso=recurso,
                        tentativa=tentativa,
                        ordem=next(contador),
                        nome=nome,
                        argumentos=dict(argumentos.arguments),
                        caracteres=len(saida),
                        duracao_s=time.perf_counter() - inicio,
                        # As tools engolem a exceção e devolvem "ERRO: ..." como texto
                        # normal, para o modelo poder se corrigir. Este é o único
                        # lugar onde a falha vira dado.
                        erro=saida.startswith("ERRO:"),
                    )
                )
            return saida

        return envolvida

    def _query(simbolo: str, budget: int | None = None) -> str:
        try:
            return grafo.query(simbolo, budget=budget)
        except ErroDeFerramenta as erro:
            return f"ERRO: {erro}"

    def _affected(entidade: str, depth: int = 1, relacao: str | None = None) -> str:
        try:
            return grafo.affected(entidade, depth=depth, relacao=relacao)
        except ErroDeFerramenta as erro:
            return f"ERRO: {erro}"

    def _ler(caminho: str, offset: int = 0, limit: int = 500) -> str:
        try:
            return fa.ler_arquivo(
                confinamento,
                caminho,
                offset,
                limit,
                max_bytes=config.execucao.max_bytes_arquivo,
            )
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    def _listar(caminho: str) -> str:
        try:
            return fa.listar_diretorio(confinamento, caminho)
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    def _buscar(padrao: str, glob: str | None = None) -> str:
        try:
            return fa.buscar(
                confinamento,
                padrao,
                glob,
                max_resultados=config.execucao.max_resultados_busca,
                max_bytes=config.execucao.max_bytes_arquivo,
            )
        except fa.CaminhoForaDaRaiz as erro:
            return f"ERRO: {erro}"

    return [
        StructuredTool.from_function(
            func=observado("graphify_query", _query),
            name="graphify_query",
            args_schema=ArgsGraphifyQuery,
            description=(
                "Pergunta ao grafo estrutural O QUE UM SÍMBOLO USA: devolve controller, "
                "entidade, service, DAO e getters com ARQUIVO E LINHA, a superclasse e os "
                "métodos sobrescritos. Consulte o grafo ANTES de procurar no backend. "
                "Quando o alvo herda de um controller abstrato, consulte o símbolo "
                "específico (a entidade ou o método concreto): partindo do controller a "
                "travessia sobe na superclasse e desce em todos os irmãos, gastando o "
                "orçamento com outros recursos. O grafo LOCALIZA; a fonte confirma."
            ),
        ),
        StructuredTool.from_function(
            func=observado("graphify_affected", _affected),
            name="graphify_affected",
            args_schema=ArgsGraphifyAffected,
            description=(
                "Pergunta inversa: QUEM USA este símbolo. Use antes de concluir que "
                "ninguém depende do recurso. 'No unique node match' significa NOME "
                "AMBÍGUO — reconsulte com o nome exato da classe; o vazio real tem outra "
                "frase ('No affected nodes found'). Confira no cabeçalho da resposta o "
                "nome que o Graphify resolveu: quando ele difere do pedido, os "
                "dependentes são de outra classe. Rode SEM `relacao` primeiro e leia os "
                'rótulos da saída antes de filtrar; `relacao="inherits"` (em Java) '
                "lista quem herda de um controller abstrato."
            ),
        ),
        StructuredTool.from_function(
            func=observado("ler_arquivo", _ler),
            name="ler_arquivo",
            args_schema=ArgsLerArquivo,
            description=(
                "Lê um trecho numerado de um arquivo do backend. É aqui que método, rota, "
                "campos e regras são confirmados — o grafo só aponta onde olhar."
            ),
        ),
        StructuredTool.from_function(
            func=observado("listar_diretorio", _listar),
            name="listar_diretorio",
            args_schema=ArgsListarDiretorio,
            description="Lista o conteúdo de um diretório do backend.",
        ),
        StructuredTool.from_function(
            func=observado("buscar_no_backend", _buscar),
            name="buscar_no_backend",
            args_schema=ArgsBuscar,
            description=(
                "Busca textual por expressão regular no backend. ÚLTIMO RECURSO, não "
                "primeiro passo: o que o grafo responde, pergunta-se ao grafo. Nunca "
                "busca dentro do graph.json (dezenas de MB)."
            ),
        ),
    ]
