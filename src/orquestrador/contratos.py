"""Contratos de dados que trafegam entre os estágios do pipeline.

Princípio 1 da arquitetura: o que passa de um estágio para o outro é **artefato em
disco**, nunca histórico de conversa. Este módulo define a forma desses artefatos —
é o vocabulário comum entre mapeador, gates, executor e auditor.

Decisão de projeto sobre a fronteira Pydantic × gate determinístico
-------------------------------------------------------------------
O `Manifesto` aqui valida apenas o que é **estrutural** (tipos, ids de categoria bem
formados, endpoint na forma canônica, ausência de campo desconhecido). A
**contabilidade das 12 categorias** — `cats` ∪ `naoAplica` cobrindo CAT-01..CAT-12 com
interseção vazia, e a qualidade das justificativas — fica deliberadamente de fora:
quem reprova isso é o `validar-suite-gerada.mjs` (princípio 4, "quem reprova é
script"). Duplicar a regra aqui apagaria o Gate A do fluxo e criaria duas fontes de
verdade para a mesma invariante.

Fonte da forma do manifesto (`_support/cobertura.json`): SKILL.md passo 6 e
`skills/qa-api/scripts/cobertura/manifesto.mjs` (+ `estrutura.mjs`, `campos/`).
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from orquestrador.excecoes import ErroDeFerramenta

# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------

# As 12 categorias do catálogo. O *significado* de cada uma vive em
# references/catalogo-de-testes.md e é conteúdo de prompt (Fase 2); aqui só os ids.
CATS: tuple[str, ...] = tuple(f"CAT-{indice:02d}" for indice in range(1, 13))

Cat = Annotated[str, StringConstraints(pattern=r"^CAT-(0[1-9]|1[0-2])$")]

METODOS_HTTP: tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
)

# Métodos que o gate considera escrita (campos/reconciliar.mjs).
METODOS_DE_ESCRITA = frozenset({"POST", "PUT", "PATCH"})


def normalizar_endpoint(valor: str) -> str:
    """Forma canônica de um endpoint, igual a `normalizarEndpoint` de comum.mjs."""
    return re.sub(r"\s+", " ", str(valor).strip())


# ---------------------------------------------------------------------------
# Nome de recurso
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Violações, gates e delta
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Unidade de trabalho e descoberta
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Manifesto — espelha _support/cobertura.json
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Saídas dos estágios de LLM
# ---------------------------------------------------------------------------

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


class SaidaMapeador(BaseModel):
    """O que o Bloco 1 emite para um recurso."""

    model_config = ConfigDict(extra="forbid")

    inventario: Inventario
    manifesto: Manifesto
    schemas: list[ArquivoSchema] = Field(default_factory=list[ArquivoSchema])

    @model_validator(mode="after")
    def _mesmo_recurso(self) -> SaidaMapeador:
        if self.inventario.recurso != self.manifesto.recurso:
            raise ValueError(
                "inventario.recurso e manifesto.recurso precisam ser o mesmo recurso: "
                f"{self.inventario.recurso!r} != {self.manifesto.recurso!r}"
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


# ---------------------------------------------------------------------------
# Propriedade de arquivo e publicação
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Superfície de módulos compartilhados do projeto de testes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Auditor semântico (stub na Fase 1)
# ---------------------------------------------------------------------------


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
