"""Carga e validação da configuração do orquestrador.

Tudo que varia entre projetos — caminhos, modelo de cada estágio, flags de cada
gate, limite de tentativas — mora no arquivo de configuração. Princípio 6: nenhum
nome de modelo é fixado no código.

Caminhos relativos no TOML são resolvidos **contra o diretório do próprio arquivo
de configuração**, para que a config seja movível junto com o projeto.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orquestrador.raiz import CONFIG_PADRAO, DIR_PROMPTS_PADRAO

ModoEstruturado = Literal["json_schema", "json_object", "tools", "prompt"]


class ErroDeConfiguracao(RuntimeError):
    """Configuração ausente, incoerente ou apontando para caminho inexistente."""


class ConfigCaminhos(BaseModel):
    """Onde estão a skill, o backend e o projeto de testes."""

    model_config = ConfigDict(extra="forbid")

    skill: Path
    scripts: Path | None = None
    backend: Path
    projeto_testes: Path
    # Relativos ao projeto de testes.
    dir_recursos: str = "cypress/e2e/apis"
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

    @model_validator(mode="after")
    def _derivar_scripts(self) -> "ConfigCaminhos":
        if self.scripts is None:
            self.scripts = self.skill / "scripts"
        return self

    # -- caminhos derivados -------------------------------------------------

    def script(self, nome: str) -> Path:
        assert self.scripts is not None  # garantido pelo validador
        return self.scripts / nome

    @property
    def dir_recursos_abs(self) -> Path:
        return self.projeto_testes / self.dir_recursos

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

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    # OpenRouter usa estes cabeçalhos para atribuição; opcionais.
    referer: str | None = None
    titulo: str | None = None
    timeout_s: float = 180.0
    max_retries: int = 2

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

    model_config = ConfigDict(extra="forbid")

    modelo: str
    temperatura: float = 0.0
    max_tokens: int | None = None
    # Como pedir saída estruturada. "prompt" é o mais portátil entre modelos do
    # OpenRouter; os outros usam o mecanismo nativo quando o modelo suporta.
    # Em qualquer modo a validação final é feita por Pydantic aqui no orquestrador.
    modo_estruturado: ModoEstruturado = "prompt"
    # Mini-loop de reparo do delta "schema" (saída que não valida).
    max_tentativas_schema: int = 3
    # Teto de passos do loop ReAct (só o mapeador usa).
    limite_passos: int = 40


class ConfigGate(BaseModel):
    """Flags do validador e limite de tentativas de reparo de um gate."""

    model_config = ConfigDict(extra="forbid")

    flags: list[str] = Field(default_factory=list)
    max_tentativas: int = 3

    @model_validator(mode="after")
    def _tentativas_positivas(self) -> "ConfigGate":
        if self.max_tentativas < 1:
            raise ValueError("max_tentativas deve ser >= 1")
        return self


class ConfigExecucao(BaseModel):
    """Executáveis externos e limites de processo."""

    model_config = ConfigDict(extra="forbid")

    node: str = "node"
    graphify: str = "graphify"
    timeout_s: int = 600
    # Comandos opcionais do Gate B. Lista vazia = etapa desligada.
    prettier: list[str] = Field(default_factory=list)
    eslint: list[str] = Field(default_factory=list)
    # Ferramenta configurada mas ausente do PATH: aviso (False) ou reprovação (True).
    exigir_formatadores: bool = False
    # Bloco 3.
    cypress: list[str] = Field(default_factory=list)
    # Orçamento das tools de leitura do mapeador.
    max_bytes_arquivo: int = 2_000_000
    max_resultados_busca: int = 40


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caminhos: ConfigCaminhos
    openrouter: ConfigOpenRouter = Field(default_factory=ConfigOpenRouter)
    estagios: dict[str, ConfigEstagio]
    gates: dict[str, ConfigGate]
    execucao: ConfigExecucao = Field(default_factory=ConfigExecucao)
    origem: Path | None = None

    # -- carga --------------------------------------------------------------

    @classmethod
    def carregar(cls, caminho: Path | str | None = None) -> "Config":
        arquivo = Path(caminho) if caminho else CONFIG_PADRAO
        if not arquivo.is_file():
            raise ErroDeConfiguracao(f"arquivo de configuração não encontrado: {arquivo}")
        with arquivo.open("rb") as fluxo:
            bruto: dict[str, Any] = tomllib.load(fluxo)
        base = arquivo.resolve().parent
        bruto = _resolver_caminhos(bruto, base)
        bruto["origem"] = arquivo.resolve()
        config = cls.model_validate(bruto)
        config._exigir_estagios()
        return config

    def _exigir_estagios(self) -> None:
        faltando = [nome for nome in ("mapeador", "executor") if nome not in self.estagios]
        if faltando:
            raise ErroDeConfiguracao(
                f"[estagios] sem entrada para: {', '.join(faltando)}"
            )
        faltando_gates = [nome for nome in ("a", "b") if nome not in self.gates]
        if faltando_gates:
            raise ErroDeConfiguracao(
                f"[gates] sem entrada para: {', '.join(faltando_gates)}"
            )

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
            problemas.append(
                f"projeto de testes não encontrado: {self.caminhos.projeto_testes}"
            )
        if exigir_backend and not self.caminhos.backend.is_dir():
            problemas.append(f"backend não encontrado: {self.caminhos.backend}")
        if problemas:
            raise ErroDeConfiguracao(
                "configuração inválida:\n" + "\n".join(f"  - {item}" for item in problemas)
            )


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
