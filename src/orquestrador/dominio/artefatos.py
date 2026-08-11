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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orquestrador.dominio.dossie import DossieDoRecurso
from orquestrador.dominio.inventario import Inventario
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.dominio.recurso import NomeDeRecurso

SUFIXO_SCHEMA = ".schema.json"


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
