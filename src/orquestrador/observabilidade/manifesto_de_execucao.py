"""O que era verdade na máquina quando esta execução rodou.

Fronteira: este módulo **sonda o ambiente, redige e escreve um JSON**. Ele não
decide nada, não valida nada e não interrompe execução nenhuma — nem quando uma
sonda falha, nem quando o destino não pode ser escrito. Quem o chama é `cli.py`,
que é dono da composição da execução.

O problema que ele resolve: hoje "ontem passou, hoje falhou" não tem resposta. O
`execucao.jsonl` diz o que o pipeline fez; ele não diz em cima de qual commit do
backend, com qual versão de Node, com quais prompts. `manifesto-execucao.json`
fecha essa lacuna — é o cabeçalho de um chamado de suporte.

Três invariantes, e nenhuma delas é negociável
----------------------------------------------
1. **Segredo nunca entra.** O nome da variável de ambiente da chave entra (é
   configuração); o valor, não — nem mascarado, porque máscara reversível é
   segredo com passo a mais. Além da redação por nome de campo, o texto final é
   varrido atrás do valor real da chave, que é a rede que pega o caminho que
   ninguém previu.
2. **Código-fonte nunca entra.** De arquivo sai hash, nunca conteúdo. Um
   diagnóstico que carrega o código do cliente é um vazamento com outro nome.
3. **Sonda que falha não derruba a execução.** Backend que não é checkout Git,
   `node` fora do PATH, diretório de prompts inexistente: o campo fica ausente
   **com o motivo registrado** em `campos_ausentes`, e o manifesto sai assim
   mesmo. Diagnóstico incompleto é ruim; diagnóstico que impede o trabalho é pior.

Sobre `schema_version`: incremente quando a forma de um campo mudar ou um campo
sair. Campo novo é compatível e não exige incremento.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as versao_instalada
from itertools import pairwise
from pathlib import Path
from typing import Any

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeFerramenta
from orquestrador.ferramentas.processo import executar
from orquestrador.raiz import RAIZ_PROJETO

__all__ = [
    "ESQUEMA_DO_MANIFESTO",
    "NOME_DO_MANIFESTO",
    "REDIGIDO",
    "coletar",
    "escrever",
    "redigir",
]

ESQUEMA_DO_MANIFESTO = 1
NOME_DO_MANIFESTO = "manifesto-execucao.json"

REDIGIDO = "[redigido]"

# Sondar o ambiente não pode custar minutos: `git` num repositório grande e `node`
# numa máquina fria são rápidos, e se não forem o campo fica ausente com motivo.
_TEMPO_LIMITE_DA_SONDA_S = 30

# Palavra que, sozinha num segmento do nome do campo, denuncia credencial.
#
# Comparação por **segmento**, e não substring: um `re.search("token")` redigia
# `max_tokens`, que é limite de geração e nada tem de secreto — e um manifesto que
# apaga configuração inofensiva ensina o leitor a ignorar `[redigido]`, que é como
# se perde a redação que importa. `token` no singular é credencial; `tokens` no
# plural é contagem de uso do modelo, e a distinção é exatamente essa.
_SEGMENTOS_DE_SEGREDO = frozenset(
    {
        "auth",
        "authorization",
        "apikey",
        "chave",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "secrets",
        "segredo",
        "senha",
        "token",
    }
)

# Par de segmentos adjacentes que só junto significa credencial. `key` sozinho
# aparece em campo legítimo demais para entrar na lista acima.
_PARES_DE_SEGREDO = frozenset({("api", "key"), ("secret", "key"), ("private", "key")})

_SEPARADOR_DE_SEGMENTO = re.compile(r"[^a-z0-9]+")

# Sufixo que marca **nome de variável de ambiente**, não valor. `api_key_env` casa
# com o par acima e precisa sobreviver: sem ele o manifesto não responde "de onde a
# chave deveria ter vindo?", que é metade dos chamados de autenticação.
_SUFIXO_DE_NOME_DE_VARIAVEL = "_env"

# Abaixo disto, procurar o valor da chave dentro do texto é procurar uma coincidência:
# uma "chave" de quatro caracteres casaria com pedaço de caminho e o manifesto sairia
# corrompido. Chave de provedor real tem dezenas de caracteres.
_MENOR_SEGREDO_BUSCAVEL = 12


class _Ausencias:
    """Acumula "este campo não pôde ser coletado, e por quê".

    Existe para que cada sonda tenha um lugar óbvio para registrar a falha em vez
    de propagá-la. Um `except` que só engole a exceção produz manifesto que mente
    por omissão: quem lê não distingue "não é repositório Git" de "esqueci de
    coletar".
    """

    def __init__(self) -> None:
        self.itens: dict[str, str] = {}

    def registrar(self, campo: str, motivo: str) -> None:
        self.itens[campo] = motivo

    def por_falha(self, campo: str, erro: BaseException) -> None:
        self.registrar(campo, f"{type(erro).__name__}: {erro}")


def _hash_do_arquivo(arquivo: Path) -> str:
    resumo = hashlib.sha256()
    with arquivo.open("rb") as fluxo:
        for bloco in iter(lambda: fluxo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _hashes(raiz: Path, padrao: str, campo: str, ausencias: _Ausencias) -> dict[str, str] | None:
    """`{caminho relativo: sha256}` de tudo que casa com `padrao` sob `raiz`.

    Hash, e nunca conteúdo: é o que responde "o prompt mudou entre ontem e hoje?"
    sem transformar o manifesto num despejo do código de quem nos contratou.
    """
    try:
        if not raiz.is_dir():
            ausencias.registrar(campo, f"diretório não encontrado: {raiz}")
            return None
        return {
            arquivo.relative_to(raiz).as_posix(): _hash_do_arquivo(arquivo)
            for arquivo in sorted(raiz.rglob(padrao))
            if arquivo.is_file()
        }
    except OSError as erro:
        ausencias.por_falha(campo, erro)
        return None


def _git(argumentos: list[str], caminho: Path) -> str:
    """Roda `git` no diretório e devolve o stdout, ou levanta `ErroDeFerramenta`.

    Passa por `ferramentas/processo.executar` e não por `subprocess` direto porque
    é lá que o ambiente do subprocesso é montado por allowlist. Um manifesto que
    diagnostica vazamento de chave enquanto exporta a chave para o `git` seria
    piada de mau gosto.
    """
    saida = executar(["git", *argumentos], cwd=caminho, timeout_s=_TEMPO_LIMITE_DA_SONDA_S)
    if saida.codigo != 0:
        raise ErroDeFerramenta(f"git {' '.join(argumentos)} saiu com código {saida.codigo}")
    return saida.stdout.strip()


def _repositorio(caminho: Path, campo: str, ausencias: _Ausencias) -> dict[str, Any]:
    """Commit, ramo e estado sujo de um checkout Git.

    Devolve sempre o caminho, mesmo quando o resto falha: saber **qual diretório**
    foi usado já é metade da reprodução, e essa parte nunca depende do `git`.

    `estado_sujo` é a informação que mais falta num chamado: um commit limpo é
    reproduzível por qualquer um, e um commit com árvore suja não é reproduzível
    por ninguém — inclusive por quem rodou.

    `raiz_do_repositorio` existe porque `git` responde sobre o repositório que
    **contém** o diretório, não sobre o diretório. Um backend copiado para dentro
    de outro checkout — o sandbox do `--dry-run` é exatamente isso — devolveria o
    commit do repositório de fora como se fosse dele. Registrar a raiz deixa a
    confusão visível em vez de silenciosa.
    """
    dados: dict[str, Any] = {"caminho": str(caminho)}
    try:
        dados["commit"] = _git(["rev-parse", "HEAD"], caminho)
        dados["ramo"] = _git(["rev-parse", "--abbrev-ref", "HEAD"], caminho)
        dados["raiz_do_repositorio"] = _git(["rev-parse", "--show-toplevel"], caminho)
        dados["estado_sujo"] = bool(_git(["status", "--porcelain"], caminho))
    except (ErroDeFerramenta, OSError) as erro:
        # Backend que não é checkout Git é o caso normal, não a exceção: muito
        # projeto chega como diretório copiado. O manifesto diz isso e segue.
        ausencias.por_falha(campo, erro)
    return dados


def _versao_do_orquestrador(ausencias: _Ausencias) -> str | None:
    try:
        return versao_instalada("orquestrador")
    except PackageNotFoundError as erro:
        ausencias.por_falha("orquestrador.versao", erro)
        return None


def _versao_do_node(config: Config, ausencias: _Ausencias) -> str | None:
    try:
        return executar(
            [config.execucao.node, "--version"], timeout_s=_TEMPO_LIMITE_DA_SONDA_S
        ).stdout.strip()
    except (ErroDeFerramenta, OSError) as erro:
        ausencias.por_falha("ambiente.node", erro)
        return None


def _impressao_da_skill(config: Config, ausencias: _Ausencias) -> str | None:
    try:
        return config.impressao_da_skill()
    except (OSError, ValueError) as erro:
        ausencias.por_falha("skill.impressao", erro)
        return None


def _e_nome_de_segredo(chave: str) -> bool:
    """O nome deste campo denuncia credencial?

    Segmenta em vez de procurar substring pelo motivo explicado em
    `_SEGMENTOS_DE_SEGREDO`: `max_tokens` não é segredo, `token` é.
    """
    nome = chave.lower()
    if nome.endswith(_SUFIXO_DE_NOME_DE_VARIAVEL):
        return False
    segmentos = [parte for parte in _SEPARADOR_DE_SEGMENTO.split(nome) if parte]
    if any(parte in _SEGMENTOS_DE_SEGREDO for parte in segmentos):
        return True
    return any(par in _PARES_DE_SEGREDO for par in pairwise(segmentos))


def redigir(valor: Any, *, chave: str = "") -> Any:
    """Copia a estrutura substituindo por `[redigido]` o que o nome denuncia.

    Redação por **nome do campo**, e não por formato do valor: heurística sobre o
    valor ("parece uma chave de API?") erra nos dois sentidos, e errar deixando
    passar é o sentido caro. Campo terminado em `_env` é nome de variável de
    ambiente, não valor, e é justamente o que se quer ler no manifesto.
    """
    if isinstance(valor, dict):
        itens: dict[str, Any] = valor  # pyright: ignore[reportUnknownVariableType]
        return {str(nome): redigir(item, chave=str(nome)) for nome, item in itens.items()}
    if isinstance(valor, (list, tuple)):
        sequencia: list[Any] = list(valor)  # pyright: ignore[reportUnknownArgumentType]
        return [redigir(item, chave=chave) for item in sequencia]
    if chave and _e_nome_de_segredo(chave):
        return REDIGIDO
    return valor


def _apagar_segredo_residual(manifesto: dict[str, Any], config: Config) -> None:
    """Rede de segurança: apaga o valor real da chave caso ele tenha entrado.

    A redação por nome cobre o que se conhece. Esta varredura cobre o que não se
    conhece — um campo novo, uma configuração que alguém apontou para o lugar
    errado, um caminho de arquivo que inclui a credencial. Ela lê a variável de
    ambiente apenas para **comparar**; o valor não é registrado em lugar nenhum.

    A varredura é sobre o JSON serializado, e não sobre a árvore, porque é o texto
    que vai para o disco e é o texto que alguém anexa a um chamado — uma chave
    escondida numa chave de dicionário escaparia de uma varredura só sobre valores.

    Só faz sentido para segredo longo o bastante: procurar uma string curta dentro
    do JSON acha coincidência e corrompe o manifesto sem motivo.
    """
    bruto = os.environ.get(config.openrouter.api_key_env, "").strip()
    if len(bruto) < _MENOR_SEGREDO_BUSCAVEL:
        return
    if bruto not in _serializar(manifesto):
        return
    limpo: dict[str, Any] = json.loads(_serializar(manifesto).replace(bruto, REDIGIDO))
    manifesto.clear()
    manifesto.update(limpo)
    manifesto["campos_ausentes"]["seguranca.varredura"] = (
        f"o valor de {config.openrouter.api_key_env} apareceu no manifesto e foi apagado. "
        "Isto é defeito do coletor, não do ambiente: descubra qual campo o trouxe."
    )


def _serializar(manifesto: dict[str, Any]) -> str:
    # `default=str` porque uma sonda futura pode devolver `Path` ou `datetime`, e o
    # manifesto não pode deixar de ser escrito por causa de um tipo não previsto.
    return json.dumps(manifesto, ensure_ascii=False, indent=2, default=str) + "\n"


def coletar(
    config: Config,
    *,
    run_id: str,
    dry_run: bool,
    recursos: list[str],
    dir_artefatos: Path | None = None,
) -> dict[str, Any]:
    """Monta o manifesto. Não levanta: falha de sonda vira `campos_ausentes`.

    `dir_artefatos` é opcional porque o manifesto é escrito **duas** vezes: uma no
    começo, quando ainda não há artefato nenhum e o que importa é existir mesmo se
    a execução morrer no meio; outra no fim, com os hashes. A primeira versão é a
    que responde ao chamado de suporte de uma execução que nunca terminou.
    """
    ausencias = _Ausencias()

    manifesto: dict[str, Any] = {
        "schema_version": ESQUEMA_DO_MANIFESTO,
        "run_id": run_id,
        "dry_run": dry_run,
        "recursos": list(recursos),
        "orquestrador": {"versao": _versao_do_orquestrador(ausencias)},
        "ambiente": {
            "python": platform.python_version(),
            "python_implementacao": platform.python_implementation(),
            # `sys.version_info` completo distingue um 3.13.0rc de um 3.13.0 final,
            # que já foi diferença de comportamento em `enum` e em `pathlib`.
            "python_completo": sys.version.replace("\n", " "),
            "node": _versao_do_node(config, ausencias),
            "sistema": platform.system(),
            "release": platform.release(),
            "arquitetura": platform.machine(),
        },
        "repositorios": {
            "orquestrador": _repositorio(RAIZ_PROJETO, "repositorios.orquestrador", ausencias),
            "backend": _repositorio(config.caminhos.backend, "repositorios.backend", ausencias),
        },
        "skill": {
            "caminho": str(config.caminhos.skill),
            "impressao": _impressao_da_skill(config, ausencias),
            "impressao_esperada": config.skill.impressao_esperada or None,
        },
        "modelos_por_estagio": {
            nome: {
                "modelo": estagio.modelo,
                "temperatura": estagio.temperatura,
                "max_tokens": estagio.max_tokens,
                "modo_estruturado": estagio.modo_estruturado,
            }
            for nome, estagio in config.estagios.items()
        },
        "configuracao": redigir(config.model_dump(mode="json")),
        "hashes": {
            "prompts": _hashes(config.caminhos.prompts, "*.md", "hashes.prompts", ausencias),
        },
    }

    if dir_artefatos is not None:
        manifesto["hashes"]["artefatos"] = _hashes(
            dir_artefatos, "*", "hashes.artefatos", ausencias
        )

    manifesto["campos_ausentes"] = ausencias.itens
    _apagar_segredo_residual(manifesto, config)
    return manifesto


def escrever(
    config: Config,
    destino: Path,
    *,
    run_id: str,
    dry_run: bool,
    recursos: list[str],
    dir_artefatos: Path | None = None,
) -> dict[str, Any]:
    """Escreve o manifesto em `destino` e devolve exatamente o que foi escrito.

    Falha de escrita entra em `campos_ausentes` do valor devolvido em vez de
    subir: quem chama está montando uma execução, não um relatório, e uma
    execução não pode morrer por não conseguir escrever o diagnóstico dela.
    """
    manifesto = coletar(
        config,
        run_id=run_id,
        dry_run=dry_run,
        recursos=recursos,
        dir_artefatos=dir_artefatos,
    )
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(_serializar(manifesto), encoding="utf-8", newline="\n")
    except OSError as erro:
        manifesto["campos_ausentes"][f"escrita[{destino}]"] = f"{type(erro).__name__}: {erro}"
    return manifesto
