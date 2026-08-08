"""Leitura de arquivos com confinamento de caminho.

`ler_arquivo`, `listar_diretorio` e `buscar_no_backend` recebem caminho vindo do
LLM. Nada é aberto no caminho cru: tudo passa por `Confinamento.resolver`, que
canoniza (resolvendo `..`, symlink e junction) e confere que o resultado continua
sob a raiz autorizada. No Windows a comparação ignora a caixa, porque o sistema de
arquivos ignora — comparar sensível a maiúsculas deixaria passar `C:\\BACKEND\\...`
como se fosse outra raiz.

Este módulo é o **dono da regra de confinamento**, e vale também para escrita: o
executor grava os `.cy.js` por `confinar`, em vez de comparar texto por conta
própria. Duas implementações da mesma regra divergem, e a que diverge é a que
aceita o caminho que deveria recusar.

Confinamento não é privacidade
------------------------------
As duas perguntas são diferentes e as duas valem. "Este caminho escapa da raiz
autorizada?" é daqui. "Este arquivo pode ser enviado a um provedor de LLM?" é de
`ferramentas/privacidade.py`, e se aplica **depois** — sobre caminho que o
confinamento já aprovou. Um `.env` no meio do backend está perfeitamente dentro
da raiz; o que o impede de ser lido é a política, não o confinamento.

As três operações abaixo consultam a política porque o que elas devolvem vai
direto para o prompt. `Confinamento` sozinho continua servindo a quem só precisa
resolver caminho — a escrita do executor e a publicação —, e para esses a
política é inerte: ela só é construída quando alguém a pede.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from orquestrador.ferramentas.privacidade import PoliticaDePrivacidade

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


def sob_a_raiz(alvo: Path | str, raiz: Path | str) -> bool:
    """`alvo` está dentro de `raiz` depois de canonizar os dois?

    Esta é a **única** implementação da regra no projeto; quem precisar confinar
    caminho chama daqui. Comparar texto (`str(alvo).startswith(str(raiz))`) parece
    equivalente e não é: `.../pedidos-antigos` começa com `.../pedidos` e passaria
    como se estivesse dentro dele. `is_relative_to` compara **componente a
    componente**, então o irmão de prefixo comum é recusado.

    `resolve()` nos dois lados antes da comparação, porque a fuga interessante não é
    o `..` literal (o contrato já o recusa): é o symlink ou a junction do Windows
    dentro da raiz apontando para fora dela.

    Caixa: `Path` no Windows compara ignorando maiúsculas — que é o que o sistema de
    arquivos faz — e no POSIX compara sensível, que também é o que o sistema de
    arquivos faz. Nada a normalizar à mão aqui.
    """
    try:
        canonico = Path(alvo).resolve(strict=False)
        return canonico.is_relative_to(Path(raiz).resolve(strict=False))
    except (OSError, ValueError):  # nome inválido, caminho longo demais
        return False


def relativo_a(alvo: Path, base: Path) -> str:
    """`alvo` visto a partir de `base`, em POSIX; o caminho inteiro se não couber.

    Só desce: quando `alvo` está **fora** de `base`, devolve o caminho absoluto em
    vez de subir com `../`. Para subir — o caso do import de um módulo
    compartilhado, que nunca está abaixo do recurso — use
    `analise_estatica.extrator_de_superficie.caminho_de_import`, que usa
    `os.path.relpath`.
    """
    try:
        return alvo.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return alvo.as_posix()


class Confinamento:
    """Resolve caminhos relativos a uma raiz e recusa qualquer fuga dela."""

    def __init__(self, raiz: Path | str, *, politica: PoliticaDePrivacidade | None = None) -> None:
        self.raiz = Path(raiz).expanduser().resolve()
        self._politica = politica

    @property
    def politica(self) -> PoliticaDePrivacidade:
        """A política de privacidade desta raiz, construída na primeira consulta.

        Preguiçosa porque `confinar()` cria um `Confinamento` descartável a cada
        arquivo que o executor grava, e construir a política lê o `.llmignore` do
        disco. Quem só resolve caminho nunca paga por isso.
        """
        if self._politica is None:
            self._politica = PoliticaDePrivacidade(self.raiz)
        return self._politica

    def resolver(self, caminho: str | Path) -> Path:
        bruto = Path(str(caminho).strip().strip('"').strip("'"))
        try:
            alvo = bruto if bruto.is_absolute() else (self.raiz / bruto)
            alvo = alvo.resolve(strict=False)
        except (OSError, ValueError) as erro:  # nome inválido, caminho longo demais
            raise CaminhoForaDaRaiz(f"caminho inválido: {caminho!r} ({erro})") from erro
        if not self._sob_a_raiz(alvo):
            raise CaminhoForaDaRaiz(f"caminho fora da raiz autorizada ({self.raiz}): {caminho!r}")
        return alvo

    def _sob_a_raiz(self, alvo: Path) -> bool:
        return sob_a_raiz(alvo, self.raiz)

    def relativo(self, alvo: Path) -> str:
        """Caminho para exibição, sempre relativo à raiz confinada."""
        return relativo_a(alvo, self.raiz)


def confinar(raiz: Path | str, caminho: str | Path) -> Path:
    """Resolve `caminho` sob `raiz` e recusa qualquer coisa que escape dela.

    Atalho para quem confina um punhado de caminhos e não guarda a raiz — o
    executor, ao gravar os arquivos que o modelo emitiu. Quem faz muitas resoluções
    contra a mesma raiz constrói um `Confinamento`, que a canoniza uma vez só.
    """
    return Confinamento(raiz).resolver(caminho)


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
    # Antes de `exists()`: a resposta para um `.env` recusado não deve depender
    # de ele existir. "não existe" e "existe mas é segredo" são a mesma frase aqui.
    if (motivo := confinamento.politica.motivo_de_recusa(alvo, diretorio=False)) is not None:
        return (
            f"ERRO: {confinamento.relativo(alvo)} não é legível: a política de privacidade "
            f"o recusa ({motivo}). Nada deste arquivo é enviado ao modelo. "
            "Procure a informação no código da aplicação."
        )
    if not alvo.exists():
        return f"ERRO: arquivo não existe: {confinamento.relativo(alvo)}"
    if alvo.is_dir():
        return f"ERRO: {confinamento.relativo(alvo)} é um diretório; use listar_diretorio."
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

    # Redação sobre o arquivo inteiro, e não sobre a fatia: um bloco PEM cortado
    # ao meio pelo `offset` começaria fora da janela e a fatia sairia com o corpo
    # da chave. A redação preserva a contagem de linhas, então o `offset` que o
    # modelo pediu continua apontando para a mesma linha.
    texto, redigidos = confinamento.politica.redigir(
        alvo.read_text(encoding="utf-8", errors="replace")
    )
    linhas = texto.splitlines()
    inicio = max(0, int(offset))
    fim = min(len(linhas), inicio + max(1, int(limit)))
    if inicio >= len(linhas) and linhas:
        return (
            f"ERRO: offset {inicio} além do fim do arquivo "
            f"({len(linhas)} linhas em {confinamento.relativo(alvo)})"
        )

    corpo = "\n".join(f"{numero:6d}\t{linhas[numero - 1]}" for numero in range(inicio + 1, fim + 1))
    cabecalho = f"{confinamento.relativo(alvo)} (linhas {inicio + 1}-{fim} de {len(linhas)})"
    rodape = ""
    if fim < len(linhas):
        rodape = f"\n... truncado; continue com offset={fim}"
    if redigidos:
        rodape += (
            f"\n[política de privacidade: {redigidos} trecho(s) redigido(s) neste arquivo; "
            "o resto do conteúdo está íntegro]"
        )
    return f"{cabecalho}\n{corpo}{rodape}"


def listar_diretorio(confinamento: Confinamento, caminho: str, *, limite: int = 200) -> str:
    """Lista um diretório, marcando subdiretórios com `/`."""
    alvo = confinamento.resolver(caminho)
    if not alvo.exists():
        return f"ERRO: diretório não existe: {confinamento.relativo(alvo)}"
    if not alvo.is_dir():
        return f"ERRO: {confinamento.relativo(alvo)} não é um diretório."

    entradas: list[str] = []
    recusadas = 0
    for item in sorted(alvo.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        e_diretorio = item.is_dir()
        if e_diretorio and item.name in DIRETORIOS_IGNORADOS:
            continue
        # A entrada some da listagem em vez de aparecer marcada: um nome de
        # arquivo já é informação — `chave-producao.pem` diz de quem é a chave —,
        # e listar o que não pode ser lido só produz tentativa de leitura.
        if confinamento.politica.motivo_de_recusa(item, diretorio=e_diretorio) is not None:
            recusadas += 1
            continue
        entradas.append(f"{item.name}/" if e_diretorio else item.name)

    total = len(entradas)
    mostradas = entradas[:limite]
    cabecalho = f"{confinamento.relativo(alvo)} ({total} entrada(s))"
    rodape = f"\n... {total - len(mostradas)} entrada(s) omitida(s)" if total > limite else ""
    if recusadas:
        rodape += f"\n[política de privacidade: {recusadas} entrada(s) ocultada(s)]"
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
    redigidos = 0
    truncado = False
    for arquivo in _percorrer(confinamento, glob):
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
                # A busca casa contra a linha crua e o que sai é a linha redigida:
                # redigir o arquivo inteiro antes de procurar custaria uma
                # varredura completa por arquivo do backend, e o que precisa
                # estar limpo é a **saída**, não o casamento.
                exibida, trechos = confinamento.politica.redigir(linha)
                redigidos += trechos
                achados.append(
                    f"{confinamento.relativo(arquivo)}:{numero}: {exibida.strip()[:240]}"
                )
                if len(achados) >= max_resultados:
                    truncado = True
                    break

    rodape = ""
    if truncado:
        rodape += f"\n... truncado em {max_resultados} ocorrências; restrinja o padrão ou o glob"
    if redigidos:
        rodape += f"\n[política de privacidade: {redigidos} trecho(s) redigido(s) no resultado]"
    if not achados:
        return f"nenhuma ocorrência de {padrao!r}" + (f" em {glob}" if glob else "") + rodape
    return "\n".join(achados) + rodape


def _percorrer(confinamento: Confinamento, glob: str | None):
    """Arquivos de texto sob a raiz, pulando o que é proibido ou privado.

    A poda de diretório acontece no `subdiretorios[:]`, antes de descer: sem ela
    a busca leria `.ssh/` inteiro para recusar arquivo por arquivo — mesmo
    veredito, disco lido à toa. É também o que impede a regex do modelo de virar
    oráculo sobre o conteúdo de um diretório que a política já fechou.
    """
    politica = confinamento.politica
    for diretorio, subdiretorios, arquivos in os.walk(confinamento.raiz):
        subdiretorios[:] = [
            nome
            for nome in subdiretorios
            if nome not in DIRETORIOS_IGNORADOS
            and politica.motivo_de_recusa(Path(diretorio) / nome, diretorio=True) is None
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
            if politica.motivo_de_recusa(alvo, diretorio=False) is not None:
                continue
            yield alvo
