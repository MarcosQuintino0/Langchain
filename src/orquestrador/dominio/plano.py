"""O plano de cenários: o gabarito expandido em casos de teste concretos.

O manifesto diz **que categorias** cada endpoint testa; este contrato diz **quais
casos** — nome, o que enviar, o que comprovar. É o elo que faltava entre os dois
estágios de LLM: sem ele, o executor recebia "teste limites no POST" e decidia
sozinho quantos e quais cenários, e era nesse decidir-sozinho que um modelo de
raciocínio gastava o orçamento inteiro planejando (medido: 65.536 tokens de
pensamento sem produzir um byte de resposta). Com o plano, o executor transcreve.

O plano também é o artefato que um humano revisa ANTES de existir código: cada
linha é legível ("criar com externalCode de 41 caracteres → rejeitar sem criar"),
e a pergunta "planejei e não entreguei?" passa a ter denominador por cenário.

Como todo módulo de `dominio/`, isto é contrato puro: valida forma e responde
perguntas sobre o próprio conteúdo. Quem confere o plano contra o manifesto é
`cenarios_faltantes` — função, não gate: a incompletude aqui é da mesma família
da saída que não valida contra o Pydantic (o estágio repara a própria saída),
não um veredito de qualidade sobre artefato publicado.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from orquestrador.dominio.manifesto import Cat, Manifesto
from orquestrador.dominio.recurso import NomeDeRecurso

# A partição das 12 categorias em grupos. Nasceu como arquitetura dos arquivos do
# executor (um spec por grupo) e hoje é **só** a unidade de pedido do planejador:
# planejar um grupo por chamada é o que mantém a resposta pedida pequena — medido
# em 2026-08-10, o endpoint de listagem inteiro numa chamada estourou o teto de
# saída do provedor (65.536 tokens, resposta cortada).
#
# O executor deixou de fatiar por aqui: o spec passou a ser por operação, e o
# arquivo de um cenário é decidido pelo ENDPOINT dele. As duas partições podiam
# divergir porque o plano já é indexado por endpoint — vários grupos caem no mesmo
# arquivo, e o destino de cada cenário continua determinístico. O que não pode
# mudar é isto: o grupo é uma decisão de tamanho de resposta, não de arquivo.
CATS_VALIDACOES: tuple[str, ...] = ("CAT-02", "CAT-03", "CAT-04", "CAT-05")
CATS_SEGURANCA: tuple[str, ...] = ("CAT-06", "CAT-08", "CAT-09")
CATS_CRUD: tuple[str, ...] = ("CAT-01", "CAT-07", "CAT-10", "CAT-11", "CAT-12")

# (chave curta para telemetria, cats do grupo) — a ordem é fixa: é a ordem das
# chamadas do planejador e da montagem do plano, e ordem estável é prefixo de
# cache e diff de log legível.
GRUPOS_DE_CATS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("crud", CATS_CRUD),
    ("validacoes", CATS_VALIDACOES),
    ("seguranca", CATS_SEGURANCA),
)


class Cenario(BaseModel):
    """Um caso de teste concreto: uma linha do plano, um `it` na suíte."""

    model_config = ConfigDict(extra="forbid")

    cat: Cat
    nome: str = Field(min_length=1)
    entrada: str = Field(min_length=1)
    espera: str = Field(min_length=1)
    # O campo do schema que o cenário cobre, quando houver um. É o elo com a
    # cobertura por campo da skill: o `it` transcrito leva a tag `@campo <nome>`,
    # e sem ela o QAAPI-025 acusa o campo como não testado — foi a reprovação
    # medida na primeira execução real desta arquitetura.
    campo: str | None = None
    # A regra do dossiê (`RN-xx`) que o cenário prova, quando houver uma. É o elo
    # na direção oposta ao `campo`: liga o caso à evidência de fonte, e é por ele
    # que a fatia do executor recebe junto o texto da regra que está transcrevendo.
    regra: str | None = None

    def render(self) -> str:
        marca_campo = f" | campo: {self.campo}" if self.campo else ""
        marca_regra = f" | regra: {self.regra}" if self.regra else ""
        return (
            f"- [{self.cat}] {self.nome}{marca_campo}{marca_regra} | entrada: {self.entrada} "
            f"| espera: {self.espera}"
        )


class PlanoDoEndpoint(BaseModel):
    """Os cenários de um endpoint — a unidade que o planejador emite por chamada.

    Uma chamada por endpoint, e não o plano inteiro numa resposta, porque o
    tamanho da resposta é o que separa pensamento saudável de espiral: o mesmo
    modelo que planejou 184 cenários em cinco respostas pequenas (raciocínio de
    2-6 mil tokens cada) travou ao planejar tudo numa só.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1)
    cenarios: list[Cenario] = Field(min_length=1)

    def cats_cobertas(self) -> set[str]:
        return {cenario.cat for cenario in self.cenarios}

    def regras_citadas(self) -> list[str]:
        """Ids de regra citados pelos cenários deste endpoint, na ordem do plano."""
        vistos: dict[str, None] = {}
        for cenario in self.cenarios:
            if cenario.regra:
                vistos.setdefault(cenario.regra, None)
        return list(vistos)

    def render(self) -> str:
        linhas = [f"### {self.endpoint}", ""]
        linhas += [cenario.render() for cenario in self.cenarios]
        return "\n".join(linhas)


