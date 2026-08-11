"""O que cada estágio de LLM entrega, e onde cada arquivo pode cair.

`SaidaMapeador` e `SaidaExecutor` são os contratos de saída dos dois estágios —
a forma que o mini-loop de schema tenta alcançar antes de o gate sequer rodar.

Todo caminho aqui passa por `_caminho_confinado`. Não é redundância com o
`Confinamento` de `ferramentas/arquivos.py`: aquele resolve caminho real contra
uma raiz no disco; este recusa a **forma** (`..`, raiz absoluta, letra de
unidade) antes de o valor virar `Path`. Um dos dois sozinho deixa passar o
caminho que o outro pega."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import NomeDeRecurso

SUFIXO_SCHEMA = ".schema.json"
SUFIXO_SPEC = ".cy.js"

# O verbo de cada método. PUT e PATCH não compartilham verbo de propósito: os dois
# caem no mesmo recurso e o nome do arquivo colidiria — e "substituir" contra
# "alterar" é a diferença real entre eles, não um desempate inventado.
_VERBO_POR_METODO: dict[str, str] = {
    "POST": "criar",
    "PUT": "substituir",
    "PATCH": "alterar",
    "DELETE": "excluir",
    "HEAD": "consultar",
    "OPTIONS": "consultar",
}

# `{id}`, `:id` e `<id>` — as três grafias de parâmetro de rota que aparecem nos
# frameworks que o extrator lê.
_PARAMETRO = re.compile(r"^(\{.*\}|:.+|<.+>)$")
_NAO_SLUG = re.compile(r"[^a-z0-9]+")


def _caminho_confinado(valor: str, *, base: str) -> str:
    """Caminho relativo a `base`, com separador POSIX, sem `..` e sem raiz absoluta."""
    caminho = str(valor).strip().replace("\\", "/")
    if not caminho:
        raise ValueError("caminho vazio")
    if caminho.startswith("/") or re.match(r"^[A-Za-z]:", caminho):
        raise ValueError(f"caminho deve ser relativo {base}: {valor!r}")
    if ".." in caminho.split("/"):
        raise ValueError(f'caminho não pode conter "..": {valor!r}')
    return caminho


def caminho_de_schema(referencia: str, recurso: str) -> str:
    """Arquivo que um `schemaEntrada` do manifesto exige, relativo à raiz de schemas.

    Espelha `localizarArquivoDeSchema` de `scripts/cobertura/campos/schema.mjs` no
    layout canônico (`<recurso>/<nome>.schema.json`): referência já pontilhada pelo
    recurso resolve direto; nome simples desce para a pasta do recurso. O layout
    achatado com prefixo, que a skill ainda aceita como legado, não é emitido aqui.

    O ponteiro JSON opcional (`entidade#/properties/entity`) escolhe o nó dentro do
    arquivo, não o arquivo: ele sai antes da comparação.
    """
    nome = str(referencia).split("#", 1)[0].strip()
    if nome.endswith(SUFIXO_SCHEMA):
        nome = nome[: -len(SUFIXO_SCHEMA)]
    if not nome:
        raise ValueError(f"schemaEntrada vazio: {referencia!r}")
    if "/" in nome:
        return f"{nome}{SUFIXO_SCHEMA}"
    return f"{recurso}/{nome}{SUFIXO_SCHEMA}"


def _slug(valor: str) -> str:
    return _NAO_SLUG.sub("-", valor.lower()).strip("-") or "spec"


def nomes_dos_specs(endpoints: Sequence[str]) -> dict[str, str]:
    """O arquivo de spec de cada endpoint do recurso — um por operação.

    **Quem nomeia é o código, não o modelo**, e é essa decisão que sustenta o
    fatiamento: o filtro de uma fatia só consegue descartar o que caiu fora dela se
    souber de antemão qual arquivo aquela chamada podia escrever, e o reparo só
    acha o dono de uma violação se o nome for derivável do endpoint. Nome escolhido
    pelo modelo faria duas chamadas colidirem no mesmo caminho sem ninguém notar.

    O prefixo comum a todos os endpoints do recurso sai do nome — `/api/v1/` não
    distingue nada quando todos o têm —, o método vira verbo em português, e `GET`
    se divide em `listar` e `consultar` conforme a rota termine ou não em parâmetro.
    É essa divisão que faz alguém achar de primeira o arquivo que procura.

    Para endpoint de ação o nome sai feio (`criar-customers-activate.cy.js`), e é o
    preço de ser derivável. Quem carrega o título legível é o `describe` lá dentro.
    """
    lidos: list[tuple[str, str, list[str], bool]] = []
    for endpoint in endpoints:
        metodo, _, rota = endpoint.partition(" ")
        segmentos = [parte for parte in rota.split("/") if parte]
        estaticos = [parte for parte in segmentos if not _PARAMETRO.match(parte)]
        termina_em_parametro = bool(segmentos) and _PARAMETRO.match(segmentos[-1]) is not None
        lidos.append((endpoint, metodo.upper(), estaticos, termina_em_parametro))

    # O prefixo comum nunca engole o último segmento estático: sem essa guarda, um
    # recurso de endpoint único ficaria sem nenhum substantivo no nome.
    comum = 0
    if len(lidos) > 1 and all(estaticos for _, _, estaticos, _ in lidos):
        referencia = lidos[0][2]
        while comum < len(referencia) and all(
            comum < len(estaticos) - 1 and estaticos[comum] == referencia[comum]
            for _, _, estaticos, _ in lidos
        ):
            comum += 1

    nomes: dict[str, str] = {}
    for endpoint, metodo, estaticos, termina_em_parametro in lidos:
        if metodo == "GET":
            verbo = "consultar" if termina_em_parametro else "listar"
        else:
            verbo = _VERBO_POR_METODO.get(metodo, metodo.lower())
        restantes = estaticos[comum:] or estaticos[-1:] or [metodo.lower()]
        nomes[endpoint] = _slug("-".join([verbo, *restantes]))

    return {
        endpoint: f"{nome}{SUFIXO_SPEC}" for endpoint, nome in _desempatar(nomes, lidos).items()
    }


def _desempatar(
    nomes: dict[str, str], lidos: Sequence[tuple[str, str, list[str], bool]]
) -> dict[str, str]:
    """Garante nome único, primeiro pelo método e depois por índice.

    Colisão é rara (exige dois endpoints com o mesmo método e os mesmos segmentos
    estáticos), mas o custo dela não é: dois arquivos com o mesmo nome viram uma
    fatia sobrescrevendo a outra em silêncio. A ordem é a dos endpoints ordenados,
    e não a de chegada, para que o nome não dependa de como o plano foi montado.
    """
    metodo_de = {endpoint: metodo for endpoint, metodo, _, _ in lidos}
    for rodada in range(2):
        donos: dict[str, list[str]] = {}
        for endpoint, nome in nomes.items():
            donos.setdefault(nome, []).append(endpoint)
        colididos = {nome: sorted(lista) for nome, lista in donos.items() if len(lista) > 1}
        if not colididos:
            break
        for nome, lista in colididos.items():
            for indice, endpoint in enumerate(lista, start=1):
                sufixo = metodo_de[endpoint].lower() if rodada == 0 else str(indice)
                nomes[endpoint] = f"{nome}-{sufixo}"
    return nomes


class ArquivoSchema(BaseModel):
    """Um schema de entrada que o mapeador escreve na raiz de schemas do projeto.

    Espelha `ArquivoGerado`, com outra raiz: o schema mora fora do diretório do
    recurso (`cypress/fixtures/schemas/`), então é o mapeador que o emite — ele é
    quem lê o backend, e o denominador da cobertura por campo pertence ao plano, não
    à implementação que depois será medida por ele.
    """

    model_config = ConfigDict(extra="forbid")

    caminho: str
    conteudo: str

    @field_validator("caminho")
    @classmethod
    def _relativo_e_confinado(cls, valor: str) -> str:
        caminho = _caminho_confinado(valor, base="à raiz do diretório de schemas")
        if not caminho.endswith(SUFIXO_SCHEMA):
            raise ValueError(f'schema precisa terminar em "{SUFIXO_SCHEMA}": {valor!r}')
        return caminho


class FatiaDeSchemas(BaseModel):
    """A fatia de serialização dos schemas de entrada.

    Envelope, e não `list[ArquivoSchema]` solto, porque o mini-loop de schema
    valida contra um `BaseModel` — e porque a fatia precisa de um contrato próprio
    citável no prompt, com o mesmo nome que a telemetria usa.
    """

    model_config = ConfigDict(extra="forbid")

    schemas: list[ArquivoSchema] = Field(default_factory=list[ArquivoSchema])


class SaidaMapeador(BaseModel):
    """O que o Bloco 1 emite para um recurso."""

    model_config = ConfigDict(extra="forbid")

    inventario: Inventario
    manifesto: Manifesto
    schemas: list[ArquivoSchema] = Field(default_factory=list[ArquivoSchema])
    # Opcional no Pydantic e obrigatório no Gate A (QAORQ-063), de propósito: a
    # ausência precisa virar delta de reparo com o bundle inteiro à vista, não uma
    # falha de schema que devolveria só o fragmento reclamado.
    dossie: DossieDoRecurso | None = None

    @model_validator(mode="after")
    def _mesmo_recurso(self) -> SaidaMapeador:
        if self.inventario.recurso != self.manifesto.recurso:
            raise ValueError(
                "inventario.recurso e manifesto.recurso precisam ser o mesmo recurso: "
                f"{self.inventario.recurso!r} != {self.manifesto.recurso!r}"
            )
        if self.dossie is not None and self.dossie.recurso != self.manifesto.recurso:
            raise ValueError(
                "dossie.recurso precisa ser o mesmo recurso do manifesto: "
                f"{self.dossie.recurso!r} != {self.manifesto.recurso!r}"
            )
        return self

    @model_validator(mode="after")
    def _schemas_cobrem_o_declarado(self) -> SaidaMapeador:
        # Mesma lógica de `EndpointManifesto._canonico`: o Gate A já reprova o schema
        # ausente (QAAPI-027), mas recusar aqui transforma o desvio num delta de
        # schema — o reparo mais barato que existe — sem tirar do gate a autoridade
        # sobre o arquivo em disco.
        recurso = self.manifesto.recurso
        emitidos: set[str] = set()
        for arquivo in self.schemas:
            if not arquivo.caminho.startswith(f"{recurso}/"):
                raise ValueError(
                    f"schema fora do recurso {recurso!r}: {arquivo.caminho!r} "
                    f'(o layout é "{recurso}/<nome>{SUFIXO_SCHEMA}")'
                )
            if arquivo.caminho in emitidos:
                raise ValueError(f"schema repetido na saída: {arquivo.caminho}")
            emitidos.add(arquivo.caminho)

        for endpoint in self.manifesto.endpoints:
            if endpoint.schema_entrada is None:
                continue
            esperado = caminho_de_schema(endpoint.schema_entrada, recurso)
            if esperado not in emitidos:
                raise ValueError(
                    f"{endpoint.endpoint} declara schemaEntrada "
                    f"{endpoint.schema_entrada!r} mas o schema {esperado!r} não está "
                    'em "schemas". Emita o arquivo ou remova a declaração.'
                )
        return self


class ArquivoGerado(BaseModel):
    """Um arquivo que o executor escreve dentro do diretório do recurso."""

    model_config = ConfigDict(extra="forbid")

    caminho: str
    conteudo: str

    @field_validator("caminho")
    @classmethod
    def _relativo_e_confinado(cls, valor: str) -> str:
        return _caminho_confinado(valor, base="ao diretório do recurso")


class SaidaExecutor(BaseModel):
    """O que o Bloco 2 emite para um recurso."""

    model_config = ConfigDict(extra="forbid")

    recurso: NomeDeRecurso
    arquivos: list[ArquivoGerado] = Field(min_length=1)

    @model_validator(mode="after")
    def _sem_caminho_repetido(self) -> SaidaExecutor:
        vistos: set[str] = set()
        for arquivo in self.arquivos:
            if arquivo.caminho in vistos:
                raise ValueError(f"arquivo repetido na saída: {arquivo.caminho}")
            vistos.add(arquivo.caminho)
        return self
