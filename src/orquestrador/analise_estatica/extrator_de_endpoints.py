"""O denominador da cobertura: que endpoints o backend realmente expõe.

O manifesto responde "o que planejei testar". Este módulo responde a outra
pergunta, a que ninguém respondia: **"o que existe"**. Sem ela o Gate A só
consegue provar que o plano foi cumprido — nunca que o plano cobria o backend —,
e é exatamente essa lacuna que deixa passar um modelo que amostra em vez de
enumerar.

Como o endpoint é encontrado
----------------------------
Em dois passos, porque a informação está em dois lugares:

1. **O `graph.json` dá a lista de arquivos.** Ele é a saída do Graphify, gerada
   pelo Bloco 0 a partir do backend, e é o único inventário de arquivos que já
   respeita os `excludes` da indexação. Varrer o diretório do backend por conta
   própria produziria um segundo critério de "o que faz parte do projeto",
   divergente do primeiro no dia em que um dos dois mudasse.
2. **O adaptador da linguagem lê a rota no fonte.** Ela não está no grafo: para
   Java, o extrator AST do Graphify emite nó de arquivo, de classe e de método,
   com arquivo e linha, e nenhuma informação de HTTP. Verbo e caminho vivem em
   anotação, e anotação não vira nó.

A matriz de suporte é declaração, não descoberta
------------------------------------------------
`MATRIZ_DE_SUPORTE` lista, por extensão, qual adaptador sabe ler o quê. O que não
estiver ali **não é analisado, e isso é dito** — em `extensoes_ignoradas`. A
tentação óbvia é uma heurística que procure `"/api/"` em qualquer arquivo e
adivinhe o resto; ela é o defeito de origem outra vez, com outra roupa: um
extrator que erra em silêncio produz um denominador plausível e errado, e um
denominador errado aprova cobertura incompleta com a mesma cara de quem aprova
cobertura completa.

Hoje a matriz tem uma linha: Java + Spring MVC. Ela foi conferida contra um
backend Spring real de 27 endpoints em cinco controladores, e contra o
`openapi.yaml` daquele projeto — que bate 1:1 com os controladores, e que este
código de propósito **não** consome: a maioria dos projetos não tem um.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from orquestrador.analise_estatica.rotas_java_spring import extrair_controladores
from orquestrador.dominio.endpoint import normalizar_endpoint
from orquestrador.dominio.inventario import Endpoint, RotaDinamica
from orquestrador.excecoes import GrafoNaoPreparado
from orquestrador.ferramentas.arquivos import CaminhoForaDaRaiz, Confinamento

__all__ = [
    "MATRIZ_DE_SUPORTE",
    "Adaptador",
    "ClasseComRotas",
    "EndpointsDoBackend",
    "arquivos_do_grafo",
    "chave_de_endpoint",
    "extrair",
    "matriz_em_texto",
]


def chave_de_endpoint(canonico: str) -> str:
    """Forma de comparação: `GET /produtos/{id}` e `GET /produtos/{produtoId}` casam.

    O nome da variável de caminho é escolha de quem escreveu o controlador e não
    muda que requisição o teste faz. Compará-lo transformaria "o autor chamou de
    `productId`" em "endpoint inexistente" — uma reprovação que o modelo conserta
    renomeando, sem que cobertura nenhuma mude. O texto exibido nas mensagens
    continua sendo o do backend, para o delta ser copiável.
    """
    metodo, _, rota = normalizar_endpoint(canonico).partition(" ")
    segmentos = [
        "{}" if segmento.startswith("{") and segmento.endswith("}") else segmento
        for segmento in rota.split("/")
    ]
    return f"{metodo} {'/'.join(segmentos)}"


@dataclass(frozen=True)
class ClasseComRotas:
    """Uma classe controladora e o que ela expõe, com a evidência de onde saiu."""

    classe: str
    arquivo: str
    linguagem: str
    framework: str
    endpoints: tuple[Endpoint, ...] = ()
    nao_resolvidas: tuple[RotaDinamica, ...] = ()


@dataclass(frozen=True)
class Adaptador:
    """Um leitor de rotas para um par (linguagem, framework).

    `marcadores` é filtro barato, não decisão: um `.java` sem `@RestController` no
    texto não pode declarar rota Spring, e pular o parser nesses arquivos evita
    percorrer o backend inteiro. Nenhum arquivo é descartado por *não* casar um
    marcador sem antes ter a extensão reconhecida — descarte por extensão é o que
    a matriz declara, e é o que aparece no relatório.
    """

    linguagem: str
    framework: str
    extensoes: tuple[str, ...]
    marcadores: tuple[str, ...]
    ler: Callable[[str, str], list[ClasseComRotas]]
    # Prefixos de caminho que a convenção da linguagem reserva a código de teste.
    # Não é heurística: `src/test/java` é o layout padrão do Maven e do Gradle, e um
    # `@RestController` declarado ali sobe só dentro de um `MockMvc`. Contá-lo
    # inflaria o denominador com rota que nenhuma suíte de API alcança.
    raizes_de_teste: tuple[str, ...] = ()


def _adaptar_spring(texto: str, arquivo: str) -> list[ClasseComRotas]:
    classes: list[ClasseComRotas] = []
    for controlador in extrair_controladores(texto):
        if not controlador.rotas and not controlador.nao_resolvidas:
            continue
        classes.append(
            ClasseComRotas(
                classe=controlador.classe,
                arquivo=arquivo,
                linguagem="Java",
                framework="Spring MVC",
                endpoints=tuple(
                    Endpoint(
                        metodo=rota.metodo,
                        rota=rota.rota,
                        handler=rota.handler,
                        arquivo=arquivo,
                        linha=rota.linha,
                    )
                    for rota in controlador.rotas
                ),
                nao_resolvidas=tuple(
                    RotaDinamica(
                        expressao=incerteza.expressao,
                        arquivo=arquivo,
                        linha=incerteza.linha,
                        motivo=incerteza.motivo,
                    )
                    for incerteza in controlador.nao_resolvidas
                ),
            )
        )
    return classes


MATRIZ_DE_SUPORTE: tuple[Adaptador, ...] = (
    Adaptador(
        linguagem="Java",
        framework="Spring MVC / Spring Boot",
        extensoes=(".java",),
        marcadores=("@RestController", "@Controller"),
        ler=_adaptar_spring,
        raizes_de_teste=("src/test/", "src/integrationTest/", "src/testFixtures/"),
    ),
)


def matriz_em_texto() -> str:
    """A matriz numa linha por adaptador, para entrar em mensagem de erro."""
    return "; ".join(
        f"{adaptador.linguagem}/{adaptador.framework} ({', '.join(adaptador.extensoes)})"
        for adaptador in MATRIZ_DE_SUPORTE
    )


def _adaptador_de(arquivo: str) -> Adaptador | None:
    sufixo = Path(arquivo).suffix.lower()
    for adaptador in MATRIZ_DE_SUPORTE:
        if sufixo in adaptador.extensoes:
            return adaptador
    return None


@dataclass(frozen=True)
class EndpointsDoBackend:
    """O que a extração viu — e, com igual destaque, o que ela não viu."""

    classes: tuple[ClasseComRotas, ...] = ()
    arquivos_no_grafo: int = 0
    arquivos_analisados: int = 0
    arquivos_de_teste: int = 0
    arquivos_ausentes: tuple[str, ...] = ()
    extensoes_ignoradas: tuple[tuple[str, int], ...] = ()

    @property
    def endpoints(self) -> list[Endpoint]:
        return [endpoint for classe in self.classes for endpoint in classe.endpoints]

    @property
    def nao_resolvidas(self) -> list[RotaDinamica]:
        return [rota for classe in self.classes for rota in classe.nao_resolvidas]

    def classe_de(self, chave: str) -> ClasseComRotas | None:
        """Qual controlador declara o endpoint desta chave."""
        for classe in self.classes:
            if any(chave_de_endpoint(item.canonico) == chave for item in classe.endpoints):
                return classe
        return None

    def resumo_do_ignorado(self) -> str:
        if not self.extensoes_ignoradas:
            return "nenhuma extensão fora da matriz"
        return ", ".join(
            f"{extensao or '(sem extensão)'}×{quantidade}"
            for extensao, quantidade in self.extensoes_ignoradas
        )


def arquivos_do_grafo(graph: Path) -> list[str]:
    """Caminhos de fonte que o `graph.json` declara, relativos à raiz do backend.

    Duas formas de nó convivem no projeto e as duas são lidas: a do Graphify real
    (`source_file`) e a da fixture mínima do `--dry-run` (`file`). Aceitar as duas
    custa uma linha e evita que o gate só funcione em produção — que é onde ele
    nunca poderia ser depurado.

    O arquivo é carregado inteiro, e não varrido por texto: o `graph.json` de um
    backend grande chega a dezenas de MB, e casar rota com expressão regular
    dentro dele é justamente o palpite que este pacote recusa. O que se extrai
    daqui é a lista de arquivos; a rota sai do fonte.
    """
    if not graph.is_file():
        raise GrafoNaoPreparado(
            f"graph.json não encontrado em {graph}. O diff grafo × manifesto não tem "
            "denominador sem ele. Rode o Bloco 0 (qa-reindex) antes do mapeador."
        )
    try:
        # `Any` explícito, e não inferido: o conteúdo é JSON de ferramenta externa, e
        # cada acesso abaixo é validado por `isinstance` antes de ser usado.
        dados: Any = json.loads(graph.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as erro:
        raise GrafoNaoPreparado(
            f"graph.json ilegível em {graph}: {erro}. Regenere o mapa com o Bloco 0."
        ) from erro

    nos: Any = cast(dict[str, Any], dados).get("nodes") if isinstance(dados, dict) else None
    if not isinstance(nos, list):
        raise GrafoNaoPreparado(
            f"graph.json em {graph} não tem a lista `nodes`. O formato mudou ou o "
            "arquivo é de outra ferramenta; o diff grafo × manifesto não pode "
            "prosseguir sobre um mapa que não sabe ler."
        )

    caminhos: set[str] = set()
    for no in cast(list[Any], nos):
        if not isinstance(no, dict):
            continue
        campos = cast(dict[str, Any], no)
        bruto: Any = campos.get("source_file") or campos.get("file")
        if isinstance(bruto, str) and bruto.strip():
            caminhos.add(bruto.strip().replace("\\", "/"))
    return sorted(caminhos)


def extrair(*, graph: Path, backend: Path) -> EndpointsDoBackend:
    """Lê o grafo, aplica a matriz e devolve os endpoints com o que ficou de fora."""
    caminhos = arquivos_do_grafo(graph)
    confinamento = Confinamento(backend)

    classes: list[ClasseComRotas] = []
    ausentes: list[str] = []
    ignoradas: Counter[str] = Counter()
    analisados = 0
    de_teste = 0

    for relativo in caminhos:
        adaptador = _adaptador_de(relativo)
        if adaptador is None:
            ignoradas[Path(relativo).suffix.lower()] += 1
            continue
        if any(relativo.startswith(raiz) for raiz in adaptador.raizes_de_teste):
            de_teste += 1
            continue
        try:
            # O caminho vem de um JSON gerado por ferramenta externa; confinar é a
            # mesma precaução que vale para caminho vindo do modelo.
            alvo = confinamento.resolver(relativo)
        except CaminhoForaDaRaiz:
            ausentes.append(relativo)
            continue
        if not alvo.is_file():
            ausentes.append(relativo)
            continue

        analisados += 1
        texto = alvo.read_text(encoding="utf-8", errors="replace")
        if adaptador.marcadores and not any(marca in texto for marca in adaptador.marcadores):
            continue
        classes.extend(adaptador.ler(texto, relativo))

    return EndpointsDoBackend(
        classes=tuple(sorted(classes, key=lambda classe: (classe.arquivo, classe.classe))),
        arquivos_no_grafo=len(caminhos),
        arquivos_analisados=analisados,
        arquivos_de_teste=de_teste,
        arquivos_ausentes=tuple(sorted(ausentes)),
        extensoes_ignoradas=tuple(sorted(ignoradas.items())),
    )
