"""O veredito do auditor semântico, que fica fora do loop quente.

O auditor não é gate: ele não reprova tentativa e não entra em `delta.violacoes`
(princípio 5). O contrato existe antes da implementação porque é ele que impede
o stub de mentir — `veredito` é `"íntegro"` ou `"revisar"`, e não há terceiro
valor que signifique "não rodei"."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AchadoAuditoria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    cat: str | None = None
    arquivo: str | None = None
    linha: int | None = None
    motivo: str


class ResultadoAuditoria(BaseModel):
    """Interface do auditor. A implementação é Fase 2."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    nao_aplica_refutados: list[AchadoAuditoria] = Field(
        default_factory=list[AchadoAuditoria],
        validation_alias="naoAplica_refutados",
        serialization_alias="naoAplica_refutados",
    )
    oraculos_fracos: list[AchadoAuditoria] = Field(default_factory=list[AchadoAuditoria])
    veredito: Literal["íntegro", "revisar"]
