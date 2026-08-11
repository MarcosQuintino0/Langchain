"""Que integração contínua este projeto já usa? — detecção por marcador.

Entra a raiz do projeto de testes, sai a lista de plataformas que ele declara. É
a pergunta que decide se geramos pipeline: quem não tem CI não pediu CI, e
depositar um `.yml` num repositório que nunca rodou nada é palpite, não entrega.

Por que a detecção sobe até o `.git`
------------------------------------
Projeto Cypress em subpasta de monorepo é o arranjo comum, e nesse arranjo o
`.github/` mora acima. Procurar só na raiz do projeto de testes responderia "não
tem CI" para a maioria dos casos em que tem. A subida para no diretório que
contém `.git` — é a fronteira do repositório, e além dela o que se acha é de
outro projeto.

Por que a tabela vive aqui e não no gerador
-------------------------------------------
Marcador, destino de arquivo novo e forma de inclusão mudam juntos, pelo mesmo
motivo: a convenção da plataforma. Separá-los em dois módulos faria a metade que
detecta e a metade que escreve envelhecerem em ritmos diferentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

LIMITE_DE_SUBIDA = 6


class Geracao(Enum):
    """O que se pode fazer por esta plataforma sem tocar em arquivo alheio."""

    # A plataforma descobre arquivo novo sozinha: escrevemos direto no projeto.
    NO_PROJETO = "no_projeto"
    # A plataforma exige que o arquivo dela aponte para o nosso: escrevemos a
    # sugestão no diretório da execução e dizemos que linha acrescentar.
    SUGESTAO = "sugestao"
    # A configuração não é YAML: gerar seria escrever Groovy ou Kotlin no escuro.
    NAO_GERA = "nao_gera"


@dataclass(frozen=True)
class Plataforma:
    chave: str
    nome: str
    marcadores: tuple[str, ...]
    geracao: Geracao
    # Onde o arquivo novo mora, relativo à raiz onde o marcador foi achado.
    destino: str = ""
    # A linha que o dono do projeto precisa acrescentar ao arquivo dele.
    inclusao: str = ""
    # Por que não geramos, quando não geramos. Vai inteiro para o relatório.
    motivo: str = ""


PLATAFORMAS: tuple[Plataforma, ...] = (
    Plataforma(
        chave="github",
        nome="GitHub Actions",
        marcadores=(".github/workflows",),
        geracao=Geracao.NO_PROJETO,
        destino=".github/workflows/testes-de-api.yml",
    ),
    Plataforma(
        chave="gitlab",
        nome="GitLab CI",
        marcadores=(".gitlab-ci.yml",),
        geracao=Geracao.SUGESTAO,
        destino="testes-de-api.gitlab-ci.yml",
        inclusao="include:\n  - local: .gitlab/ci/testes-de-api.yml",
    ),
    Plataforma(
        chave="azure",
        nome="Azure Pipelines",
        marcadores=("azure-pipelines.yml",),
        geracao=Geracao.SUGESTAO,
        destino="testes-de-api.azure.yml",
        inclusao="- template: testes-de-api.azure.yml",
    ),
    Plataforma(
        chave="bitbucket",
        nome="Bitbucket Pipelines",
        marcadores=("bitbucket-pipelines.yml",),
        geracao=Geracao.SUGESTAO,
        destino="testes-de-api.bitbucket.yml",
        inclusao="(o Bitbucket lê só o bitbucket-pipelines.yml da raiz: "
        "copie o passo para dentro dele)",
    ),
    Plataforma(
        chave="circleci",
        nome="CircleCI",
        marcadores=(".circleci/config.yml",),
        geracao=Geracao.SUGESTAO,
        destino="testes-de-api.circleci.yml",
        inclusao="(o CircleCI lê só o .circleci/config.yml: copie o job para dentro dele)",
    ),
    Plataforma(
        chave="jenkins",
        nome="Jenkins",
        marcadores=("Jenkinsfile",),
        geracao=Geracao.NAO_GERA,
        motivo="o Jenkinsfile é Groovy, não YAML: gerar seria escrever numa "
        "linguagem que este projeto não lê nem verifica",
    ),
    Plataforma(
        chave="teamcity",
        nome="TeamCity",
        marcadores=(".teamcity",),
        geracao=Geracao.NAO_GERA,
        motivo="a configuração do TeamCity é Kotlin DSL, não YAML: gerar seria "
        "escrever numa linguagem que este projeto não lê nem verifica",
    ),
)


@dataclass(frozen=True)
class CiDoProjeto:
    """O que se achou, e onde."""

    plataformas: tuple[Plataforma, ...]
    raiz: Path | None = None

    @property
    def tem_ci(self) -> bool:
        return bool(self.plataformas)


def plataformas_presentes(existe: set[str]) -> tuple[Plataforma, ...]:
    """A parte pura: dado o conjunto de caminhos presentes, quem se reconhece.

    Recebe caminhos relativos já normalizados com `/`. Existe separada de
    `detectar` para poder ser exercitada sem montar árvore de diretório — e
    porque é ela que carrega a regra, enquanto a outra carrega o passeio.
    """
    return tuple(
        plataforma
        for plataforma in PLATAFORMAS
        if any(marcador in existe for marcador in plataforma.marcadores)
    )


def detectar(projeto: Path) -> CiDoProjeto:
    """Sobe do projeto de testes até o repositório, procurando os marcadores.

    Para no primeiro diretório que tem marcador — CI de submódulo não é CI do
    repositório inteiro, e misturar os dois faria a gente escrever no lugar
    errado. Também para no `.git`, que é onde o repositório acaba.
    """
    atual = projeto.resolve()
    for _ in range(LIMITE_DE_SUBIDA):
        if not atual.is_dir():
            break
        achadas = plataformas_presentes(_nomes_visiveis(atual))
        if achadas:
            return CiDoProjeto(plataformas=achadas, raiz=atual)
        if (atual / ".git").exists() or atual.parent == atual:
            break
        atual = atual.parent
    return CiDoProjeto(plataformas=())


def _nomes_visiveis(diretorio: Path) -> set[str]:
    """Os nomes do primeiro nível, mais um nível dentro dos diretórios ocultos.

    Dois níveis bastam porque é onde todo marcador mora (`.github/workflows`,
    `.circleci/config.yml`), e descer mais varreria `node_modules` inteiro atrás
    de nada.
    """
    nomes: set[str] = set()
    try:
        itens = list(diretorio.iterdir())
    except OSError:
        return nomes
    for item in itens:
        nomes.add(item.name)
        if item.is_dir() and item.name.startswith("."):
            try:
                nomes.update(f"{item.name}/{filho.name}" for filho in item.iterdir())
            except OSError:
                continue
    return nomes
