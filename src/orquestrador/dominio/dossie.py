"""O dossiê do recurso: o que o mapeador leu no backend além do gabarito.

O manifesto diz **que categorias** testar e os schemas dizem **que campos**
existem; este contrato carrega o resto do que a exploração encontrou e que hoje
morre com o histórico descartado do ReAct: as regras de negócio com efeito
observável por HTTP, o contrato de erro de cada endpoint, o comportamento dos
parâmetros de consulta e o registro do que foi procurado sem ser encontrado. É o
insumo que deixa o planejador afirmar "409 CUSTOMER_CODE_EXISTS" em vez de "4xx
funcional" — sem lhe dar tools, que é a fronteira dele.

Duas invariantes de forma sustentam a confiança no conteúdo:

* **Regra sem evidência não valida.** `arquivo:linha` é obrigatório, e recusar no
  Pydantic transforma a omissão num delta de schema — o reparo mais barato que
  existe. Se a evidência aponta código real não é pergunta deste módulo
  (`dominio/` não abre arquivo): quem confere é o verificador determinístico que
  o Gate A pluga.
* **Endpoint citado tem a forma canônica** do manifesto (`MÉTODO /rota`), senão
  a fatia por endpoint do planejador não encontra as regras do endpoint que está
  planejando.

Como todo módulo de `dominio/`, isto é contrato puro: valida forma e responde
perguntas sobre o próprio conteúdo. A completude — checklist negativa
respondida, endpoint citado existente no gabarito — é respondida por
`aspectos_nao_verificados` e `endpoints_desconhecidos`: funções, não gates, na
mesma família de `cenarios_faltantes` (o estágio repara a própria saída).
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from orquestrador.dominio.endpoint import exigir_endpoint_canonico
from orquestrador.dominio.recurso import NomeDeRecurso

EndpointCitado = Annotated[str, AfterValidator(exigir_endpoint_canonico)]

# A checklist negativa é fechada de propósito: em prosa livre, "não encontrei" e
# "não procurei" saem com a mesma cara, e a ausência de uma frase é indistinguível
# de esquecimento. Com lista fechada, o aspecto sem resposta vira item de
# `aspectos_nao_verificados` — cobrável por delta, como qualquer incompletude.
Aspecto = Literal[
    # campos calculados ou derivados na resposta (total, saldo, contagem)
    "campos-derivados",
    # máquina de estados: transições permitidas e proibidas de status
    "maquina-de-estados",
    # validação de um campo que depende do valor de outro
    "regras-condicionais-entre-campos",
    # criação/alteração aqui que grava ou altera registro de outro recurso
    "efeitos-colaterais-em-outros-recursos",
]
ASPECTOS_NEGATIVOS: tuple[Aspecto, ...] = get_args(Aspecto)


class Evidencia(BaseModel):
    """Onde a afirmação foi lida. Trecho de várias linhas cita a linha inicial."""

    model_config = ConfigDict(extra="forbid")

    arquivo: str = Field(min_length=1)
    linha: int | None = Field(default=None, ge=1)

    def render(self) -> str:
        return self.arquivo if self.linha is None else f"{self.arquivo}:{self.linha}"


class RegraDeNegocio(BaseModel):
    """Uma regra com efeito observável por HTTP — o que um teste consegue provar.

    `endpoints` vazio significa transversal: a regra vale para o recurso inteiro
    (um protocolo de versão via `If-Match`, por exemplo). Não há classe separada
    para "protocolo" de propósito — seria o mesmo shape com outro nome, e a fatia
    por endpoint já devolve as transversais junto com as específicas.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(pattern=r"^RN-\d{2,}$")
    resumo: str = Field(min_length=1)
    # O efeito é a parte testável: entrada → resposta/estado, nunca "o serviço
    # valida X" — isso descreve o código, não o comportamento na borda HTTP.
    efeito: str = Field(min_length=1)
    evidencias: list[Evidencia] = Field(min_length=1)
    endpoints: list[EndpointCitado] = Field(default_factory=list[str])
    codigos_de_erro: list[str] = Field(
        default_factory=list[str],
        validation_alias="codigosDeErro",
        serialization_alias="codigosDeErro",
    )

    @property
    def transversal(self) -> bool:
        return not self.endpoints

    def aplica_a(self, endpoint: str) -> bool:
        return self.transversal or endpoint in self.endpoints

    def render(self) -> str:
        linhas = [f"### {self.id} — {self.resumo}", f"- efeito: {self.efeito}"]
        if self.codigos_de_erro:
            linhas.append(f"- códigos de erro: {', '.join(self.codigos_de_erro)}")
        alvo = ", ".join(self.endpoints) if self.endpoints else "todos os endpoints do recurso"
        linhas.append(f"- endpoints: {alvo}")
        linhas.append(f"- evidência: {', '.join(e.render() for e in self.evidencias)}")
        return "\n".join(linhas)