class PlanoDeTestes(BaseModel):
    """O plano do recurso inteiro, montado das partes por endpoint."""

    model_config = ConfigDict(extra="forbid")

    recurso: NomeDeRecurso
    endpoints: list[PlanoDoEndpoint] = Field(min_length=1)

    def do_endpoint(self, endpoint: str) -> PlanoDoEndpoint | None:
        for parte in self.endpoints:
            if parte.endpoint == endpoint:
                return parte
        return None

    def endpoints_das_cats(self, cats: set[str]) -> list[str]:
        """Endpoints cujo plano tem ao menos um cenário de alguma dessas categorias.

        É o que traduz uma violação de cobertura ("falta CAT-04") no arquivo que
        deveria tê-la: com o spec por operação, quem sabe onde aquele teste mora é
        o plano, porque foi ele que decidiu qual endpoint cobre qual categoria.
        """
        return [parte.endpoint for parte in self.endpoints if parte.cats_cobertas() & cats]

    def cenarios_das_cats(self, cats: tuple[str, ...]) -> str:
        """As linhas do plano cujas categorias caem em `cats`, agrupadas por endpoint.

        É a fatia que o executor recebe: o spec de validações leva CAT-02..05, o de
        segurança leva CAT-06/08/09, e o de CRUD leva o resto. Renderizar aqui, no
        dono do formato, evita que cada consumidor invente uma projeção própria.
        """
        partes: list[str] = []
        for parte in self.endpoints:
            selecionados = [c for c in parte.cenarios if c.cat in cats]
            if not selecionados:
                continue
            partes.append(f"### {parte.endpoint}")
            partes += [cenario.render() for cenario in selecionados]
        return "\n".join(partes)

    def regras_citadas(self, cats: tuple[str, ...]) -> list[str]:
        """Ids de regra citados pelos cenários dessas categorias, na ordem do plano.

        É o outro lado de `cenarios_das_cats`: a fatia diz o que transcrever, e
        esta lista diz quais regras do dossiê precisam ir junto — só as citadas,
        porque reenviar o dossiê inteiro em cada fatia pagaria o custo N vezes.
        """
        vistos: dict[str, None] = {}
        for parte in self.endpoints:
            for cenario in parte.cenarios:
                if cenario.cat in cats and cenario.regra:
                    vistos.setdefault(cenario.regra, None)
        return list(vistos)

    def render(self) -> str:
        return "\n\n".join(parte.render() for parte in self.endpoints)

    def total_de_cenarios(self) -> int:
        return sum(len(parte.cenarios) for parte in self.endpoints)


def cenarios_faltantes(
    plano_do_endpoint: PlanoDoEndpoint, cats_do_gabarito: list[str]
) -> list[str]:
    """Categorias que o gabarito manda testar e o plano não cobriu.

    A conferência é por endpoint, na direção que importa: toda categoria em `cats`
    precisa de ao menos um cenário. A direção oposta (cenário de categoria fora de
    `cats`) não reprova — planejar a mais custa um teste; planejar a menos apaga
    cobertura sem deixar rastro. A mesma assimetria do resto do projeto.
    """
    cobertas = plano_do_endpoint.cats_cobertas()
    return [cat for cat in cats_do_gabarito if cat not in cobertas]


