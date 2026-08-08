"""Execução de processos externos.

Regras que valem para todo subprocess deste projeto (aprendidas no Windows):

* sempre **lista de argumentos**, nunca `shell=True` com string montada — separador
  de caminho e aspas quebram de formas difíceis de diagnosticar;
* `stdout` e `stderr` capturados **separadamente** — o `validar-suite-gerada.mjs`
  escreve no stdout quando aprova e no stderr quando reprova, então quem lê só
  stdout enxerga reprovação como saída vazia;
* `encoding="utf-8"` explícito — a saída dos scripts tem acentuação;
* `.cmd`/`.bat` (npx, npm, prettier) resolvidos por `shutil.which` antes da chamada;
* `env` **sempre explícito** — veja `montar_ambiente`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


from orquestrador.excecoes import ErroDeFerramenta, ExecutavelAusente

__all__ = [
    "PREFIXOS_PROIBIDOS",
    "VARIAVEIS_BASE",
    "VARIAVEIS_DO_CYPRESS",
    "SaidaProcesso",
    "executar",
    "executar_node",
    "montar_ambiente",
    "resolver_executavel",
]


# ---------------------------------------------------------------------------
# Ambiente dos subprocessos
# ---------------------------------------------------------------------------
#
# Sem `env=`, o `subprocess.run` herda o ambiente **inteiro** do processo pai — e o
# processo pai tem a chave do provedor. Ela chegaria ao Node, ao Cypress, ao
# prettier e ao eslint, que executam código do repositório do cliente: plugin de
# Cypress, `cypress.config.js`, configuração de eslint e hook de npm são código
# arbitrário de terceiro rodando com a nossa credencial no ambiente.
#
# Por isso o ambiente é montado por **allowlist**: o que não estiver nomeado aqui
# não sai deste processo. A lista abaixo é o mínimo para Node e Cypress subirem no
# Windows; cada entrada tem o motivo ao lado, porque a tentação de "só herdar tudo"
# volta na primeira vez que algo não roda.

VARIAVEIS_BASE: tuple[str, ...] = (
    # -- achar e lançar o executável --
    "PATH",  # onde node, npx e as DLLs que eles carregam são procurados
    "PATHEXT",  # sem ela o Windows não reconhece .cmd/.bat como executável
    "COMSPEC",  # npx, prettier e eslint são wrappers .cmd, lançados via cmd.exe
    # -- o Node não inicia sem isto no Windows --
    "SystemRoot",  # ws2_32/bcrypt são carregadas daqui; sem ela o Node aborta
    "SystemDrive",  # usada para resolver caminho absoluto sem letra de unidade
    "windir",  # alguns utilitários procuram por este nome em vez de SystemRoot
    # -- escrita temporária: npm, prettier e Cypress todos precisam --
    "TEMP",
    "TMP",
    # -- perfil do usuário: .npmrc, cache do npm e o binário do Cypress --
    "USERPROFILE",  # raiz do "~" no Windows
    "HOME",  # o equivalente POSIX, para quando não estivermos no Windows
    "APPDATA",  # configuração e cache global do npm
    "LOCALAPPDATA",  # o binário do Cypress vive em %LOCALAPPDATA%\Cypress\Cache
    # -- descoberta de instalação (navegador do Cypress, certificados) --
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramData",
    "CommonProgramFiles",
    # -- decisões que o Node toma na partida --
    "NUMBER_OF_PROCESSORS",  # os.cpus() e o dimensionamento do thread pool do libuv
    "PROCESSOR_ARCHITECTURE",  # escolha do binário nativo (x64 × arm64)
    "OS",  # scripts .mjs que testam Windows_NT
    # -- codificação: a saída dos scripts da skill tem acentuação --
    "LANG",
    "LC_ALL",
)

# Para o pipeline passar em `variaveis_extras` quando for rodar o Cypress: tanto a
# skill quanto o `cypress.config.js` do cliente recebem configuração por `CYPRESS_*`,
# e `CI` muda o reporter. Fica fora da base porque só o Bloco 3 precisa disso — o
# validador e os formatadores não têm por que enxergar a configuração do runner.
VARIAVEIS_DO_CYPRESS: tuple[str, ...] = ("CYPRESS_*", "CI")

# Rede de segurança sobre a allowlist. Uma allowlist já exclui estas por construção;
# o filtro existe porque `variaveis_extras` é aberto, e um prefixo largo demais
# configurado no futuro reintroduziria a chave em silêncio — exatamente o defeito
# que este módulo passou a impedir.
PREFIXOS_PROIBIDOS: tuple[str, ...] = (
    "OPENROUTER_",
    "OPENAI_",
    "ANTHROPIC_",
    "LANGCHAIN_",
    "LANGSMITH_",
)


def _normalizar(nome: str) -> str:
    """Nome de variável comparável: o ambiente do Windows ignora a caixa."""
    return nome.upper() if os.name == "nt" else nome


def montar_ambiente(variaveis_extras: Sequence[str] | None = None) -> dict[str, str]:
    """Ambiente mínimo do subprocesso, montado por allowlist a partir do nosso.

    `variaveis_extras` aceita nome exato ou **prefixo** terminado em `*`
    (`"CYPRESS_*"`), e é como o Bloco 3 entrega a configuração do runner sem que
    ela vaze para os gates. Variável ausente do nosso ambiente é simplesmente
    omitida: repassar string vazia faria o Node tratar como "definida e vazia".
    """
    permitidas = [*VARIAVEIS_BASE, *(variaveis_extras or ())]
    exatas = {_normalizar(nome) for nome in permitidas if not nome.endswith("*")}
    prefixos = tuple(_normalizar(nome[:-1]) for nome in permitidas if nome.endswith("*"))

    ambiente: dict[str, str] = {}
    for nome, valor in os.environ.items():
        chave = _normalizar(nome)
        # A comparação da denylist é sempre em maiúsculas, inclusive fora do Windows:
        # o nome convencional é maiúsculo, e aqui errar para o lado de bloquear custa
        # uma variável a menos, não uma credencial a mais.
        if nome.upper().startswith(PREFIXOS_PROIBIDOS):
            continue
        if chave in exatas or (prefixos and chave.startswith(prefixos)):
            ambiente[nome] = valor
    return ambiente


@dataclass(frozen=True)
class SaidaProcesso:
    argv: list[str]
    codigo: int
    stdout: str
    stderr: str
    duracao_s: float
    cwd: str | None = None
    campos_extras: dict[str, str] = field(default_factory=dict)

    @property
    def texto(self) -> str:
        """stdout e stderr juntos, para log e para `saida_bruta` do gate."""
        partes = [parte for parte in (self.stdout.strip(), self.stderr.strip()) if parte]
        return "\n".join(partes)

    @property
    def comando(self) -> str:
        return " ".join(self.argv)


def resolver_executavel(nome: str) -> str:
    """Caminho completo do executável, tolerando .cmd/.bat/.exe do Windows."""
    caminho = Path(nome)
    if caminho.is_file():
        return str(caminho)
    encontrado = shutil.which(nome)
    if encontrado:
        return encontrado
    raise ExecutavelAusente(
        f'executável não encontrado no PATH: "{nome}". '
        "Instale-o ou ajuste [execucao] na configuração."
    )


def executar(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    timeout_s: int = 600,
    resolver: bool = True,
    variaveis_extras: Sequence[str] | None = None,
) -> SaidaProcesso:
    """Roda um comando e devolve stdout, stderr e código de saída.

    O ambiente é o de `montar_ambiente`, nunca o herdado: veja o porquê no topo do
    módulo. `variaveis_extras` amplia a allowlist para esta chamada — é por onde o
    Cypress recebe `VARIAVEIS_DO_CYPRESS`.
    """
    if not argv:
        raise ErroDeFerramenta("comando vazio")
    comando = list(argv)
    if resolver:
        comando[0] = resolver_executavel(comando[0])

    inicio = time.perf_counter()
    try:
        concluido = subprocess.run(  # noqa: S603 - lista de argumentos, sem shell
            comando,
            cwd=str(cwd) if cwd else None,
            env=montar_ambiente(variaveis_extras),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=False,
            check=False,
        )
    except FileNotFoundError as erro:
        raise ExecutavelAusente(f"não foi possível executar {comando[0]}: {erro}") from erro
    except subprocess.TimeoutExpired as erro:
        raise ErroDeFerramenta(
            f"tempo esgotado ({timeout_s}s) em: {' '.join(argv)}"
        ) from erro

    return SaidaProcesso(
        argv=argv,
        codigo=concluido.returncode,
        stdout=concluido.stdout or "",
        stderr=concluido.stderr or "",
        duracao_s=time.perf_counter() - inicio,
        cwd=str(cwd) if cwd else None,
    )


def executar_node(
    script: Path,
    argumentos: list[str],
    *,
    node: str = "node",
    cwd: Path | str | None = None,
    timeout_s: int = 600,
    variaveis_extras: Sequence[str] | None = None,
) -> SaidaProcesso:
    """Invoca um script `.mjs` da skill."""
    if not script.is_file():
        raise ErroDeFerramenta(f"script não encontrado: {script}")
    return executar(
        [node, str(script), *argumentos],
        cwd=cwd,
        timeout_s=timeout_s,
        variaveis_extras=variaveis_extras,
    )
