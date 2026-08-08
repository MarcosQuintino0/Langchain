"""O que o projeto de testes do consumidor já oferece ao executor.

O executor não pode inventar helper nem reimplementar o que já existe em
`cypress/support/api/`. Este contrato é o que ele recebe para saber o que
importar — com a declaração **verbatim**, porque assinatura parafraseada é
assinatura adivinhada."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field


class ExportCompartilhado(BaseModel):
    """Um export de um módulo compartilhado, com a declaração **verbatim**.

    `declaracao` é copiada do arquivo, nunca reconstruída: uma assinatura remontada
    por regex é um palpite bem-intencionado que pode divergir do real — e quem lê
    vai confiar nela.
    """

    model_config = ConfigDict(extra="forbid")

    nome: str
    declaracao: str
    comentario: str | None = None


class ModuloCompartilhado(BaseModel):
    """Um arquivo de `support/api/` (ou equivalente) do projeto de testes."""

    model_config = ConfigDict(extra="forbid")

    caminho: str
    # Os três caminhos de import que os layouts da skill produzem. São calculados,
    # não adivinhados: acertar a profundidade do `../../..` de cabeça é a fonte de
    # erro mais boba e mais provável.
    #   recurso     spec na raiz do recurso (layout plano)
    #   subdominio  spec dentro de uma subpasta de sub-domínio (recurso composto)
    #   support     módulo em `_support/`, que não desce junto com os sub-domínios
    import_do_recurso: str
    import_do_subdominio: str
    import_do_support: str
    exports: list[ExportCompartilhado] = Field(default_factory=list[ExportCompartilhado])


class SuperficieDoProjeto(BaseModel):
    """O que o projeto de testes oferece de pronto ao executor.

    Extraída deterministicamente, uma vez por execução (é do projeto, não do
    recurso), e entregue pela **instrução fixa** do estágio — nunca pela entrada da
    tentativa, que é reenviada a cada reparo.
    """

    model_config = ConfigDict(extra="forbid")

    raiz: str
    modulos: list[ModuloCompartilhado] = Field(default_factory=list[ModuloCompartilhado])

    @property
    def total_de_exports(self) -> int:
        return sum(len(modulo.exports) for modulo in self.modulos)

    def para_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"

    def render(self) -> str:
        """Texto compacto para colar na instrução do estágio."""
        if not self.modulos:
            return "(nenhum módulo compartilhado encontrado)"
        blocos: list[str] = []
        for modulo in self.modulos:
            linhas = [
                f"### `{modulo.caminho}`",
                "",
                f'- de `_support/`: `"{modulo.import_do_support}"`',
                f'- de um spec na raiz do recurso: `"{modulo.import_do_recurso}"`',
                f'- de um spec em subpasta de sub-domínio: `"{modulo.import_do_subdominio}"`',
                "",
            ]
            for exportado in modulo.exports:
                linhas.append("```js")
                if exportado.comentario:
                    linhas.append(exportado.comentario)
                linhas.append(exportado.declaracao)
                linhas.append("```")
            blocos.append("\n".join(linhas).rstrip())
        return "\n\n".join(blocos)
