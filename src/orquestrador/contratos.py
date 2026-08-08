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
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------

# As 12 categorias do catálogo. O *significado* de cada uma vive em
# references/catalogo-de-testes.md e é conteúdo de prompt (Fase 2); aqui só os ids.
CATS: tuple[str, ...] = tuple(f"CAT-{indice:02d}" for indice in range(1, 13))
CATS_SET = frozenset(CATS)

Cat = Annotated[str, StringConstraints(pattern=r"^CAT-(0[1-9]|1[0-2])$")]

# Estados aceitos numa exceção de campo ("<estado>: <motivo>"), conforme
# scripts/cobertura/campos/regras.mjs.
ESTADOS_DE_EXCECAO: tuple[str, ...] = ("naoAplica", "pendente", "bloqueada")

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
# Violações, gates e delta
# ---------------------------------------------------------------------------


class Violacao(BaseModel):
    """Uma reprovação (ou aviso) emitida por um gate.

    Os aliases espelham o JSON dos scripts `.mjs` (`{code, message, file, line}`),
    então `Violacao.model_validate(item)` consome a saída deles sem tradução manual.
    """

    model_config = ConfigDict(populate_by_name=True)

    codigo: str = Field(alias="code")
    mensagem: str = Field(alias="message")
    arquivo: str | None = Field(default=None, alias="file")
    linha: int | None = Field(default=None, alias="line")

    def render(self) -> str:
        """Linha única para o prompt de reparo e para o console."""
        local = ""
        if self.arquivo:
            local = f" {self.arquivo}"
            if self.linha is not None:
                local += f":{self.linha}"
        return f"[{self.codigo}]{local} - {self.mensagem}"


class ResultadoGate(BaseModel):
    """Veredito de um gate determinístico sobre um artefato."""

    aprovado: bool
    violacoes: list[Violacao] = Field(default_factory=list)
    avisos: list[Violacao] = Field(default_factory=list)
    saida_bruta: str = ""
    gate: str = ""

    @property
    def codigos(self) -> list[str]:
        return [violacao.codigo for violacao in self.violacoes]

    @classmethod
    def combinar(cls, partes: list["ResultadoGate"], *, gate: str) -> "ResultadoGate":
        """Une os vereditos das checagens de um mesmo gate (todas precisam passar)."""
        violacoes: list[Violacao] = []
        avisos: list[Violacao] = []
        bruta: list[str] = []
        for parte in partes:
            violacoes.extend(parte.violacoes)
            avisos.extend(parte.avisos)
            if parte.saida_bruta:
                bruta.append(parte.saida_bruta)
        return cls(
            aprovado=not violacoes,
            violacoes=violacoes,
            avisos=avisos,
            saida_bruta="\n".join(bruta),
            gate=gate,
        )


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

    nome: str
    caminho_testes: Path
    # Raiz do diretório de schemas do projeto de testes, vinda da configuração
    # (`[caminhos].dir_schemas`) como `caminho_testes`. Não é descoberta aqui: quem
    # sobe do recurso procurando `cypress/fixtures/schemas` é a skill
    # (`campos/schema.mjs`), e repetir a busca criaria uma segunda fonte de verdade
    # para o mesmo diretório.
    raiz_schemas: Path | None = None
    caminhos_backend: list[Path] = Field(default_factory=list)

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
            raise ValueError(
                f"rota deve ser o caminho completo começando em '/': {valor!r}"
            )
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

    recurso: str
    endpoints: list[Endpoint] = Field(min_length=1)
    rotas_dinamicas_nao_resolvidas: list[RotaDinamica] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sem_endpoint_duplicado(self) -> "Inventario":
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
    nao_aplica: dict[Cat, str] = Field(default_factory=dict, alias="naoAplica")
    schema_entrada: str | None = Field(default=None, alias="schemaEntrada")
    sem_corpo: str | None = Field(default=None, alias="semCorpo")
    campos: dict[str, str | dict[str, str]] | None = None

    @field_validator("endpoint")
    @classmethod
    def _canonico(cls, valor: str) -> str:
        # QAAPI-024 reprova endpoint fora da forma canônica. Rejeitar aqui (em vez de
        # normalizar em silêncio) transforma o desvio num delta de schema — o reparo
        # mais barato que existe — sem tirar do gate a autoridade sobre o arquivo.
        canonico = normalizar_endpoint(valor)
        if valor != canonico:
            raise ValueError(
                f'endpoint fora da forma canônica "{canonico}": {valor!r}'
            )
        partes = canonico.split(" ", 1)
        if len(partes) != 2 or partes[0] not in METODOS_HTTP or not partes[1].startswith("/"):
            raise ValueError(
                f'endpoint deve ter a forma "MÉTODO /rota/completa": {valor!r}'
            )
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

    recurso: str = Field(min_length=1)
    profundidade: Literal["completa", "contrato"] | None = None
    handler_compartilhado: str | None = Field(default=None, alias="handlerCompartilhado")
    handler_coberto_por: str | None = Field(default=None, alias="handlerCobertoPor")
    sub_dominios: dict[str, SubDominio] | None = Field(default=None, alias="subDominios")
    endpoints: list[EndpointManifesto] = Field(min_length=1)

    @model_validator(mode="after")
    def _endpoints_unicos(self) -> "Manifesto":
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
            raise ValueError(
                f'schema precisa terminar em "{SUFIXO_SCHEMA}": {valor!r}'
            )
        return caminho


