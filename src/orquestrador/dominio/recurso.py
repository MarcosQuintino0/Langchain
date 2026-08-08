"""A unidade de trabalho do pipeline, e o nome que vira diretório.

O princípio 3 diz que os agentes são stateless entre unidades de trabalho, e a
unidade é o `Recurso`. Ele carrega os caminhos que o estágio precisa e nada mais.

`NomeDeRecurso` é tipo, não `str`, porque o nome vira diretório por concatenação
em pelo menos três lugares. Validar em cada um deles seria três chances de
esquecer; validar no tipo é uma."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

# O nome do recurso não é rótulo: ele vira diretório por concatenação
# (`cypress/e2e/apis/<nome>`, `<raiz_schemas>/<nome>/x.schema.json`), então tudo o
# que um caminho aceita, ele aceitaria — inclusive `..`, `C:`, separador e nome de
# dispositivo. Restringir aqui é a única defesa que vale, porque cada consumidor
# concatena por conta própria.
_SLUG_DE_RECURSO = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Abrir `CON`, `NUL` ou `COM1` no Windows não abre arquivo nenhum: o Win32 desvia
# para o dispositivo, com ou sem extensão e sem diferenciar caixa. O erro que sai
# disso não menciona recurso, diretório nem orquestrador.
DISPOSITIVOS_RESERVADOS_DO_WINDOWS: frozenset[str] = frozenset(
    (
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{indice}" for indice in range(1, 10)),
        *(f"LPT{indice}" for indice in range(1, 10)),
    )
)


def validar_nome_de_recurso(valor: str) -> str:
    """Aceita só o slug que pode virar diretório sem surpresa em nenhum sistema."""
    nome = str(valor)
    if not _SLUG_DE_RECURSO.match(nome):
        raise ValueError(
            f"nome de recurso inválido: {valor!r}. Use minúsculas, dígitos, ponto, "
            'hífen ou sublinhado, começando por letra ou dígito (ex.: "pedidos", '
            '"nota-fiscal", "v2.pedidos").'
        )
    if nome.endswith("."):
        # O Windows descarta o ponto final ao abrir o caminho, então `pedidos.` e
        # `pedidos` seriam o mesmo diretório com dois nomes — e os artefatos de um
        # recurso apareceriam no do outro.
        raise ValueError(f"nome de recurso não pode terminar em ponto: {valor!r}")
    if nome.split(".", 1)[0].upper() in DISPOSITIVOS_RESERVADOS_DO_WINDOWS:
        raise ValueError(
            f"{valor!r} é dispositivo reservado do Windows: "
            f"{', '.join(sorted(DISPOSITIVOS_RESERVADOS_DO_WINDOWS))} não viram "
            "diretório, com ou sem extensão."
        )
    return nome


NomeDeRecurso = Annotated[str, AfterValidator(validar_nome_de_recurso)]


class Recurso(BaseModel):
    """Unidade de trabalho do pipeline: um recurso por vez, sem histórico entre eles."""

    nome: NomeDeRecurso
    caminho_testes: Path
    # Raiz do diretório de schemas do projeto de testes, vinda da configuração
    # (`[caminhos].dir_schemas`) como `caminho_testes`. Não é descoberta aqui: quem
    # sobe do recurso procurando `cypress/fixtures/schemas` é a skill
    # (`campos/schema.mjs`), e repetir a busca criaria uma segunda fonte de verdade
    # para o mesmo diretório.
    raiz_schemas: Path | None = None
    caminhos_backend: list[Path] = Field(default_factory=list[Path])

    @property
    def manifesto_path(self) -> Path:
        return self.caminho_testes / "_support" / "cobertura.json"

    @property
    def caminho_schemas(self) -> Path:
        """Raiz onde os schemas do mapeador são gravados (`<raiz>/<recurso>/x.schema.json`).

        Sem `raiz_schemas` não há palpite razoável: gravar no diretório errado é pior
        que falhar, porque o Gate A continuaria reprovando com QAAPI-027 enquanto o
        arquivo estaria em disco, parecendo entregue.
        """
        if self.raiz_schemas is None:
            raise ValueError(
                f"recurso {self.nome!r} sem raiz de schemas. Informe `raiz_schemas` ao "
                "construir o Recurso (a CLI a preenche de [caminhos].dir_schemas)."
            )
        return self.raiz_schemas