class RespostaDeErro(BaseModel):
    """Um modo de falha conhecido: status, código do corpo e a condição."""

    model_config = ConfigDict(extra="forbid")

    status: int = Field(ge=400, le=599)
    # O código de erro do corpo (ex.: CUSTOMER_CODE_EXISTS); None quando o corpo
    # não carrega código — o cenário então afirma só o status.
    codigo: str | None = None
    quando: str = Field(min_length=1)

    def render(self) -> str:
        rotulo = f"{self.status} {self.codigo}" if self.codigo else str(self.status)
        return f"- {rotulo}: {self.quando}"


class ErrosDoEndpoint(BaseModel):
    """O contrato de erro de um endpoint: cada modo de falha lido na fonte."""

    model_config = ConfigDict(extra="forbid")

    endpoint: EndpointCitado
    respostas: list[RespostaDeErro] = Field(min_length=1)

    def render(self) -> str:
        return "\n".join([f"#### {self.endpoint}", *(r.render() for r in self.respostas)])


class ParametroDeConsulta(BaseModel):
    """Um parâmetro de query aceito de verdade, com o default quando ausente."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    nome: str = Field(min_length=1)
    comportamento: str = Field(min_length=1)
    erro_quando_invalido: str | None = Field(
        default=None,
        validation_alias="erroQuandoInvalido",
        serialization_alias="erroQuandoInvalido",
    )

    def render(self) -> str:
        linha = f"- {self.nome}: {self.comportamento}"
        if self.erro_quando_invalido:
            linha += f" | inválido: {self.erro_quando_invalido}"
        return linha


class ConsultaDoEndpoint(BaseModel):
    """A superfície de consulta de um endpoint de listagem.

    `parametro_desconhecido` registra o que acontece com parâmetro fora da lista
    (ignorado, rejeitado). É a defesa contra o teste que filtra por um parâmetro
    imaginário e "passa" verificando nada — a lista aqui é exaustiva por contrato.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    endpoint: EndpointCitado
    parametros: list[ParametroDeConsulta] = Field(min_length=1)
    parametro_desconhecido: str | None = Field(
        default=None,
        validation_alias="parametroDesconhecido",
        serialization_alias="parametroDesconhecido",
    )

    def render(self) -> str:
        linhas = [f"#### {self.endpoint}", *(p.render() for p in self.parametros)]
        fecho = "- não há outro parâmetro além dos listados"
        if self.parametro_desconhecido:
            fecho += f"; parâmetro desconhecido: {self.parametro_desconhecido}"
        linhas.append(fecho)
        return "\n".join(linhas)


class Incerteza(BaseModel):
    """O que foi procurado e não pôde ser determinado pela fonte.

    Registrar é o que autoriza o planejador a caracterizar o resultado em vez de
    afirmar um código — testar o que se sabe em vez de chutar o que não se sabe.
    """

    model_config = ConfigDict(extra="forbid")

    descricao: str = Field(min_length=1)
    endpoints: list[EndpointCitado] = Field(default_factory=list[str])

    def aplica_a(self, endpoint: str) -> bool:
        return not self.endpoints or endpoint in self.endpoints

    def render(self) -> str:
        sufixo = f" (endpoints: {', '.join(self.endpoints)})" if self.endpoints else ""
        return f"- {self.descricao}{sufixo}"


class VerificacaoNegativa(BaseModel):
    """A resposta de um item da checklist: o que foi vasculhado e o que se achou."""

    model_config = ConfigDict(extra="forbid")

    aspecto: Aspecto
    resultado: str = Field(min_length=1)

    def render(self) -> str:
        return f"- {self.aspecto}: {self.resultado}"