# Um método de escrita citado na entrada do cenário. É o gatilho da exigência de
# prova de estado: quem muda estado precisa prová-lo; quem só lê, não.
_ESCRITA_NA_ENTRADA = re.compile(r"\b(POST|PUT|PATCH|DELETE)\b")

# O vocabulário que conta como prova de estado no `espera`. Heurística assumida:
# falso negativo custa UMA volta de reparo; falso positivo não existe como risco
# de aprovação — quem aprova artefato continua sendo o gate, nunca esta lista.
_PROVA_DE_ESTADO = re.compile(
    r"releitura|rel(?:er|ê)|confirma|comprova|prova(?:ndo)?\b"
    r"|estado\s+(?:inalterado|intacto|preservado)|não\s+mud"
    r"|sem\s+(?:criação|alteração|persistência|efeito)"
    r"|não\s+(?:cria|criou|lista|grava|gravou|persiste|persistiu|altera|alterou|aparece)"
    r"|nenhum(?:a)?\s+(?:registro|efeito|criação|alteração)|permanece|continua",
    re.IGNORECASE,
)

# Acima disto, variações do mesmo campo na mesma categoria são repetição do mesmo
# defeito, não cobertura — o teto que o prompt pede e esta função cobra.
LIMITE_DE_VARIACOES_POR_CAMPO = 3


def cenarios_sem_prova_de_estado(parte: PlanoDoEndpoint) -> list[str]:
    """Nomes dos cenários de escrita cujo `espera` não prova o estado.

    Foi medido (2026-08-10): a proporção de cenários com releitura caiu de 55%
    para 25% numa mudança de prompt — sinal de que exigência que vive só em
    prosa flutua com a atenção do modelo. Esta função a torna cobrável na mesma
    moeda do QAORQ-050: o estágio repara a própria saída, uma volta.
    """
    return [
        cenario.nome
        for cenario in parte.cenarios
        if _ESCRITA_NA_ENTRADA.search(cenario.entrada)
        and not _PROVA_DE_ESTADO.search(cenario.espera)
    ]


def variacoes_excedentes(parte: PlanoDoEndpoint) -> list[str]:
    """Mensagens sobre (categoria, campo) com mais cenários que o limite.

    O freio do prompt ("um cenário por partição, nunca um por variação de dado")
    vira número aqui: acima de `LIMITE_DE_VARIACOES_POR_CAMPO` para o mesmo campo
    na mesma categoria é repetição, e repetição custa executor, Cypress e leitura
    humana sem comprar cobertura.
    """
    contagem: dict[tuple[str, str], int] = {}
    for cenario in parte.cenarios:
        if cenario.campo:
            chave = (cenario.cat, cenario.campo)
            contagem[chave] = contagem.get(chave, 0) + 1
    return [
        f"{cat} tem {quantidade} cenários para o campo {campo!r} "
        f"(limite {LIMITE_DE_VARIACOES_POR_CAMPO}): mantenha os mais reveladores"
        for (cat, campo), quantidade in sorted(contagem.items())
        if quantidade > LIMITE_DE_VARIACOES_POR_CAMPO
    ]


def conferir_plano(plano: PlanoDeTestes, manifesto: Manifesto) -> list[str]:
    """Defeitos de completude do plano inteiro, como mensagens prontas para delta.

    Lista vazia é aprovação. Endpoint do gabarito sem seção no plano é defeito na
    mesma moeda que categoria sem cenário: os dois apagam cobertura em silêncio.
    """
    defeitos: list[str] = []
    for item in manifesto.endpoints:
        parte = plano.do_endpoint(item.endpoint)
        if parte is None:
            defeitos.append(f"endpoint {item.endpoint!r} não tem seção no plano")
            continue
        for cat in cenarios_faltantes(parte, list(item.cats)):
            defeitos.append(
                f"{item.endpoint}: categoria {cat} está em `cats` no gabarito e não "
                "tem nenhum cenário no plano"
            )
    return defeitos
