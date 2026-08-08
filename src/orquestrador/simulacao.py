"""Modo simulação: o pipeline inteiro roda sem chamar nenhum modelo.

O `ModeloSimulado` é um `BaseChatModel` de verdade, então a fiação exercitada no
`--dry-run` é a mesma da execução real: o loop ReAct do LangGraph roda, as tools
são chamadas, os gates invocam os scripts `.mjs` de verdade sobre arquivos de
verdade, os deltas são montados e reenviados. O único trecho substituído é a
resposta do modelo, que vem de fixture.

Um roteiro por (recurso, estágio, tentativa):

    fixtures/roteiros/<recurso>/<estagio>/tentativa-01.json

```json
{
  "descricao": "primeira tentativa: falta contabilizar CAT-05",
  "passos": [
    {"tipo": "tool", "nome": "listar_diretorio", "argumentos": {"caminho": "src"}},
    {"tipo": "final", "artefato": {"inventario": {}, "manifesto": {}}}
  ]
}
```

`final` aceita `artefato` (objeto, serializado em JSON) ou `conteudo` (texto cru —
útil para exercitar o delta de schema com uma resposta malformada).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field

from orquestrador.raiz import DIR_FIXTURES

if TYPE_CHECKING:
    from orquestrador.config import Config

# Estimativa grosseira só para a telemetria do dry-run ter ordem de grandeza; a
# tabela final é marcada como SIMULADA para ninguém confundir com custo real.
CARACTERES_POR_TOKEN = 4


class RoteiroAusente(FileNotFoundError):
    pass


class PassoDeTool(BaseModel):
    """Passo que manda o grafo executar uma tool e devolver o resultado ao modelo."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["tool"]
    nome: str
    argumentos: dict[str, Any] = Field(default_factory=dict[str, Any])
    pensamento: str = ""


