"""JSON de validador externo → `Violacao`.

Funções puras: recebem a saída de processo já capturada, não invocam nada. É o
que torna o parsing testável sem a ferramenta instalada.

O nome nomeia a entrada, não a técnica: são as **saídas dos validadores externos**
que este módulo interpreta. Sobrou uma, o `eslint --format json`; os parsers do
`validar-suite-gerada.mjs` e do `qa-cobertura.mjs` saíram com o desacoplamento da
skill, porque nada os invoca mais e parser sem chamador não tem como estar certo
ou errado — ele só envelhece.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from orquestrador.dominio.veredito import Violacao

__all__ = ["violacoes_do_eslint"]


class _MensagemDoEslint(BaseModel):
    """Uma entrada de `messages` no `eslint --format json`.

    Os nomes são os do ESLint, não os nossos: este modelo existe para validar o que
    chega, não para renomear. `extra="ignore"` é deliberado — o ESLint acrescenta
    campo entre versões (`fix`, `suggestions`, `messageId`), e reprovar por campo
    novo transformaria atualização de linter em falha de gate.
    """

    model_config = ConfigDict(extra="ignore")

    severity: int = 0
    message: str = ""
    ruleId: str | None = None
    line: int | None = None


class _ArquivoDoEslint(BaseModel):
    """Um arquivo relatado pelo `eslint --format json`."""

    model_config = ConfigDict(extra="ignore")

    filePath: str = ""
    messages: list[_MensagemDoEslint] = Field(default_factory=list[_MensagemDoEslint])


_LISTA_DO_ESLINT = TypeAdapter(list[_ArquivoDoEslint])

# A severidade 2 é "error" no ESLint; 1 é "warn" e não reprova o gate.
_SEVERIDADE_DE_ERRO = 2


def violacoes_do_eslint(stdout: str) -> list[Violacao]:
    """Converte `eslint --format json` em violações com arquivo e linha.

    O JSON do ESLint é o exemplo de fronteira deste módulo: entra `Any` de terceiro,
    sai `list[Violacao]`. A conversão acontece na primeira linha útil — o `Any` não
    atravessa o laço. Saída ilegível vira lista vazia, e não exceção: quem decide o
    que fazer com "o linter não falou" é `gate_b`, que já trata o caso.
    """
    try:
        arquivos = _LISTA_DO_ESLINT.validate_json(stdout.strip() or "[]")
    except ValidationError:
        return []

    violacoes: list[Violacao] = []
    for arquivo in arquivos:
        caminho = arquivo.filePath.replace("\\", "/")
        for mensagem in arquivo.messages:
            if mensagem.severity != _SEVERIDADE_DE_ERRO:
                continue
            regra = mensagem.ruleId or "eslint"
            violacoes.append(
                Violacao(
                    codigo="QAORQ-021",
                    mensagem=f"{regra}: {mensagem.message.strip()}",
                    arquivo=caminho,
                    linha=mensagem.line,
                )
            )
    return violacoes
