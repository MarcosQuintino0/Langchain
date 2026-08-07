"""Leitura de arquivos com confinamento de caminho.

`ler_arquivo`, `listar_diretorio` e `buscar_no_backend` recebem caminho vindo do
LLM. Nada é aberto no caminho cru: tudo passa por `Confinamento.resolver`, que
canoniza (resolvendo `..`, symlink e junction) e confere que o resultado continua
sob a raiz autorizada. No Windows a comparação ignora a caixa, porque o sistema de
arquivos ignora — comparar sensível a maiúsculas deixaria passar `C:\\BACKEND\\...`
como se fosse outra raiz.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Diretórios que nunca interessam à descoberta e explodem o custo de varredura.
DIRETORIOS_IGNORADOS = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        ".mvn",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "obj",
        "out",
        "target",
        "venv",
    }
)

# Nunca varrer o grafo: são dezenas de MB. Ou `graphify query`, ou leitura do código.
ARQUIVOS_PROIBIDOS = frozenset({"graph.json"})

EXTENSOES_BINARIAS = frozenset(
    {
        ".class",
        ".dll",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".so",
        ".war",
        ".zip",
    }
)


class CaminhoForaDaRaiz(PermissionError):
    """O caminho pedido escapa da raiz autorizada."""


def relativo_a(alvo: Path, base: Path) -> str:
    """`alvo` visto a partir de `base`, em POSIX; o caminho inteiro se não couber.

    Só desce: quando `alvo` está **fora** de `base`, devolve o caminho absoluto em
    vez de subir com `../`. Para subir — o caso do import de um módulo
    compartilhado, que nunca está abaixo do recurso — use
    `ferramentas.superficie.caminho_de_import`, que usa `os.path.relpath`.
    """
    try:
        return alvo.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return alvo.as_posix()


class Confinamento:
    """Resolve caminhos relativos a uma raiz e recusa qualquer fuga dela."""

    def __init__(self, raiz: Path | str) -> None:
        self.raiz = Path(raiz).expanduser().resolve()

    def resolver(self, caminho: str | Path) -> Path:
        bruto = Path(str(caminho).strip().strip('"').strip("'"))
        try:
            alvo = bruto if bruto.is_absolute() else (self.raiz / bruto)
            alvo = alvo.resolve(strict=False)
        except (OSError, ValueError) as erro:  # nome inválido, caminho longo demais
            raise CaminhoForaDaRaiz(f"caminho inválido: {caminho!r} ({erro})") from erro
        if not self._sob_a_raiz(alvo):
            raise CaminhoForaDaRaiz(
                f"caminho fora da raiz autorizada ({self.raiz}): {caminho!r}"
            )
        return alvo

    def _sob_a_raiz(self, alvo: Path) -> bool:
        raiz = os.path.normcase(str(self.raiz))
        candidato = os.path.normcase(str(alvo))
        return candidato == raiz or candidato.startswith(raiz + os.sep)

    def relativo(self, alvo: Path) -> str:
        """Caminho para exibição, sempre relativo à raiz confinada."""
        return relativo_a(alvo, self.raiz)


# ---------------------------------------------------------------------------
# Operações
# ---------------------------------------------------------------------------


def ler_arquivo(
    confinamento: Confinamento,
    caminho: str,
    offset: int = 0,
    limit: int = 500,
    *,
    max_bytes: int = 2_000_000,
) -> str:
    """Devolve um trecho numerado do arquivo, como `cat -n`."""
    alvo = confinamento.resolver(caminho)
    if not alvo.exists():
        return f"ERRO: arquivo não existe: {confinamento.relativo(alvo)}"
    if alvo.is_dir():
        return (
            f"ERRO: {confinamento.relativo(alvo)} é um diretório; "
            "use listar_diretorio."
        )
    if alvo.name in ARQUIVOS_PROIBIDOS:
        return (
            f"ERRO: {alvo.name} não pode ser lido diretamente (dezenas de MB). "
            "Use graphify_query/graphify_affected para consultar o grafo."
        )
    if alvo.suffix.lower() in EXTENSOES_BINARIAS:
        return f"ERRO: arquivo binário, não legível como texto: {confinamento.relativo(alvo)}"
    tamanho = alvo.stat().st_size
    if tamanho > max_bytes:
        return (
            f"ERRO: arquivo grande demais ({tamanho} bytes > {max_bytes}). "
            "Leia por trechos com offset/limit ou consulte o grafo."
        )

    texto = alvo.read_text(encoding="utf-8", errors="replace")
    linhas = texto.splitlines()
    inicio = max(0, int(offset))
    fim = min(len(linhas), inicio + max(1, int(limit)))
    if inicio >= len(linhas) and linhas:
        return (
            f"ERRO: offset {inicio} além do fim do arquivo "
            f"({len(linhas)} linhas em {confinamento.relativo(alvo)})"
        )

    corpo = "\n".join(
        f"{numero:6d}\t{linhas[numero - 1]}" for numero in range(inicio + 1, fim + 1)
    )
    cabecalho = f"{confinamento.relativo(alvo)} (linhas {inicio + 1}-{fim} de {len(linhas)})"
    rodape = ""
    if fim < len(linhas):
        rodape = f"\n... truncado; continue com offset={fim}"
    return f"{cabecalho}\n{corpo}{rodape}"


def listar_diretorio(confinamento: Confinamento, caminho: str, *, limite: int = 200) -> str:
    """Lista um diretório, marcando subdiretórios com `/`."""
    alvo = confinamento.resolver(caminho)
    if not alvo.exists():
        return f"ERRO: diretório não existe: {confinamento.relativo(alvo)}"
    if not alvo.is_dir():
        return f"ERRO: {confinamento.relativo(alvo)} não é um diretório."

    entradas: list[str] = []
    for item in sorted(alvo.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if item.is_dir():
            if item.name in DIRETORIOS_IGNORADOS:
                continue
            entradas.append(f"{item.name}/")
        else:
            entradas.append(item.name)

    total = len(entradas)
    mostradas = entradas[:limite]
    cabecalho = f"{confinamento.relativo(alvo)} ({total} entrada(s))"
    rodape = f"\n... {total - len(mostradas)} entrada(s) omitida(s)" if total > limite else ""
    return "\n".join([cabecalho, *mostradas]) + rodape


def buscar(
    confinamento: Confinamento,
    padrao: str,
    glob: str | None = None,
    *,
    max_resultados: int = 40,
    max_bytes: int = 2_000_000,
) -> str:
    """Busca por expressão regular no conteúdo dos arquivos sob a raiz.

    Último recurso: o que o grafo responde, pergunta-se ao grafo.
    """
    try:
        regex = re.compile(padrao)
    except re.error as erro:
        return f"ERRO: expressão regular inválida ({erro}): {padrao!r}"

    achados: list[str] = []
    truncado = False
    for arquivo in _percorrer(confinamento.raiz, glob):
        if len(achados) >= max_resultados:
            truncado = True
            break
        try:
            if arquivo.stat().st_size > max_bytes:
                continue
            conteudo = arquivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for numero, linha in enumerate(conteudo.splitlines(), start=1):
            if regex.search(linha):
                achados.append(
                    f"{confinamento.relativo(arquivo)}:{numero}: {linha.strip()[:240]}"
                )
                if len(achados) >= max_resultados:
                    truncado = True
                    break

    if not achados:
        return f"nenhuma ocorrência de {padrao!r}" + (f" em {glob}" if glob else "")
    rodape = (
        f"\n... truncado em {max_resultados} ocorrências; restrinja o padrão ou o glob"
        if truncado
        else ""
    )
    return "\n".join(achados) + rodape


def _percorrer(raiz: Path, glob: str | None):
    """Arquivos de texto sob a raiz, pulando diretórios e arquivos proibidos."""
    for diretorio, subdiretorios, arquivos in os.walk(raiz):
        subdiretorios[:] = [
            nome for nome in subdiretorios if nome not in DIRETORIOS_IGNORADOS
        ]
        base = Path(diretorio)
        for nome in arquivos:
            if nome in ARQUIVOS_PROIBIDOS:
                continue
            alvo = base / nome
            if alvo.suffix.lower() in EXTENSOES_BINARIAS:
                continue
            if glob and not alvo.match(glob):
                continue
            yield alvo
