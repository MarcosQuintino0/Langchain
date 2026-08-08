"""O gabarito de cobertura — espelho de `_support/cobertura.json`.

Fonte da forma: SKILL.md passo 6 e `scripts/cobertura/manifesto.mjs` da skill
(+ `estrutura.mjs`, `campos/`).

Fronteira Pydantic × gate determinístico
----------------------------------------
Aqui se valida apenas o que é **estrutural**: tipos, ids de categoria bem
formados, endpoint na forma canônica, ausência de campo desconhecido. A
**contabilidade das 12 categorias** — `cats` ∪ `naoAplica` cobrindo CAT-01..CAT-12
com interseção vazia, e a qualidade das justificativas — fica deliberadamente de
fora: quem reprova isso é o `validar-suite-gerada.mjs` (princípio 4, "quem reprova
é script"). Duplicar a regra aqui apagaria o Gate A do fluxo e criaria duas fontes
de verdade para a mesma invariante."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from orquestrador.dominio.endpoint import (
    METODOS_DE_ESCRITA,
    METODOS_HTTP,
    normalizar_endpoint,
)
from orquestrador.dominio.recurso import NomeDeRecurso

# As 12 categorias do catálogo. O *significado* de cada uma vive em
# references/catalogo-de-testes.md e é conteúdo de prompt (Fase 2); aqui só os ids.
CATS: tuple[str, ...] = tuple(f"CAT-{indice:02d}" for indice in range(1, 13))

Cat = Annotated[str, StringConstraints(pattern=r"^CAT-(0[1-9]|1[0-2])$")]


class SubDominio(BaseModel):
    """Sub-domínio declarado (SKILL.md passo 6, validado por estrutura.mjs)."""

    model_config = ConfigDict(extra="forbid")

    rotas: list[str] = Field(min_length=1)
    motivo: str


class EndpointManifesto(BaseModel):
    """Uma entrada de `endpoints` no `_support/cobertura.json`."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    endpoint: str
    cats: list[Cat] = Field(default_factory=list)
    # Sobre `validation_alias` + `serialization_alias` em vez de `alias`, ver a
    # docstring de `Violacao`.
    nao_aplica: dict[Cat, str] = Field(
        default_factory=dict, validation_alias="naoAplica", serialization_alias="naoAplica"
    )
    schema_entrada: str | None = Field(
        default=None, validation_alias="schemaEntrada", serialization_alias="schemaEntrada"
    )
    sem_corpo: str | None = Field(
        default=None, validation_alias="semCorpo", serialization_alias="semCorpo"
    )
    campos: dict[str, str | dict[str, str]] | None = None

    @field_validator("endpoint")
    @classmethod
    def _canonico(cls, valor: str) -> str:
        # QAAPI-024 reprova endpoint fora da forma canônica. Rejeitar aqui (em vez de
        # normalizar em silêncio) transforma o desvio num delta de schema — o reparo
        # mais barato que existe — sem tirar do gate a autoridade sobre o arquivo.
        canonico = normalizar_endpoint(valor)
        if valor != canonico:
            raise ValueError(f'endpoint fora da forma canônica "{canonico}": {valor!r}')
        partes = canonico.split(" ", 1)
        if len(partes) != 2 or partes[0] not in METODOS_HTTP or not partes[1].startswith("/"):
            raise ValueError(f'endpoint deve ter a forma "MÉTODO /rota/completa": {valor!r}')
        return canonico

    @field_validator("cats")
    @classmethod
    def _cats_sem_repeticao(cls, valor: list[str]) -> list[str]:
        if len(set(valor)) != len(valor):
            raise ValueError("categoria repetida em cats")
        return valor

    @property
    def metodo(self) -> str:
        return self.endpoint.split(" ", 1)[0]

    @property
    def eh_escrita(self) -> bool:
        return self.metodo in METODOS_DE_ESCRITA


class Manifesto(BaseModel):
    """O gabarito da cobertura de um recurso: `_support/cobertura.json`.

    Formato definido pela skill, não por este orquestrador. Alterações de forma
    devem sair de `manifesto.mjs` / `estrutura.mjs`, nunca daqui.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # O manifesto é formato da skill, mas este campo em particular volta para o disco
    # como diretório (`caminho_de_schema`), e quem o preenche é um LLM.
    recurso: NomeDeRecurso
    profundidade: Literal["completa", "contrato"] | None = None
    handler_compartilhado: str | None = Field(
        default=None,
        validation_alias="handlerCompartilhado",
        serialization_alias="handlerCompartilhado",
    )
    handler_coberto_por: str | None = Field(
        default=None, validation_alias="handlerCobertoPor", serialization_alias="handlerCobertoPor"
    )
    sub_dominios: dict[str, SubDominio] | None = Field(
        default=None, validation_alias="subDominios", serialization_alias="subDominios"
    )
    endpoints: list[EndpointManifesto] = Field(min_length=1)

    @model_validator(mode="after")
    def _endpoints_unicos(self) -> Manifesto:
        vistos: set[str] = set()
        for item in self.endpoints:
            if item.endpoint in vistos:
                raise ValueError(f"endpoint duplicado no manifesto: {item.endpoint}")
            vistos.add(item.endpoint)
        return self

    def para_json(self) -> str:
        """Serializa exatamente na forma que o `validar-suite-gerada.mjs` espera."""
        dados = self.model_dump(by_alias=True, exclude_none=True)
        return json.dumps(dados, ensure_ascii=False, indent=2) + "\n"

    def endpoints_canonicos(self) -> list[str]:
        return [item.endpoint for item in self.endpoints]
