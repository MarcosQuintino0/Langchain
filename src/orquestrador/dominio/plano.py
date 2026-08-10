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

from pydantic import BaseModel, ConfigDict, Field

from orquestrador.dominio.manifesto import Cat, Manifesto
from orquestrador.dominio.recurso import NomeDeRecurso


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

    def render(self) -> str:
        marca_campo = f" | campo: {self.campo}" if self.campo else ""
        return (
            f"- [{self.cat}] {self.nome}{marca_campo} | entrada: {self.entrada} "
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
