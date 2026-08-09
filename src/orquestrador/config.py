"""Carga e validação da configuração do orquestrador.

Tudo que varia entre projetos — caminhos, modelo de cada estágio, flags de cada
gate, limite de tentativas — mora no arquivo de configuração. Princípio 6: nenhum
nome de modelo é fixado no código.

Caminhos relativos no TOML são resolvidos **contra o diretório do próprio arquivo
de configuração**, para que a config seja movível junto com o projeto.

Os limites numéricos são **tipos**, não convenção: `PositiveInt` e
`NonNegativeFloat` com teto declarado. Um `max_tentativas = 0` desliga o loop de
reparo em silêncio e um `-1` faz o pipeline pular o estágio inteiro sem erro
nenhum — o tipo é o que impede a configuração de voltar a ficar inválida por um
caminho que ninguém testou. Pelo mesmo motivo os modelos têm
`validate_assignment=True`: mutação depois da carga é tão capaz de invalidar a
configuração quanto o próprio arquivo.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

from orquestrador.dominio.orcamento import Orcamento
from orquestrador.excecoes import ErroDeConfiguracao
from orquestrador.raiz import CONFIG_PADRAO, DIR_PROMPTS_PADRAO

ModoEstruturado = Literal["json_schema", "json_object", "tools", "prompt"]

# Tetos. Não existe número "certo" aqui; existe a fronteira acima da qual o valor
# quase certamente é engano de digitação e sai caro — cada tentativa é uma volta
# inteira de LLM, e cada passo do ReAct reenvia o histórico da exploração.
Tentativas = Annotated[PositiveInt, Field(le=20)]
PassosDoAgente = Annotated[PositiveInt, Field(le=500)]
# 2.0 é o teto que os provedores aceitam; acima disso a chamada é recusada lá.
Temperatura = Annotated[NonNegativeFloat, Field(le=2.0)]
Segundos = Annotated[PositiveInt, Field(le=3600)]
SegundosFracionados = Annotated[PositiveFloat, Field(le=3600.0)]

# `extra="forbid"` pega chave com erro de digitação; `validate_assignment=True`
# estende a garantia a quem escreve no modelo depois da carga.
MODELO_DE_CONFIG = ConfigDict(extra="forbid", validate_assignment=True)


class ConfigCaminhos(BaseModel):
    """Onde estão a skill, o backend e o projeto de testes."""

    model_config = MODELO_DE_CONFIG

    skill: Path
    scripts: Path | None = None
    backend: Path
    projeto_testes: Path
    # Relativos ao projeto de testes.
    dir_recursos: str = "cypress/e2e/apis"
    # Raiz dos schemas de entrada, emitidos pelo mapeador e lidos pelo validador da
    # skill. Configurável porque a skill aceita dois layouts (`cypress/fixtures/schemas`
    # e `fixtures/schemas`) e quem escolhe é o projeto do consumidor.
    dir_schemas: str = "cypress/fixtures/schemas"
    graph: str = ".agents/state/qa-api/graphify-out/graph.json"
    # Módulos compartilhados que o executor consome (client, rotas, auth, asserts
    # base, schema). Configurável porque a skill manda preservar o padrão do
    # consumidor, e nem todo projeto usa este caminho.
    support_compartilhado: str = "cypress/support/api"
    # Relativos à raiz do projeto (resolvidos na carga).
    saida: Path = Path(".execucoes")
    # Prompt é conteúdo, não código: fica fora do pacote, na raiz do projeto, e o
    # caminho é configurável para a Fase 2 poder iterá-los onde quiser.
    prompts: Path = DIR_PROMPTS_PADRAO

    @model_validator(mode="before")
    @classmethod
    def _derivar_scripts(cls, bruto: Any) -> Any:
        """`scripts` omitido vira `<skill>/scripts`.

        Feito **antes** da construção, e não num validador `after`: com
        `validate_assignment=True`, escrever num campo dentro do validador `after`
        dispara outra rodada de validação do modelo inteiro.
        """
        if not isinstance(bruto, dict):
            return bruto
        # Um validador `mode="before"` recebe o que quem chamou passou, então `Any` é a
        # anotação honesta. O `cast` só nomeia o que o `isinstance` acabou de provar
        # sobre a forma — chave de TOML é string —, sem afirmar nada sobre os valores.
        dados = cast(dict[str, Any], bruto)
        if not dados.get("scripts") and dados.get("skill"):
            return {**dados, "scripts": Path(str(dados["skill"])) / "scripts"}
        return dados

    # -- caminhos derivados -------------------------------------------------

    def script(self, nome: str) -> Path:
        # Checagem real, e não `assert`: o `_derivar_scripts` só preenche `scripts`
        # quando o `skill` bruto é verdadeiro, então um `skill = ""` no config.toml
        # atravessa a validação (vira `Path(".")`) e deixa `scripts` em None. Quem
        # tropeça nisso primeiro é `validar_caminhos`, cuja função é justamente
        # transformar caminho errado em mensagem acionável — e um `assert` nu ali
        # devolvia `AssertionError('')`, sem dizer o que estava errado. Sob
        # `python -O` seria pior: some a checagem e sobra um TypeError de `None /
        # str`, dentro do adaptador de subprocesso, longe da causa.
        if self.scripts is None:
            raise ErroDeConfiguracao(
                "[caminhos].scripts não pôde ser derivado porque [caminhos].skill está "
                "vazio. Preencha o caminho da skill qa-api, ou aponte scripts direto."
            )
        return self.scripts / nome

    @property
    def dir_recursos_abs(self) -> Path:
        return self.projeto_testes / self.dir_recursos

    @property
    def dir_schemas_abs(self) -> Path:
        return self.projeto_testes / self.dir_schemas

    @property
    def graph_abs(self) -> Path:
        return self.projeto_testes / self.graph

    @property
    def support_abs(self) -> Path:
        return self.projeto_testes / self.support_compartilhado

    def recurso(self, nome: str) -> Path:
        return self.dir_recursos_abs / nome


class ConfigOpenRouter(BaseModel):
    """Acesso ao modelo. A chave vem sempre do ambiente, nunca do arquivo."""

    model_config = MODELO_DE_CONFIG

    # Tipo de URL, não string: `base_url` termina concatenada com o caminho da API
    # dentro do cliente, e um valor sem esquema vira um 404 que se parece com
    # indisponibilidade do provedor. Quem consome converte com `str()`.
    base_url: AnyHttpUrl = AnyHttpUrl("https://openrouter.ai/api/v1")
    api_key_env: str = "OPENROUTER_API_KEY"
    # OpenRouter usa estes cabeçalhos para atribuição; opcionais.
    referer: str | None = None
    titulo: str | None = None
    timeout_s: SegundosFracionados = 180.0
    # Zero é legítimo: significa "não tente de novo".
    max_retries: Annotated[NonNegativeInt, Field(le=10)] = 2

    # Para onde o código-fonte do cliente pode ir. Conjunto fechado, conferido na
    # carga da configuração: `base_url` fora daqui é `ErroDeConfiguracao`, e não
    # aviso. Trocar a `base_url` é a mudança de uma linha que passa despercebida
    # numa revisão e manda o backend de quem nos contratou para outro lugar.
    hosts_permitidos: list[str] = Field(default_factory=lambda: ["openrouter.ai"])

    # O OpenRouter é um roteador: o endpoint é um só, e o provedor que de fato
    # executa a inferência é escolhido por ele. Sem estes dois campos, "mandei para
    # o openrouter.ai" não diz nada sobre quem leu o código.
    #
    # `retencao_de_dados="deny"` pede ao roteador que use apenas provedores que não
    # retêm o conteúdo. `provedores_permitidos`, quando preenchido, restringe a
    # lista e **desliga o fallback**: sem essa segunda parte, um provedor
    # indisponível faria o roteador escolher outro qualquer, que é exatamente o
    # caso em que a política importa.
    retencao_de_dados: Literal["deny", "allow"] = "deny"
    provedores_permitidos: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _base_url_esta_na_allowlist(self) -> ConfigOpenRouter:
        host = self.base_url.host or ""
        if host not in self.hosts_permitidos:
            raise ValueError(
                f"[openrouter].base_url aponta para {host!r}, que não está em "
                f"hosts_permitidos ({', '.join(self.hosts_permitidos)}).\n"
                "Não há fallback aqui de propósito: o que atravessa esta fronteira é "
                "o código-fonte de quem nos contratou. Se o destino novo é "
                "legítimo, declare-o em [openrouter].hosts_permitidos, na mesma "
                "mudança — a declaração é o que torna a escolha revisável."
            )
        return self

    def roteamento(self) -> dict[str, Any]:
        """O bloco `provider` que acompanha cada chamada ao OpenRouter.

        Vazio só se a política for `allow` sem lista de provedores — o que é uma
        escolha explícita de quem configurou, não um padrão.
        """
        politica: dict[str, Any] = {"data_collection": self.retencao_de_dados}
        if self.provedores_permitidos:
            politica["only"] = list(self.provedores_permitidos)
            politica["allow_fallbacks"] = False
        return politica

    def politica_declarada(self) -> dict[str, Any]:
        """A política, como ela vai para o manifesto de execução.

        É **o que foi pedido**, não o que aconteceu: o provedor efetivo de cada
        chamada viria da resposta, e hoje o orquestrador não o lê. A distinção está
        no nome do campo de propósito — chamá-lo de "rota efetiva" seria dizer que
        temos evidência quando temos declaração.
        """
        return {
            "host": self.base_url.host,
            "hosts_permitidos": list(self.hosts_permitidos),
            "retencao_de_dados": self.retencao_de_dados,
            "provedores_permitidos": list(self.provedores_permitidos),
            "fallback_permitido": not self.provedores_permitidos,
        }

    def chave(self) -> str:
        chave = os.environ.get(self.api_key_env, "").strip()
        if not chave:
            raise ErroDeConfiguracao(
                f"variável de ambiente {self.api_key_env} não definida. "
                "Crie um .env ao lado da config (veja .env.exemplo) ou exporte a chave. "
                "Para rodar sem modelo nenhum, use --dry-run."
            )
        return chave

    def headers(self) -> dict[str, str]:
        extras: dict[str, str] = {}
        if self.referer:
            extras["HTTP-Referer"] = self.referer
        if self.titulo:
            extras["X-Title"] = self.titulo
        return extras


class ConfigEstagio(BaseModel):
    """Modelo e parâmetros de um estágio de LLM."""

    model_config = MODELO_DE_CONFIG

    modelo: str
    temperatura: Temperatura = 0.0
    max_tokens: PositiveInt | None = None
    # Como pedir saída estruturada. "prompt" é o mais portátil entre modelos do
    # OpenRouter; os outros usam o mecanismo nativo quando o modelo suporta.
    # Em qualquer modo a validação final é feita por Pydantic aqui no orquestrador.
    modo_estruturado: ModoEstruturado = "prompt"
    # Mini-loop de reparo do delta "schema" (saída que não valida).
    max_tentativas_schema: Tentativas = 3
    # Teto de passos do loop ReAct (só o mapeador usa).
    limite_passos: PassosDoAgente = 40

    # Teto de tokens de **raciocínio** por chamada. `None` deixa o padrão do
    # provedor, que é sem teto.
    #
    # Isto não é ajuste fino: num modelo de raciocínio, o pensamento consome o
    # MESMO orçamento de saída que a resposta. Medido nesta configuração — reparo
    # com 30 mil caracteres de entrada, DeepSeek V4 Flash: 63.172 tokens de
    # raciocínio de um teto de 65.536, e a resposta foi cortada no meio. O sintoma
    # não é "erro do provedor": é `QAORQ-011`, saída que não é JSON, porque o que
    # chega é o rascunho do pensamento truncado.
    #
    # Zero não é um valor útil aqui, e a faixa recusa: com o raciocínio desligado o
    # mesmo modelo devolveu `{"arquivos": []}` — estrutura válida e vazia, que só não
    # passou porque o contrato exige ao menos um arquivo. Limitar resolve; desligar
    # troca um defeito por outro.
    max_tokens_de_raciocinio: Annotated[PositiveInt, Field(le=100_000)] | None = None


class ConfigGate(BaseModel):
    """Flags do validador e limite de tentativas de reparo de um gate."""

    model_config = MODELO_DE_CONFIG

    flags: list[str] = Field(default_factory=list)
    max_tentativas: Tentativas = 3
    # Reprovar quando o gabarito declarar categoria que nenhum `it` cobre. Ligado por
    # padrão: é a checagem que responde por "planejei e não entreguei", que é o
    # defeito de origem do projeto. Só o Gate B a consome.
    exigir_cobertura: bool = True


class ConfigExecucao(BaseModel):
    """Executáveis externos e limites de processo."""

    model_config = MODELO_DE_CONFIG

    node: str = "node"
    graphify: str = "graphify"
    timeout_s: Segundos = 600
    # Comandos opcionais do Gate B. Lista vazia = etapa desligada.
    prettier: list[str] = Field(default_factory=list)
    eslint: list[str] = Field(default_factory=list)
    # Ferramenta configurada mas ausente do PATH: aviso QAORQ-022 (False) ou
    # interrupção por erro de ferramenta (True). Não reprova em nenhum dos dois: PATH
    # de quem roda o pipeline não é algo que o executor conserte reescrevendo teste.
    exigir_formatadores: bool = False
    # Bloco 3.
    cypress: list[str] = Field(default_factory=list)
    # Orçamento das tools de leitura do mapeador. Teto alto de propósito: o que
    # protege o custo é a soma, não cada leitura — mas zero desligaria as tools.
    max_bytes_arquivo: Annotated[PositiveInt, Field(le=50_000_000)] = 2_000_000
    max_resultados_busca: Annotated[PositiveInt, Field(le=1_000)] = 40


class ConfigSkill(BaseModel):
    """Compatibilidade com a skill externa `qa-api`.

    A skill é outro repositório e a integração com ela é um contrato **implícito**:
    formato dos argumentos, código de saída, forma do JSON e semântica dos códigos
    `QAAPI-`. Nada disso está declarado em lugar nenhum, e é por isso que precisa de
    uma âncora.

    Campo vazio desliga a verificação — é o padrão para quem está desenvolvendo a
    skill e o consumidor ao mesmo tempo, onde o hash mudaria a cada edição.
    """

    model_config = MODELO_DE_CONFIG

    impressao_esperada: str = ""


class Config(BaseModel):
    model_config = MODELO_DE_CONFIG

    caminhos: ConfigCaminhos
    openrouter: ConfigOpenRouter = Field(default_factory=ConfigOpenRouter)
    estagios: dict[str, ConfigEstagio]
    gates: dict[str, ConfigGate]
    execucao: ConfigExecucao = Field(default_factory=ConfigExecucao)
    skill: ConfigSkill = Field(default_factory=lambda: ConfigSkill())
    # Sem tetos configurados, `Orcamento.configurado` é falso e a checagem custa
    # uma comparação por chamada. Orçamento que aparece sem ninguém pedir
    # interrompe execução legítima e ensina a desligá-lo.
    orcamento: Orcamento = Field(default_factory=Orcamento)
    origem: Path | None = None

    # -- carga --------------------------------------------------------------

    @classmethod
    def carregar(cls, caminho: Path | str | None = None) -> Config:
        arquivo = Path(caminho) if caminho else CONFIG_PADRAO
        if not arquivo.is_file():
            raise ErroDeConfiguracao(f"arquivo de configuração não encontrado: {arquivo}")
        with arquivo.open("rb") as fluxo:
            bruto: dict[str, Any] = tomllib.load(fluxo)
        base = arquivo.resolve().parent
        bruto = _resolver_caminhos(bruto, base)
        bruto["origem"] = arquivo.resolve()
        try:
            config = cls.model_validate(bruto)
        except ValidationError as erro:
            # A CLI trata `ErroDeConfiguracao` como código 2 e mensagem legível; um
            # ValidationError cru sobe como traceback e parece defeito do programa.
            raise ErroDeConfiguracao(
                f"{arquivo}:\n"
                + "\n".join(
                    f"  - {'.'.join(str(parte) for parte in item['loc'])}: {item['msg']}"
                    for item in erro.errors()
                )
            ) from erro
        config._exigir_estagios()
        return config

    def _exigir_estagios(self) -> None:
        faltando = [nome for nome in ("mapeador", "executor") if nome not in self.estagios]
        if faltando:
            raise ErroDeConfiguracao(f"[estagios] sem entrada para: {', '.join(faltando)}")
        faltando_gates = [nome for nome in ("a", "b") if nome not in self.gates]
        if faltando_gates:
            raise ErroDeConfiguracao(f"[gates] sem entrada para: {', '.join(faltando_gates)}")

    def estagio(self, nome: str) -> ConfigEstagio:
        try:
            return self.estagios[nome]
        except KeyError as erro:
            raise ErroDeConfiguracao(f"estágio não configurado: {nome}") from erro

    def gate(self, nome: str) -> ConfigGate:
        try:
            return self.gates[nome]
        except KeyError as erro:
            raise ErroDeConfiguracao(f"gate não configurado: {nome}") from erro

    # -- overrides ----------------------------------------------------------

    def com_max_tentativas(self, maximo: int) -> Config:
        """Cópia com o `max_tentativas` de todos os gates sobrescrito.

        Cópia revalidada, não mutação: `--max-tentativas` chega de fora e precisa
        atravessar o mesmo tipo que o arquivo atravessou. A CLI escrevia direto no
        modelo já construído, então um valor fora da faixa entrava sem passar por
        validação nenhuma.
        """
        try:
            gates = {
                nome: ConfigGate.model_validate({**gate.model_dump(), "max_tentativas": maximo})
                for nome, gate in self.gates.items()
            }
        except ValidationError as erro:
            raise ErroDeConfiguracao(
                f"--max-tentativas {maximo!r} é inválido: {erro.errors()[0]['msg']}"
            ) from erro
        return self.model_copy(update={"gates": gates})

    # -- validação de ambiente ---------------------------------------------

    def validar_caminhos(self, *, exigir_backend: bool = True) -> None:
        """Confere o que precisa existir antes de uma execução real."""
        problemas: list[str] = []
        if not self.caminhos.skill.is_dir():
            problemas.append(f"skill não encontrada: {self.caminhos.skill}")
        for nome in ("validar-suite-gerada.mjs", "qa-cobertura.mjs", "qa-reindex.mjs"):
            if not self.caminhos.script(nome).is_file():
                problemas.append(f"script ausente: {self.caminhos.script(nome)}")
        if not self.caminhos.projeto_testes.is_dir():
            problemas.append(f"projeto de testes não encontrado: {self.caminhos.projeto_testes}")
        if exigir_backend and not self.caminhos.backend.is_dir():
            problemas.append(f"backend não encontrado: {self.caminhos.backend}")
        if problemas:
            raise ErroDeConfiguracao(
                "configuração inválida:\n" + "\n".join(f"  - {item}" for item in problemas)
            )
        self.validar_impressao_da_skill()

    def validar_impressao_da_skill(self) -> None:
        """Recusa rodar contra uma skill diferente da que este consumidor conhece.

        A skill é outro repositório, e a integração com ela é um contrato implícito:
        formato dos argumentos, código de saída, forma do JSON e semântica dos
        códigos `QAAPI-`. Nada disso é declarado em lugar nenhum — uma mudança
        compatível do ponto de vista dela pode quebrar este consumidor, ou pior,
        mudar em silêncio o significado de uma aprovação.

        A verificação é a mesma que o `qa-reindex.mjs` já faz com a versão do
        Graphify, e pelo mesmo motivo: descobrir a incompatibilidade na primeira
        linha do log é barato; descobri-la num veredito errado, não.

        Impressão de conteúdo em vez de commit: a skill nem sempre é um checkout
        Git na máquina do consumidor, e o que importa não é qual revisão está lá —
        é se os scripts que invocamos mudaram.
        """
        if not self.skill.impressao_esperada:
            return
        atual = self.impressao_da_skill()
        if atual == self.skill.impressao_esperada:
            return
        raise ErroDeConfiguracao(
            f"a skill em {self.caminhos.scripts} mudou desde a última verificação.\n"
            f"  esperada: {self.skill.impressao_esperada}\n"
            f"  atual:    {atual}\n"
            "Reveja o contrato antes de seguir — argumentos, códigos de saída, forma do "
            "JSON e os códigos QAAPI- são acordo implícito entre os dois repositórios, e "
            "os prompts em prompts/ são destilados das references/ dela.\n"
            f'Conferido, atualize [skill].impressao_esperada = "{atual}" na configuração. '
            "Para desligar a verificação, deixe o campo vazio."
        )

    def impressao_da_skill(self) -> str:
        """Hash do conteúdo dos scripts `.mjs` que este orquestrador invoca.

        O caminho relativo entra no hash junto do conteúdo: renomear um módulo de
        `cobertura/` sem mudar uma linha dele também muda o contrato.

        `script()` em vez de `self.caminhos.scripts` direto porque é ele que
        transforma `scripts` ausente numa mensagem acionável.
        """
        raiz = self.caminhos.script("qa-cobertura.mjs").parent
        resumo = hashlib.sha256()
        for arquivo in sorted(raiz.rglob("*.mjs")):
            resumo.update(arquivo.relative_to(raiz).as_posix().encode("utf-8"))
            resumo.update(arquivo.read_bytes())
        return resumo.hexdigest()[:16]


def _resolver_caminhos(bruto: dict[str, Any], base: Path) -> dict[str, Any]:
    """Torna absolutos os caminhos do bloco [caminhos], usando `base` como âncora."""
    caminhos = dict(bruto.get("caminhos") or {})
    for chave in ("skill", "scripts", "backend", "projeto_testes", "saida", "prompts"):
        valor = caminhos.get(chave)
        if valor is None:
            continue
        alvo = Path(str(valor)).expanduser()
        caminhos[chave] = str(alvo if alvo.is_absolute() else (base / alvo).resolve())
    bruto["caminhos"] = caminhos
    return bruto
