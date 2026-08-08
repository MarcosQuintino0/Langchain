"""O que o backend expõe, segundo quem leu o código.

O lado esquerdo do diff do Gate A. `RotaDinamica` é o que o extrator **não**
conseguiu resolver, e existe para que incerteza não vire ausência: rota que
ninguém sabe qual é não gera cobrança nem some do relatório."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orquestrador.dominio.endpoint import METODOS_HTTP, normalizar_endpoint
from orquestrador.dominio.recurso import NomeDeRecurso


class Endpoint(BaseModel):
    """Endpoint descoberto no backend, com a evidência que o sustenta."""

    model_config = ConfigDict(extra="forbid")

    metodo: str
    rota: str
    handler: str
    arquivo: str
    linha: int | None = None

    @field_validator("metodo")
    @classmethod
    def _metodo_conhecido(cls, valor: str) -> str:
        normalizado = str(valor).strip().upper()
        if normalizado not in METODOS_HTTP:
            raise ValueError(
                f"método HTTP inválido: {valor!r} (use um de {', '.join(METODOS_HTTP)})"
            )
        return normalizado

    @field_validator("rota")
    @classmethod
    def _rota_completa(cls, valor: str) -> str:
        rota = str(valor).strip()
        if not rota.startswith("/"):
            raise ValueError(f"rota deve ser o caminho completo começando em '/': {valor!r}")
        return rota

    @property
    def canonico(self) -> str:
        """Chave de cruzamento com o manifesto: 'MÉTODO /rota'."""
        return normalizar_endpoint(f"{self.metodo} {self.rota}")


class RotaDinamica(BaseModel):
    """Rota que a descoberta não conseguiu resolver estaticamente.

    Existe para que "não resolvi" seja um registro explícito, e não um endpoint
    que simplesmente sumiu do inventário.
    """

    model_config = ConfigDict(extra="forbid")

    expressao: str
    arquivo: str
    linha: int | None = None
    motivo: str


class Inventario(BaseModel):
    """Artefato NOVO (não existe hoje na skill): o que a descoberta enxergou.

    É a entrada do diff grafo × manifesto do Gate A — hoje um stub.
    """

    model_config = ConfigDict(extra="forbid")

    recurso: NomeDeRecurso
    endpoints: list[Endpoint] = Field(min_length=1)
    rotas_dinamicas_nao_resolvidas: list[RotaDinamica] = Field(default_factory=list[RotaDinamica])

    @model_validator(mode="after")
    def _sem_endpoint_duplicado(self) -> Inventario:
        vistos: set[str] = set()
        for endpoint in self.endpoints:
            if endpoint.canonico in vistos:
                raise ValueError(f"endpoint duplicado no inventário: {endpoint.canonico}")
            vistos.add(endpoint.canonico)
        return self

    def para_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
