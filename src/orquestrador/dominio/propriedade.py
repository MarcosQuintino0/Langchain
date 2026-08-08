"""De quem é cada arquivo tocado, e o que fazer quando o schema diverge.

O diário de propriedade é o que permite publicar de forma transacional sem
apagar trabalho de quem não é o orquestrador: sem ele, "sobrescrever" e "criar"
são indistinguíveis no momento em que dá errado.

`DivergenciaDeSchema` é o terceiro estado do Bloco 1 — o schema do cliente
declara menos campos do que o backend tem, o artefato está íntegro e publicá-lo é
o certo, mas o denominador da cobertura encolheu por um motivo que ninguém
conferiu."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Classificacao(StrEnum):
    """De quem é o arquivo que acabamos de tocar.

    É a distinção que faltava para poder apagar qualquer coisa com segurança.
    `criado` é o único estado em que a remoção é reversível no sentido que importa:
    se não existia antes de nós, apagá-lo devolve o projeto ao estado anterior.
    `modificado` já era do consumidor; `preexistente` nem chegamos a escrever.

    `criado` **atravessa execuções**: um spec que nasceu na execução de ontem e foi
    reescrito hoje continua sendo nosso. Sem essa propagação, a segunda execução o
    classificaria como `modificado` e o arquivo viraria intocável — e o produto
    perderia a capacidade de limpar a própria sujeira.
    """

    CRIADO = "criado"
    MODIFICADO = "modificado"
    PREEXISTENTE = "preexistente"


class EntradaDoDiario(BaseModel):
    """O que aconteceu com **um** arquivo do projeto do consumidor.

    `hash_anterior is None` significa "não havia arquivo", que é diferente de
    "arquivo vazio" — é essa diferença que separa `criado` de `modificado`, e
    representá-la por string vazia apagaria justamente o caso que autoriza a
    remoção.
    """

    model_config = ConfigDict(extra="forbid")

    destino: Path
    classificacao: Classificacao
    hash_anterior: str | None = None
    hash_novo: str | None = None
    recurso: str = ""
    execucao: str = ""


class DiarioDePropriedade(BaseModel):
    """O diário acumulado entre execuções, indexado pelo caminho de destino.

    Ele é a memória que permite responder "este arquivo é nosso?" numa execução que
    não criou o arquivo. Perdê-lo (apagar o diretório de saída, por exemplo) não
    corrompe nada: sem entrada, tudo vira `modificado` ou `preexistente` e nada é
    removido. A degradação é para o lado conservador, de propósito.
    """

    model_config = ConfigDict(extra="forbid")

    versao: Literal[1] = 1
    entradas: list[EntradaDoDiario] = Field(default_factory=list[EntradaDoDiario])

    def para_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"

    def por_destino(self) -> dict[Path, EntradaDoDiario]:
        return {entrada.destino: entrada for entrada in self.entradas}

    def substituir(
        self, novas: list[EntradaDoDiario], *, esquecer: set[Path] | None = None
    ) -> DiarioDePropriedade:
        """Cópia com `novas` sobrescrevendo as entradas de mesmo destino.

        `esquecer` sai do diário: é para o arquivo que **deixou de existir** porque
        nós o removemos. Mantê-lo transformaria o diário num cemitério de caminhos
        que nunca mais casam com nada.

        A ordem das entradas antigas é preservada para o arquivo continuar legível
        num diff; só o conteúdo da linha muda quando o mesmo arquivo é reescrito.
        """
        fora = esquecer or set()
        indice = {entrada.destino: entrada for entrada in novas}
        atualizadas = [
            indice.pop(entrada.destino, entrada)
            for entrada in self.entradas
            if entrada.destino not in fora
        ]
        return DiarioDePropriedade(versao=self.versao, entradas=[*atualizadas, *indice.values()])


class DivergenciaDeSchema(BaseModel):
    """Campos que o mapeador achou no backend e o schema preservado não declara.

    Diff legível por máquina, e não só a frase de aviso que existia antes: é ele
    que sustenta o `REQUER_REVISAO` do recurso e que alguém abre depois para decidir
    se o schema é que está desatualizado.
    """

    model_config = ConfigDict(extra="forbid")

    recurso: str
    arquivo: Path
    campos_ausentes: list[str] = Field(default_factory=list[str])

    def render(self) -> str:
        return (
            f"[QAORQ-040] schema preservado {self.arquivo.name}: "
            "o mapeador encontrou no backend "
            f"{len(self.campos_ausentes)} campo(s) que ele não declara "
            f"({', '.join(self.campos_ausentes)}) — eles ficam fora do denominador "
            "da cobertura por campo"
        )