class PassoFinal(BaseModel):
    """Passo que encerra a tentativa.

    `artefato` é o objeto do contrato, serializado em JSON; `conteudo` é texto cru,
    que existe para exercitar o delta de schema com uma resposta malformada. A
    distinção é entre **declarado** e ausente, não entre valor e vazio: um roteiro
    com `"artefato": null` está afirmando que o modelo respondeu `null`, e o
    mini-loop de reparo precisa ver isso em vez de cair no texto cru. Por isso o
    teste é `model_fields_set`, e não `artefato is None`.
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["final"]
    artefato: Any = None
    conteudo: str = ""

    def texto(self) -> str:
        if "artefato" in self.model_fields_set:
            return json.dumps(self.artefato, ensure_ascii=False, indent=2)
        return self.conteudo


# O `tipo` do roteiro deixa de ser string comparada à mão e vira discriminante: um
# `"tipo": "tol"` na fixture passa a ser erro de validação com o nome do arquivo,
# em vez de virar silenciosamente um passo final sem artefato.
PassoDoRoteiro = Annotated[PassoDeTool | PassoFinal, Field(discriminator="tipo")]


class ModeloSimulado(BaseChatModel):
    """Devolve, em ordem, os passos de um roteiro de fixture."""

    passos: list[PassoDoRoteiro]
    rotulo: str = "simulado"
    simulado: bool = True

    @property
    def _llm_type(self) -> str:
        return "simulado"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ModeloSimulado:  # noqa: ARG002
        # O roteiro é fixo; as tools existem para o grafo do LangGraph poder
        # executá-las quando o roteiro pedir.
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> ChatResult:
        # Quantas vezes este modelo já falou nesta invocação define o passo atual.
        # Contar a partir das mensagens mantém o modelo stateless (princípio 3).
        indice = sum(1 for mensagem in messages if isinstance(mensagem, AIMessage))
        passo = self.passos[min(indice, len(self.passos) - 1)]
        entrada = sum(len(str(mensagem.content)) for mensagem in messages)

        if isinstance(passo, PassoDeTool):
            conteudo = passo.pensamento
            mensagem = AIMessage(
                content=conteudo,
                tool_calls=[
                    {
                        "name": passo.nome,
                        "args": passo.argumentos,
                        "id": f"chamada-{indice}",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_uso(entrada, len(conteudo) + 40),
            )
        else:
            conteudo = passo.texto()
            mensagem = AIMessage(content=conteudo, usage_metadata=_uso(entrada, len(conteudo)))

        return ChatResult(generations=[ChatGeneration(message=mensagem)])


def _uso(caracteres_entrada: int, caracteres_saida: int) -> dict[str, int]:
    entrada = max(1, caracteres_entrada // CARACTERES_POR_TOKEN)
    saida = max(1, caracteres_saida // CARACTERES_POR_TOKEN)
    return {"input_tokens": entrada, "output_tokens": saida, "total_tokens": entrada + saida}


class Roteiros:
    """Localiza o roteiro de cada (recurso, estágio, tentativa)."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = Path(raiz)

    def diretorio(self, recurso: str, estagio: str) -> Path:
        return self.raiz / recurso / estagio

    def disponiveis(self, recurso: str, estagio: str) -> list[Path]:
        destino = self.diretorio(recurso, estagio)
        if not destino.is_dir():
            return []
        return sorted(destino.glob("tentativa-*.json"))

    def carregar(self, recurso: str, estagio: str, tentativa: int) -> dict[str, Any]:
        arquivos = self.disponiveis(recurso, estagio)
        if not arquivos:
            raise RoteiroAusente(
                f"nenhum roteiro de simulação para recurso={recurso!r} estagio={estagio!r} "
                f"em {self.diretorio(recurso, estagio)}"
            )
        alvo = self.diretorio(recurso, estagio) / f"tentativa-{tentativa:02d}.json"
        # Sem roteiro para esta tentativa: repete o último — assim um limite de
        # tentativas maior que o roteiro falha por esgotamento do gate, e não por
        # arquivo faltando.
        escolhido = alvo if alvo.is_file() else arquivos[-1]
        return json.loads(escolhido.read_text(encoding="utf-8"))

    def modelo(self, recurso: str, estagio: str, tentativa: int) -> ModeloSimulado:
        roteiro = self.carregar(recurso, estagio, tentativa)
        # `passos` continua `Any`: é JSON de fixture, e quem lhe dá forma é o
        # `ModeloSimulado` logo abaixo, validando contra o união discriminada.
        passos = roteiro.get("passos")
        if not passos:
            raise RoteiroAusente(
                f'roteiro sem "passos": recurso={recurso!r} estagio={estagio!r} '
                f"tentativa={tentativa}"
            )
        return ModeloSimulado(passos=passos, rotulo=f"{estagio}/tentativa-{tentativa:02d}")

    def recursos(self) -> Iterator[str]:
        if not self.raiz.is_dir():
            return
        for item in sorted(self.raiz.iterdir()):
            if item.is_dir():
                yield item.name


# ---------------------------------------------------------------------------
# Sandbox do dry-run
# ---------------------------------------------------------------------------


def preparar_sandbox(config: Config, destino: Path) -> Config:
    """Copia o projeto e o backend de fixture para uma sandbox e reaponta a config.

    O dry-run escreve arquivos de verdade e roda os gates de verdade sobre eles;
    o que ele não faz é chamar modelo. Trabalhar numa cópia mantém as fixtures
    limpas entre execuções.
    """
    projeto = destino / "projeto-testes"
    backend = destino / "backend"
    shutil.copytree(DIR_FIXTURES / "projeto-testes", projeto, dirs_exist_ok=True)
    shutil.copytree(DIR_FIXTURES / "backend", backend, dirs_exist_ok=True)

    grafo = projeto / config.caminhos.graph
    grafo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DIR_FIXTURES / "graphify-out" / "graph.json", grafo)

    caminhos = config.caminhos.model_copy(update={"projeto_testes": projeto, "backend": backend})
    return config.model_copy(update={"caminhos": caminhos})