class DossieDoRecurso(BaseModel):
    """O dossiê inteiro de um recurso, com as fatias que cada consumidor recebe.

    O render completo vira `dossie.md` no diretório da execução (revisão humana,
    como `plano.md`); `render_para_endpoint` é a fatia de uma chamada do
    planejador — só o que vale para aquele endpoint, porque é o filtro que paga o
    custo do dossiê sem reenviá-lo inteiro N vezes.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    recurso: NomeDeRecurso
    regras: list[RegraDeNegocio] = Field(default_factory=list[RegraDeNegocio])
    erros: list[ErrosDoEndpoint] = Field(default_factory=list[ErrosDoEndpoint])
    consultas: list[ConsultaDoEndpoint] = Field(default_factory=list[ConsultaDoEndpoint])
    incertezas: list[Incerteza] = Field(default_factory=list[Incerteza])
    verificacoes_negativas: list[VerificacaoNegativa] = Field(
        default_factory=list[VerificacaoNegativa],
        validation_alias="verificacoesNegativas",
        serialization_alias="verificacoesNegativas",
    )

    @model_validator(mode="after")
    def _sem_repeticao(self) -> DossieDoRecurso:
        ids = [regra.id for regra in self.regras]
        if len(set(ids)) != len(ids):
            raise ValueError("id de regra repetido no dossiê")
        for rotulo, itens in (
            ("erros", [e.endpoint for e in self.erros]),
            ("consultas", [c.endpoint for c in self.consultas]),
        ):
            if len(set(itens)) != len(itens):
                raise ValueError(f"endpoint repetido em `{rotulo}`")
        aspectos = [v.aspecto for v in self.verificacoes_negativas]
        if len(set(aspectos)) != len(aspectos):
            raise ValueError("aspecto repetido em `verificacoesNegativas`")
        return self

    # -- fatias -------------------------------------------------------------

    def regras_do_endpoint(self, endpoint: str) -> list[RegraDeNegocio]:
        """As regras que valem para o endpoint: as dele e as transversais."""
        return [regra for regra in self.regras if regra.aplica_a(endpoint)]

    @property
    def regras_transversais(self) -> list[RegraDeNegocio]:
        return [regra for regra in self.regras if regra.transversal]

    def regras_por_id(self, ids: list[str]) -> list[RegraDeNegocio]:
        """As regras pedidas, na ordem do dossiê. Id desconhecido é ignorado em
        silêncio: quem valida citação de regra é o consumidor do plano, não este
        acessor — e uma fatia do executor com uma regra a menos ainda é útil."""
        pedidos = set(ids)
        return [regra for regra in self.regras if regra.id in pedidos]

    def erros_do_endpoint(self, endpoint: str) -> ErrosDoEndpoint | None:
        for item in self.erros:
            if item.endpoint == endpoint:
                return item
        return None

    def consulta_do_endpoint(self, endpoint: str) -> ConsultaDoEndpoint | None:
        for item in self.consultas:
            if item.endpoint == endpoint:
                return item
        return None

    # -- renders ------------------------------------------------------------

    def render(self) -> str:
        return self._render(self.regras, self.erros, self.consultas, self.incertezas)

    def render_para_endpoint(self, endpoint: str) -> str:
        """A fatia de uma chamada do planejador. Vazia vira `""` — o chamador omite a seção.

        A checklist negativa entra inteira: "não há máquina de estados" é
        informação de recurso, mas é exatamente o que impede o planejador de
        inventar transição de status em qualquer endpoint.
        """
        erros = self.erros_do_endpoint(endpoint)
        consulta = self.consulta_do_endpoint(endpoint)
        return self._render(
            self.regras_do_endpoint(endpoint),
            [erros] if erros else [],
            [consulta] if consulta else [],
            [incerteza for incerteza in self.incertezas if incerteza.aplica_a(endpoint)],
        )

    def _render(
        self,
        regras: list[RegraDeNegocio],
        erros: list[ErrosDoEndpoint],
        consultas: list[ConsultaDoEndpoint],
        incertezas: list[Incerteza],
    ) -> str:
        secoes: list[str] = []
        if regras:
            secoes.append("## Regras de negócio\n\n" + "\n\n".join(r.render() for r in regras))
        if erros:
            secoes.append("## Contrato de erro\n\n" + "\n\n".join(e.render() for e in erros))
        if consultas:
            secoes.append(
                "## Parâmetros de consulta\n\n" + "\n\n".join(c.render() for c in consultas)
            )
        if incertezas:
            secoes.append(
                "## Incertezas — caracterizar o resultado, não afirmar um código\n\n"
                + "\n".join(i.render() for i in incertezas)
            )
        if self.verificacoes_negativas:
            secoes.append(
                "## Procurado e não encontrado\n\n"
                + "\n".join(v.render() for v in self.verificacoes_negativas)
            )
        return "\n\n".join(secoes)


def aspectos_nao_verificados(dossie: DossieDoRecurso) -> list[str]:
    """Itens da checklist negativa que o dossiê não respondeu.

    Na ordem canônica de `ASPECTOS_NEGATIVOS`, porque a lista vira mensagens de
    delta e texto que muda de ordem entre tentativas custa cache e diff de log.
    """
    respondidos = {item.aspecto for item in dossie.verificacoes_negativas}
    return [aspecto for aspecto in ASPECTOS_NEGATIVOS if aspecto not in respondidos]


def endpoints_desconhecidos(dossie: DossieDoRecurso, conhecidos: list[str]) -> list[str]:
    """Endpoints citados pelo dossiê que não estão na lista canônica do gabarito.

    Citar rota imaginária é o jeito mais barato de o dossiê mentir, e nenhum
    consumidor perceberia: a fatia por endpoint simplesmente nunca a entregaria.
    A direção oposta (endpoint do gabarito que o dossiê não cita) não reprova —
    recurso sem regra de negócio existe; regra sobre rota inexistente, não.
    """
    reconhecidos = set(conhecidos)
    citados: dict[str, None] = {}
    for regra in dossie.regras:
        for endpoint in regra.endpoints:
            citados.setdefault(endpoint, None)
    for erros in dossie.erros:
        citados.setdefault(erros.endpoint, None)
    for consulta in dossie.consultas:
        citados.setdefault(consulta.endpoint, None)
    for incerteza in dossie.incertezas:
        for endpoint in incerteza.endpoints:
            citados.setdefault(endpoint, None)
    return [endpoint for endpoint in citados if endpoint not in reconhecidos]
