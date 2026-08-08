"""Montagem dos prompts — a implementação do princípio 2.

    prompt_reparo = instrucao_fixa_do_estagio + artefato_atual + delta.violacoes

Nada além disso. Sem histórico de tentativas: é esse corte que troca o custo
quadrático por custo linear, e ele mora aqui, num lugar só, para não escapar por
descuido em algum estágio.

Mora em `llm/` porque o que ele produz é **entrada de modelo**: junto do cliente,
da saída estruturada e da contagem de mensagens. Não conhece gate nem recurso — o
`Delta` chega pronto, e este módulo só o renderiza.

O **conteúdo** das instruções fixas é Fase 2; aqui só a carga do arquivo e a
substituição de `{{chave}}`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orquestrador.contratos import Delta, Violacao
from orquestrador.raiz import DIR_PROMPTS_PADRAO

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class PromptAusente(FileNotFoundError):
    pass


def carregar_prompt(
    nome: str,
    dados: dict[str, Any] | None = None,
    *,
    dir_prompts: Path | None = None,
) -> str:
    """Lê `<dir_prompts>/<nome>.md` e substitui os `{{placeholders}}` conhecidos.

    O diretório vem da configuração (`[caminhos].prompts`), com padrão na raiz do
    projeto: prompt é conteúdo editorial, não código de pacote.

    Placeholder sem valor é mantido literal — o arquivo é um placeholder da Fase 1
    e apagá-lo em silêncio esconderia a lacuna.
    """
    arquivo = (dir_prompts or DIR_PROMPTS_PADRAO) / f"{nome}.md"
    if not arquivo.is_file():
        raise PromptAusente(f"prompt do estágio não encontrado: {arquivo}")
    texto = arquivo.read_text(encoding="utf-8")
    valores = dados or {}

    def substituir(casamento: re.Match[str]) -> str:
        chave = casamento.group(1)
        if chave not in valores:
            return casamento.group(0)
        return str(valores[chave])

    return _PLACEHOLDER.sub(substituir, texto)


def esquema_json(tipo: type[BaseModel]) -> str:
    """JSON Schema do contrato de saída, para colar no prompt."""
    return json.dumps(tipo.model_json_schema(by_alias=True), ensure_ascii=False, indent=2)


def montar_entrada_inicial(secoes: dict[str, str]) -> str:
    """Concatena as seções da entrada da primeira tentativa."""
    partes: list[str] = []
    for titulo, corpo in secoes.items():
        if not corpo.strip():
            continue
        partes.append(f"## {titulo}\n\n{corpo}".rstrip())
    return "\n\n".join(partes) + "\n"


def montar_entrada_reparo(artefato_atual: str, delta: Delta) -> str:
    """A entrada de uma tentativa de reparo: artefato atual + violações. Só isso."""
    return montar_entrada_inicial(
        {
            f"Artefato atual ({delta.recurso})": f"```\n{artefato_atual.strip()}\n```",
            "Violações a corrigir": delta.render(),
        }
    )


# ---------------------------------------------------------------------------
# Projeção do artefato atual
# ---------------------------------------------------------------------------

# Linhas de contexto de cada lado do ponto reclamado. Doze é o bastante para
# enxergar o `it` inteiro e o `describe` que o contém na convenção da skill.
CONTEXTO_PADRAO = 12

# Teto do texto projetado. Continua existindo — janela de modelo é finita —, mas
# deixou de ser o critério de escolha: o que entra primeiro é o trecho reclamado.
LIMITE_PADRAO = 60_000

_ELISAO = "..."


def recortar_por_violacoes(
    arquivos: dict[str, str],
    violacoes: Sequence[Violacao],
    *,
    contexto: int = CONTEXTO_PADRAO,
    limite: int = LIMITE_PADRAO,
) -> str:
    """Projeta o artefato mantendo **o que as violações apontam**, não o começo dele.

    O corte anterior era posicional: concatenava os arquivos e cortava no
    caractere 60.000. Numa suíte grande o trecho reclamado costuma cair depois do
    corte, e aí o reparo recebe uma violação sobre um código que ele não está
    vendo — a tentativa é gasta sem chance nenhuma de convergir.

    A ordem aqui é por relevância:

    1. o índice de todos os arquivos, com o número de linhas de cada um. Ele é
       curto e responde "o que existe" mesmo quando o conteúdo é elidido;
    2. os trechos apontados por violação com arquivo **e** linha, com `contexto`
       linhas de cada lado e janelas sobrepostas unidas;
    3. os arquivos apontados por violação sem linha, inteiros — a reclamação é
       sobre o arquivo, não sobre um ponto dele;
    4. só então, e só se sobrar orçamento, o resto.

    Violação sem arquivo associado (o prettier reprovando o diretório, um contador
    de cobertura) não localiza nada: ela promove o passo 4 a obrigatório, porque o
    que o modelo precisa rever é a suíte, e é justamente aí que o índice do passo 1
    impede que a elisão vire silêncio.
    """
    disponiveis = sorted(arquivos)
    if not disponiveis:
        return ""

    linhas_por_arquivo = {nome: arquivos[nome].splitlines() for nome in disponiveis}
    apontados: dict[str, set[int]] = {}
    inteiros: set[str] = set()
    sem_arquivo = False

    for violacao in violacoes:
        alvo = _resolver_arquivo(violacao.arquivo, disponiveis)
        if alvo is None:
            sem_arquivo = True
            continue
        if violacao.linha is None:
            inteiros.add(alvo)
        else:
            apontados.setdefault(alvo, set()).add(violacao.linha)

    secoes: list[str] = [_indice(linhas_por_arquivo)]
    ja_incluidos: set[str] = set()

    for nome in disponiveis:
        if nome in inteiros:
            secoes.append(_secao_inteira(nome, linhas_por_arquivo[nome]))
            ja_incluidos.add(nome)
        elif nome in apontados:
            secoes.append(
                _secao_recortada(nome, linhas_por_arquivo[nome], apontados[nome], contexto)
            )
            ja_incluidos.add(nome)

    obrigatorias = len(secoes)
    if sem_arquivo or not ja_incluidos:
        for nome in disponiveis:
            if nome not in ja_incluidos:
                secoes.append(_secao_inteira(nome, linhas_por_arquivo[nome]))

    return _dentro_do_limite(secoes, obrigatorias=obrigatorias, limite=limite)


def _indice(linhas_por_arquivo: dict[str, list[str]]) -> str:
    itens = [f"- {nome} ({len(linhas)} linha(s))" for nome, linhas in linhas_por_arquivo.items()]
    return "\n".join(["=== arquivos do artefato ===", *itens])


def _secao_inteira(nome: str, linhas: list[str]) -> str:
    return f"--- {nome} (1-{len(linhas)} de {len(linhas)}) ---\n" + "\n".join(linhas)


def _secao_recortada(nome: str, linhas: list[str], alvos: set[int], contexto: int) -> str:
    total = len(linhas)
    corpo: list[str] = []
    anterior_fim = 0
    for inicio, fim in _janelas(alvos, total, contexto):
        if inicio > anterior_fim + 1:
            corpo.append(_ELISAO)
        corpo.extend(f"{numero:6d}\t{linhas[numero - 1]}" for numero in range(inicio, fim + 1))
        anterior_fim = fim
    if anterior_fim < total:
        corpo.append(_ELISAO)
    faixas = ", ".join(f"{inicio}-{fim}" for inicio, fim in _janelas(alvos, total, contexto))
    return f"--- {nome} (linhas {faixas} de {total}) ---\n" + "\n".join(corpo)


def _janelas(alvos: set[int], total: int, contexto: int) -> list[tuple[int, int]]:
    """Janelas fechadas `[inicio, fim]` em torno dos alvos, unidas quando encostam.

    Unir é o que impede o mesmo trecho de aparecer duas vezes quando duas violações
    caem perto uma da outra — repetição num prompt de reparo é custo puro, e ainda
    faz o modelo achar que são dois lugares diferentes.
    """
    if total == 0:
        return []
    brutas = sorted(
        (max(1, alvo - contexto), min(total, alvo + contexto))
        for alvo in alvos
        if 1 <= alvo <= total
    )
    if not brutas:
        return [(1, total)]
    unidas: list[tuple[int, int]] = [brutas[0]]
    for inicio, fim in brutas[1:]:
        ultimo_inicio, ultimo_fim = unidas[-1]
        if inicio <= ultimo_fim + 1:
            unidas[-1] = (ultimo_inicio, max(ultimo_fim, fim))
        else:
            unidas.append((inicio, fim))
    return unidas


def _dentro_do_limite(secoes: list[str], *, obrigatorias: int, limite: int) -> str:
    """Junta as seções, descartando as opcionais do fim até caber.

    Descartar do fim, e nunca cortar no meio de uma seção: metade de um spec é
    código que não compila, e o modelo trata o que recebe como o estado real do
    disco. As `obrigatorias` — índice e trechos reclamados — nunca saem; se nem
    elas couberem, o texto sai maior que o limite, porque entregar o reparo sem o
    trecho reclamado é pior do que entregá-lo grande.
    """
    escolhidas = list(secoes)
    while len(escolhidas) > obrigatorias and sum(len(s) + 2 for s in escolhidas) > limite:
        escolhidas.pop()
    texto = "\n\n".join(escolhidas)
    if len(escolhidas) < len(secoes):
        omitidos = len(secoes) - len(escolhidas)
        texto += f"\n\n=== {omitidos} arquivo(s) omitido(s) por tamanho; veja o índice acima ==="
    return texto


def _resolver_arquivo(caminho: str | None, disponiveis: list[str]) -> str | None:
    """Casa o `file` da violação com uma chave do artefato, ou devolve `None`.

    O `file` vem de três origens com convenções diferentes: o validador da skill o
    emite relativo à raiz do recurso, o eslint o emite relativo ao projeto, e as
    violações do orquestrador prefixam o nome do recurso. Casar por sufixo cobre as
    três; casamento ambíguo devolve `None`, porque apontar o arquivo errado é pior
    que não apontar nenhum.
    """
    if not caminho:
        return None
    alvo = caminho.replace("\\", "/").removeprefix("./")
    if alvo in disponiveis:
        return alvo
    candidatos = [nome for nome in disponiveis if alvo.endswith(f"/{nome}")]
    return candidatos[0] if len(candidatos) == 1 else None
