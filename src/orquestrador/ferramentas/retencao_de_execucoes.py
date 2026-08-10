"""Retenção explícita e confinada dos diretórios locais de execução.

Planejar nunca escreve. Aplicar revalida cada alvo imediatamente antes de remover,
recusa execução ativa/ilegível/sem terminal e só aceita filho direto da raiz sem
symlink ou junction. O diário fica na raiz, acima dos diretórios removidos.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

from orquestrador.observabilidade.leitura import ler_execucao

__all__ = [
    "PlanoDeLimpeza",
    "RecusaDeLimpeza",
    "ResultadoDaLimpeza",
    "aplicar_limpeza",
    "planejar_limpeza",
]

_DATA_NO_NOME = re.compile(r"^(\d{8}-\d{6})-")
_FORMATO = "%Y%m%d-%H%M%S"
_NOME_DO_DIARIO = "retencao.jsonl"


@dataclass(frozen=True)
class RecusaDeLimpeza:
    caminho: Path
    motivo: str


@dataclass(frozen=True)
class PlanoDeLimpeza:
    base: Path
    antes_de_dias: int
    candidatos: list[Path] = field(default_factory=list[Path])
    recusados: list[RecusaDeLimpeza] = field(default_factory=list[RecusaDeLimpeza])


@dataclass(frozen=True)
class ResultadoDaLimpeza:
    removidos: list[Path] = field(default_factory=list[Path])
    recusados: list[RecusaDeLimpeza] = field(default_factory=list[RecusaDeLimpeza])


def _e_junction(caminho: Path) -> bool:
    verificar = getattr(os.path, "isjunction", None)
    return bool(verificar(caminho)) if verificar is not None else False


def _recusa_estrutural(base: Path, caminho: Path) -> str | None:
    if caminho.is_symlink() or _e_junction(caminho):
        return "symlink ou junction não é removível pela retenção"
    try:
        resolvido = caminho.resolve(strict=True)
    except OSError as erro:
        return f"caminho ilegível: {type(erro).__name__}"
    if resolvido.parent != base:
        return "caminho resolvido está fora da raiz ou não é filho direto"
    if not resolvido.is_dir():
        return "alvo não é diretório"
    return None


def _data_do_nome(caminho: Path, fuso: tzinfo | None) -> datetime | None:
    achado = _DATA_NO_NOME.match(caminho.name)
    if achado is None:
        return None
    try:
        return datetime.strptime(achado.group(1), _FORMATO).replace(tzinfo=fuso)
    except ValueError:
        return None


def _recusa_operacional(caminho: Path) -> str | None:
    leitura = ler_execucao(caminho)
    if leitura.ativa:
        return "execução ativa (execucao.lock presente)"
    if leitura.terminal is None:
        return "execução sem evento terminal"
    erros = [problema.codigo for problema in leitura.problemas if problema.severidade == "erro"]
    if erros:
        return "log inválido: " + ", ".join(sorted(set(erros)))
    return None


def planejar_limpeza(
    base: Path,
    *,
    antes_de_dias: int,
    agora: datetime | None = None,
) -> PlanoDeLimpeza:
    if antes_de_dias < 1:
        raise ValueError("--antes-de precisa ser >= 1")
    raiz = base.resolve()
    if not raiz.is_dir():
        return PlanoDeLimpeza(raiz, antes_de_dias)
    instante = agora or datetime.now().astimezone()
    limite = instante - timedelta(days=antes_de_dias)
    candidatos: list[Path] = []
    recusados: list[RecusaDeLimpeza] = []
    for caminho in sorted(raiz.iterdir()):
        if not caminho.is_dir():
            continue
        if (motivo := _recusa_estrutural(raiz, caminho)) is not None:
            recusados.append(RecusaDeLimpeza(caminho, motivo))
            continue
        data = _data_do_nome(caminho, instante.tzinfo)
        if data is None:
            recusados.append(RecusaDeLimpeza(caminho, "nome sem data de execução reconhecível"))
            continue
        if data >= limite:
            continue
        if (motivo := _recusa_operacional(caminho)) is not None:
            recusados.append(RecusaDeLimpeza(caminho, motivo))
            continue
        candidatos.append(caminho)
    return PlanoDeLimpeza(raiz, antes_de_dias, candidatos, recusados)


def _registrar(base: Path, caminho: Path, resultado: str, motivo: str = "") -> None:
    linha = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "run_id": caminho.name,
        "resultado": resultado,
        "motivo": motivo,
    }
    with (base / _NOME_DO_DIARIO).open("a", encoding="utf-8", newline="\n") as fluxo:
        fluxo.write(json.dumps(linha, ensure_ascii=False) + "\n")
        fluxo.flush()


def aplicar_limpeza(plano: PlanoDeLimpeza) -> ResultadoDaLimpeza:
    base = plano.base.resolve(strict=True)
    removidos: list[Path] = []
    recusados = list(plano.recusados)
    for caminho in plano.candidatos:
        motivo = _recusa_estrutural(base, caminho) or _recusa_operacional(caminho)
        if motivo is not None:
            recusa = RecusaDeLimpeza(caminho, f"revalidação: {motivo}")
            recusados.append(recusa)
            _registrar(base, caminho, "recusado", recusa.motivo)
            continue
        try:
            shutil.rmtree(caminho)
        except OSError as erro:
            recusa = RecusaDeLimpeza(caminho, f"remoção falhou: {type(erro).__name__}: {erro}")
            recusados.append(recusa)
            _registrar(base, caminho, "falhou", recusa.motivo)
            continue
        removidos.append(caminho)
        _registrar(base, caminho, "removido")
    return ResultadoDaLimpeza(removidos, recusados)
