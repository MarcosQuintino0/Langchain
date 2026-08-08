"""JSON dos scripts `.mjs` → `ResultadoGate` / `Violacao`.

Funções puras: recebem a saída de processo já capturada, não invocam nada. É o
que torna o parsing testável sem Node instalado.

O nome nomeia a entrada, não a técnica: são as **saídas dos validadores externos**
que este módulo interpreta. Chamava-se `parser.py`, que não diz de quê — e num
pacote onde outros três módulos também parseiam alguma coisa, esse nome mandava
quem procurava abrir o arquivo para descobrir.
"""

from __future__ import annotations

import json
from typing import Any

from orquestrador.contratos import ResultadoGate, Violacao
from orquestrador.ferramentas.json_externo import extrair_json
from orquestrador.ferramentas.processo import SaidaProcesso

__all__ = [
    "resultado_do_validador",
    "resumo_da_cobertura",
    "violacoes_do_eslint",
]

# O contrato documentado do `validar-suite-gerada.mjs`: exit 0 com `valid: true`,
# exit 1 com `valid: false`. Qualquer outro par é quebra de contrato — não dá para
# escolher em quem acreditar, e escolher errado é aprovar suíte reprovada.
_CODIGO_ESPERADO: dict[bool, int] = {True: 0, False: 1}


def resultado_do_validador(saida: SaidaProcesso, *, gate: str) -> ResultadoGate:
    """Converte a saída de `validar-suite-gerada.mjs --json` num veredito.

    O script escreve no **stdout quando aprova** e no **stderr quando reprova**;
    ler só um dos dois faz reprovação parecer saída vazia.

    Nenhuma falha daqui vira violação: quando o script não se comporta como o
    contrato dele diz, o resultado é `ERRO_DA_FERRAMENTA`, e quem o recebe
    interrompe o recurso em vez de mandar o modelo consertar o que ele não escreveu.
    """
    if saida.codigo == 2:
        return ResultadoGate.erro_da_ferramenta(
            "validar-suite-gerada.mjs recusou a invocação (exit 2): "
            f"{saida.texto[:1000]}\ncomando: {saida.comando}",
            gate=gate,
            saida_bruta=saida.texto,
        )

    # Tenta os dois fluxos: aprovação sai pelo stdout, reprovação pelo stderr, e
    # um aviso solto no fluxo "errado" não pode fazer o gate parecer quebrado.
    dados: dict[str, Any] | None = None
    ultimo_erro: Exception | None = None
    for fluxo in (saida.stdout, saida.stderr):
        if not fluxo.strip():
            continue
        try:
            dados = extrair_json(fluxo)
            break
        except (ValueError, json.JSONDecodeError) as erro:
            ultimo_erro = erro
    if dados is None:
        return ResultadoGate.erro_da_ferramenta(
            f"saída não-JSON de validar-suite-gerada.mjs ({ultimo_erro or 'saída vazia'}). "
            f"código={saida.codigo} comando={saida.comando}\n{saida.texto[:1000]}",
            gate=gate,
            saida_bruta=saida.texto,
        )

    valido = bool(dados.get("valid"))
    if saida.codigo != _CODIGO_ESPERADO[valido]:
        return ResultadoGate.erro_da_ferramenta(
            f'validar-suite-gerada.mjs devolveu "valid": {str(valido).lower()} com '
            f"exit {saida.codigo}; o contrato é exit {_CODIGO_ESPERADO[valido]}. "
            "Confira a versão da skill em [caminhos].skill.\n"
            f"comando: {saida.comando}\n{saida.texto[:1000]}",
            gate=gate,
            saida_bruta=saida.texto,
        )

    violacoes = [Violacao.model_validate(item) for item in dados.get("errors") or []]
    if valido and violacoes:
        return ResultadoGate.erro_da_ferramenta(
            f'validar-suite-gerada.mjs devolveu "valid": true e listou '
            f"{len(violacoes)} erro(s) ({', '.join(v.codigo for v in violacoes)}). "
            "Confira a versão da skill em [caminhos].skill.\n"
            f"comando: {saida.comando}",
            gate=gate,
            saida_bruta=saida.texto,
        )

    return ResultadoGate(
        aprovado=valido,
        violacoes=violacoes,
        avisos=[Violacao.model_validate(item) for item in dados.get("warnings") or []],
        saida_bruta=saida.texto,
        gate=gate,
    )


def resumo_da_cobertura(saida: SaidaProcesso) -> dict[str, Any]:
    """Contadores de `qa-cobertura.mjs --json`.

    Devolve `{}` quando o script não conseguiu gerar o relatório — ele sai 0 mesmo
    nesse caso, então a ausência do JSON é o único sinal.
    """
    try:
        return extrair_json(saida.stdout)
    except (ValueError, json.JSONDecodeError):
        return {}


def violacoes_do_eslint(stdout: str) -> list[Violacao]:
    """Converte `eslint --format json` em violações com arquivo e linha."""
    try:
        arquivos = json.loads(stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []
    violacoes: list[Violacao] = []
    for arquivo in arquivos if isinstance(arquivos, list) else []:
        caminho = str(arquivo.get("filePath", "")).replace("\\", "/")
        for mensagem in arquivo.get("messages") or []:
            if mensagem.get("severity") != 2:
                continue
            regra = mensagem.get("ruleId") or "eslint"
            violacoes.append(
                Violacao(
                    codigo="QAORQ-021",
                    mensagem=f"{regra}: {mensagem.get('message', '').strip()}",
                    arquivo=caminho,
                    linha=mensagem.get("line"),
                )
            )
    return violacoes