class SaidaMapeador(BaseModel):
    """O que o Bloco 1 emite para um recurso."""

    model_config = ConfigDict(extra="forbid")

    inventario: Inventario
    manifesto: Manifesto
    schemas: list[ArquivoSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mesmo_recurso(self) -> "SaidaMapeador":
        if self.inventario.recurso != self.manifesto.recurso:
            raise ValueError(
                "inventario.recurso e manifesto.recurso precisam ser o mesmo recurso: "
                f"{self.inventario.recurso!r} != {self.manifesto.recurso!r}"
            )
        return self

    @model_validator(mode="after")
    def _schemas_cobrem_o_declarado(self) -> "SaidaMapeador":
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
                    f'{endpoint.endpoint} declara schemaEntrada '
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

    recurso: str
    arquivos: list[ArquivoGerado] = Field(min_length=1)

    @model_validator(mode="after")
    def _sem_caminho_repetido(self) -> "SaidaExecutor":
        vistos: set[str] = set()
        for arquivo in self.arquivos:
            if arquivo.caminho in vistos:
                raise ValueError(f"arquivo repetido na saída: {arquivo.caminho}")
            vistos.add(arquivo.caminho)
        return self


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
    exports: list[ExportCompartilhado] = Field(default_factory=list)


class SuperficieDoProjeto(BaseModel):
    """O que o projeto de testes oferece de pronto ao executor.

    Extraída deterministicamente, uma vez por execução (é do projeto, não do
    recurso), e entregue pela **instrução fixa** do estágio — nunca pela entrada da
    tentativa, que é reenviada a cada reparo.
    """

    model_config = ConfigDict(extra="forbid")

    raiz: str
    modulos: list[ModuloCompartilhado] = Field(default_factory=list)

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
        default_factory=list, alias="naoAplica_refutados"
    )
    oraculos_fracos: list[AchadoAuditoria] = Field(default_factory=list)
    veredito: Literal["íntegro", "revisar"]


# ---------------------------------------------------------------------------
# Telemetria
# ---------------------------------------------------------------------------


class UsoDeTokens(BaseModel):
    entrada: int = 0
    saida: int = 0

    @property
    def total(self) -> int:
        return self.entrada + self.saida

    def __add__(self, outro: "UsoDeTokens") -> "UsoDeTokens":
        return UsoDeTokens(
            entrada=self.entrada + outro.entrada, saida=self.saida + outro.saida
        )


class RegistroDeChamada(BaseModel):
    """Uma chamada de modelo, para provar (ou refutar) o custo linear.

    `caracteres_instrucao` e `caracteres_entrada` medem o que foi **efetivamente
    enviado**: a instrução fixa do estágio (constante, serve de linha de base) e a
    entrada da tentativa. É por essa dupla que se verifica o princípio 2 a partir
    do log — a entrada de um reparo não pode crescer com o número da tentativa.
    """

    estagio: str
    recurso: str
    tentativa: int
    modelo: str
    uso: UsoDeTokens = Field(default_factory=UsoDeTokens)
    duracao_s: float = 0.0
    simulado: bool = False
    detalhe: str = ""
    caracteres_instrucao: int = 0
    caracteres_entrada: int = 0


class RegistroDeTool(BaseModel):
    """Uma chamada de tool do mapeador — o instrumento do custo de exploração.

    O Graphify existe para localizar código sem gastar token varrendo o backend, e a
    instrução do estágio manda consultá-lo **antes** de ler arquivo. Sem este
    registro, "ele obedeceu?" e "que fatia da entrada veio de resposta de tool?" são
    dedução, não medida — e é sobre elas que se decide a otimização do mapeador, que
    é onde mora quase todo o custo do pipeline.

    `ordem` é a posição na sequência da tentativa: é ela, e não o total, que responde
    se o grafo foi consultado antes ou depois da leitura de arquivo.

    `caracteres` é o tamanho do retorno. É o número que importa: ele entra na próxima
    volta do ReAct e é reenviado em todas as seguintes, então resposta de tool grande
    é multiplicador, não parcela.
    """

    estagio: str
    recurso: str
    tentativa: int
    ordem: int
    nome: str
    argumentos: dict[str, Any] = Field(default_factory=dict)
    caracteres: int = 0
    duracao_s: float = 0.0
    # As tools devolvem a falha como texto (`ERRO: ...`) para o modelo poder se
    # corrigir sozinho. Sem esta marca, grafo quebrado passa por exploração
    # bem-sucedida no relatório — e o custo de reindexar aparece como custo de LLM.
    erro: bool = False


def dados_para_log(valor: Any) -> Any:
    """Converte modelos/Path para algo serializável em JSONL."""
    if isinstance(valor, BaseModel):
        return valor.model_dump(mode="json")
    if isinstance(valor, Path):
        return str(valor)
    if isinstance(valor, (list, tuple)):
        return [dados_para_log(item) for item in valor]
    if isinstance(valor, dict):
        return {chave: dados_para_log(item) for chave, item in valor.items()}
    return valor
