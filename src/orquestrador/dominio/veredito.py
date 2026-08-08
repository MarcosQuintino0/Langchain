"""O que um gate decide, e o que uma reprovação manda de volta ao modelo.

Três estados, não dois: `APROVADO`, `REPROVADO` e `ERRO_DA_FERRAMENTA`. Script
que não rodou não é aprovação degradada, e também não é violação para o LLM
reparar — mandar o modelo consertar um `.mjs` que não executou queima tentativa
sem chance de convergir.

`Delta` é o **único** contexto novo que uma tentativa de reparo recebe, e é essa
restrição que troca custo quadrático por linear (princípio 2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orquestrador.excecoes import ErroDeFerramenta


class Violacao(BaseModel):
    """Uma reprovação (ou aviso) emitida por um gate.

    Os aliases espelham o JSON dos scripts `.mjs` (`{code, message, file, line}`),
    então `Violacao.model_validate(item)` consome a saída deles sem tradução manual.

    Os apelidos são declarados como `validation_alias` + `serialization_alias`, e não
    como o `alias=` que faz as duas coisas de uma vez. O motivo é estático: o
    verificador de tipo deriva a assinatura de `__init__` do `alias=` e **não lê**
    `populate_by_name`, então com `alias="code"` todo `Violacao(codigo=...)` do
    projeto — que roda perfeitamente — vira "No parameter named". Separar os dois
    apelidos deixa a assinatura em português, que é como o código constrói, e mantém
    o JSON da skill igual na entrada e na saída.
    """

    model_config = ConfigDict(populate_by_name=True)

    codigo: str = Field(validation_alias="code", serialization_alias="code")
    mensagem: str = Field(validation_alias="message", serialization_alias="message")
    arquivo: str | None = Field(default=None, validation_alias="file", serialization_alias="file")
    linha: int | None = Field(default=None, validation_alias="line", serialization_alias="line")

    def render(self) -> str:
        """Linha única para o prompt de reparo e para o console."""
        local = ""
        if self.arquivo:
            local = f" {self.arquivo}"
            if self.linha is not None:
                local += f":{self.linha}"
        return f"[{self.codigo}]{local} - {self.mensagem}"


class VereditoDeGate(StrEnum):
    """Os três desfechos de uma checagem determinística.

    O que um booleano não expressa é o terceiro: quando a ferramenta não rodou, não
    existe veredito sobre o artefato. Chamar isso de aprovação declara sucesso sem
    evidência; chamar de reprovação manda o modelo consertar um script que não
    executou — tentativa gasta sem chance nenhuma de convergir.
    """

    APROVADO = "aprovado"
    REPROVADO = "reprovado"
    ERRO_DA_FERRAMENTA = "erro_da_ferramenta"


class ResultadoGate(BaseModel):
    """Veredito de um gate determinístico sobre um artefato.

    **Construa por `aprovado_por`, `reprovado_por` ou `erro_da_ferramenta`**, nunca
    pelo construtor cru. Os três estados de `VereditoDeGate` não são simétricos —
    reprovar pede violações, erro de ferramenta pede motivo e nenhum dos dois vale
    para o outro —, e um construtor único aceita todas as combinações inclusive as
    que `_veredito_coerente` depois rejeita em tempo de execução. Nomear o estado no
    sítio de chamada move essa checagem para o verificador de tipo, e a leitura de
    `reprovado_por(violacoes=[...])` diz o desfecho sem precisar avaliar um booleano.

    `extra="forbid"` fecha a porta do apelido que existia aqui: até a Etapa 3.5 um
    validador `mode="before"` traduzia `aprovado=True/False` para o veredito. Sem o
    `forbid`, remover o validador faria `ResultadoGate(aprovado=False)` continuar
    construindo — em silêncio, com o padrão `APROVADO`. Aprovação por descuido é
    exatamente o falso sucesso que o gate existe para impedir.
    """

    model_config = ConfigDict(extra="forbid")

    veredito: VereditoDeGate = VereditoDeGate.APROVADO
    # `list[Violacao]` e não `list` como fábrica, aqui e nos outros modelos deste
    # módulo: o verificador de tipo não propaga a anotação do campo para dentro do
    # `default_factory` quando o elemento não é primitivo, e infere `list[Unknown]`
    # — o que apaga o tipo de `resultado.violacoes` em todo consumidor. Chamar
    # `list[Violacao]()` devolve exatamente a mesma lista vazia.
    violacoes: list[Violacao] = Field(default_factory=list[Violacao])
    avisos: list[Violacao] = Field(default_factory=list[Violacao])
    # Preenchido só no `ERRO_DA_FERRAMENTA`: é a mensagem que quem opera precisa ler
    # para consertar o ambiente. Não é violação, e por isso mora fora de `violacoes`
    # — o que está em `violacoes` vira delta e volta para o modelo.
    motivo: str = ""
    saida_bruta: str = ""
    gate: str = ""

    @model_validator(mode="after")
    def _veredito_coerente(self) -> ResultadoGate:
        if self.veredito is VereditoDeGate.APROVADO and self.violacoes:
            raise ValueError(
                "resultado aprovado com violação é contradição: "
                f"{', '.join(v.codigo for v in self.violacoes)}"
            )
        if self.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA and not self.motivo.strip():
            raise ValueError(
                "erro de ferramenta exige `motivo`: é a única coisa que quem opera "
                "recebe, e o loop de reparo não pode ajudar"
            )
        if self.veredito is not VereditoDeGate.ERRO_DA_FERRAMENTA and self.motivo:
            raise ValueError("`motivo` só descreve falha de ferramenta")
        return self

    @property
    def aprovado(self) -> bool:
        return self.veredito is VereditoDeGate.APROVADO

    @property
    def codigos(self) -> list[str]:
        return [violacao.codigo for violacao in self.violacoes]

    @classmethod
    def aprovado_por(
        cls,
        *,
        gate: str = "",
        avisos: list[Violacao] | None = None,
        saida_bruta: str = "",
    ) -> ResultadoGate:
        """Checagem que rodou e nada encontrou.

        Não recebe `violacoes` de propósito: aprovar com violação é a contradição que
        `_veredito_coerente` recusa, e aqui ela nem chega a ser escrevível. Aviso é
        outra coisa — vai para o log e para o console, nunca para o delta.
        """
        return cls(
            veredito=VereditoDeGate.APROVADO,
            avisos=avisos or [],
            saida_bruta=saida_bruta,
            gate=gate,
        )

    @classmethod
    def reprovado_por(
        cls,
        violacoes: list[Violacao],
        *,
        gate: str = "",
        avisos: list[Violacao] | None = None,
        saida_bruta: str = "",
    ) -> ResultadoGate:
        """Checagem que rodou e reprovou o artefato; `violacoes` vira o delta de reparo.

        A lista é posicional e obrigatória porque é ela que dá ao modelo o que
        consertar. Vazia é aceito — o validador da skill pode reprovar sem enumerar —,
        mas então a decisão de reprovar sem dizer o quê fica explícita em `[]`, não
        escondida num argumento omitido.
        """
        return cls(
            veredito=VereditoDeGate.REPROVADO,
            violacoes=violacoes,
            avisos=avisos or [],
            saida_bruta=saida_bruta,
            gate=gate,
        )

    @classmethod
    def erro_da_ferramenta(cls, motivo: str, *, gate: str, saida_bruta: str = "") -> ResultadoGate:
        """Checagem que não pôde ser feita — indisponibilidade, não veredito."""
        return cls(
            veredito=VereditoDeGate.ERRO_DA_FERRAMENTA,
            motivo=motivo,
            saida_bruta=saida_bruta,
            gate=gate,
        )

    @classmethod
    def combinar(cls, partes: list[ResultadoGate], *, gate: str) -> ResultadoGate:
        """Une os vereditos das checagens de um mesmo gate (todas precisam passar).

        Aprovar por ausência de violação é o defeito que esta função já teve: uma
        checagem que reprova sem conseguir descrever o motivo desaparecia na união.
        Agora aprovar exige as duas coisas — nenhum filho reprovado **e** nenhuma
        violação. E erro de ferramenta domina o resto: checagem que não rodou não é
        compensada por outra que rodou.
        """
        violacoes: list[Violacao] = []
        avisos: list[Violacao] = []
        bruta: list[str] = []
        motivos: list[str] = []
        for parte in partes:
            violacoes.extend(parte.violacoes)
            avisos.extend(parte.avisos)
            if parte.saida_bruta:
                bruta.append(parte.saida_bruta)
            if parte.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA:
                motivos.append(parte.motivo)

        if motivos:
            veredito = VereditoDeGate.ERRO_DA_FERRAMENTA
        elif violacoes or not all(parte.aprovado for parte in partes):
            veredito = VereditoDeGate.REPROVADO
        else:
            veredito = VereditoDeGate.APROVADO

        return cls(
            veredito=veredito,
            violacoes=violacoes,
            avisos=avisos,
            motivo="\n".join(motivos),
            saida_bruta="\n".join(bruta),
            gate=gate,
        )

    def exigir_veredito(self) -> ResultadoGate:
        """Devolve o resultado, ou interrompe se não houver veredito sobre o artefato.

        É aqui que o terceiro estado sai do vocabulário dos gates e vira interrupção.
        O loop de reparo (`Pipeline._ciclo`) só sabe aprovar ou montar delta, e delta
        de ferramenta quebrada é tentativa queimada: o modelo não tem como consertar
        um script que não rodou. Falhar alto deixa a mensagem na mão de quem pode.
        """
        if self.veredito is not VereditoDeGate.ERRO_DA_FERRAMENTA:
            return self
        raise ErroDeFerramenta(f"{self.gate or 'gate'} não pôde emitir veredito: {self.motivo}")


class EstadoDoRecurso(StrEnum):
    """Como um recurso terminou. Três estados, não dois.

    `REQUER_REVISAO` existe porque "preservei o contrato do cliente e ele diverge
    do que encontrei no backend" não é nenhum dos outros dois. Não é reprovação —
    os gates aprovaram, o artefato está íntegro e publicá-lo é o certo. E não pode
    ser aprovação: o denominador da cobertura por campo encolheu por um motivo que
    ninguém conferiu, então "100% coberto" ali significa "100% do que o schema
    declara", que é menos do que o backend tem.

    Quem decide entre atualizar o schema e aceitar a diferença é o dono do
    projeto. O orquestrador **nunca** atualiza schema existente para fazer teste
    passar: seria trocar a régua independente pela régua de quem é medido.
    """

    APROVADO = "aprovado"
    REPROVADO = "reprovado"
    REQUER_REVISAO = "requer_revisao"


EstagioDelta = Literal["gate_a", "gate_b", "schema"]


class Delta(BaseModel):
    """O **único** contexto novo que uma tentativa de reparo recebe.

    Princípio 2: `prompt_reparo = instrucao_fixa_do_estagio + artefato_atual +
    delta.violacoes`. Nada de histórico das tentativas anteriores — é isso que
    mantém o custo linear em vez de quadrático.
    """

    estagio: EstagioDelta
    recurso: str
    violacoes: list[Violacao]
    tentativa: int

    def render(self) -> str:
        cabecalho = (
            f"{len(self.violacoes)} violação(ões) reprovaram o artefato "
            f"em {self.estagio} (tentativa {self.tentativa}):"
        )
        return "\n".join([cabecalho, *(f"- {v.render()}" for v in self.violacoes)])
