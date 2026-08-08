"""Exceções do pipeline, num lugar só.

A distinção que importa é **quem errou**:

* `ErroDeConfiguracao` — o arquivo de configuração ou o ambiente estão errados.
  Nada foi tentado ainda; quem corrige é o operador.
* `ErroDeFerramenta` e derivadas — erro de invocação **nosso**: comando mal
  montado, executável ausente, tempo esgotado, script que não devolveu veredito.
  Nunca vira delta para o LLM; o pipeline falha alto para que seja corrigido no
  código. Também não é isolado por recurso: ferramenta quebrada está quebrada para
  todos, e insistir só queima token — `Pipeline.rodar` interrompe o laço e entrega
  o que já tinha terminado.
* `FalhaDeEstagio` e `FalhaDeGate` — o LLM não entregou artefato válido dentro do
  limite de tentativas. Falha **daquele recurso**, não da execução: `Pipeline`
  captura, registra e segue para o próximo recurso.

As duas últimas carregam os arquivos que ficaram em disco em estado reprovado.
Os arquivos não são apagados (apagar arquivo do usuário é pior que deixar, e o
artefato reprovado é o que se quer inspecionar) — mas o efeito não pode ser
silencioso, então quem falha diz o que deixou para trás.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita ciclo de import em tempo de execução
    from orquestrador.contratos import Violacao


class ErroDeConfiguracao(RuntimeError):
    """Configuração ausente, incoerente ou apontando para caminho inexistente.

    Mora aqui, e não em `config.py`, porque este módulo é o dono canônico da
    taxonomia: com a exceção declarada ao lado do arquivo que a levanta havia duas
    respostas válidas para "de onde eu importo isso?", e num projeto escrito por
    agente cada um importa de onde encontrou primeiro.
    """


class ErroDeFerramenta(RuntimeError):
    """Falha ao invocar uma ferramenta externa (ausente, timeout, uso inválido)."""


class ExecutavelAusente(ErroDeFerramenta):
    """Executável não encontrado no PATH."""


class ProjetoNaoPreparado(ErroDeFerramenta):
    """O projeto de testes não tem os módulos compartilhados que o executor consome.

    Pré-condição, não falha de recurso: o orquestrador **gera testes** num projeto
    já preparado; preparar o projeto é outro fluxo da skill
    (`references/preparar-projeto.md`). Falha cedo, antes de qualquer chamada de
    modelo — substituir em silêncio pela arquitetura-base produziria imports que não
    existem no projeto do usuário, e o loop de reparo não converge sobre isso.
    """


class GrafoNaoPreparado(ErroDeFerramenta):
    """O Bloco 0 não deixou um `graph.json` utilizável para esta execução.

    Mesma natureza de `ProjetoNaoPreparado`: pré-condição do ambiente, não falha de
    um recurso. Interrompe **antes de qualquer chamada de modelo** porque um grafo
    ausente, corrompido ou defasado não produz erro visível adiante — ele produz um
    mapeador consultando um mapa errado, gastando token em exploração que nenhum
    gate consegue reprovar por esse motivo.
    """


class FalhaComArtefatos(RuntimeError):
    """Falha de recurso que pode ter deixado artefato reprovado em disco."""

    def __init__(
        self,
        mensagem: str,
        *,
        arquivos: Iterable[Path] | None = None,
        violacoes: Iterable[Violacao] | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.arquivos: list[Path] = list(arquivos or ())
        self.violacoes: list[Violacao] = list(violacoes or ())

    @property
    def codigos(self) -> list[str]:
        return sorted({violacao.codigo for violacao in self.violacoes})


class FalhaDeEstagio(FalhaComArtefatos):
    """O estágio não produziu artefato válido dentro do limite de tentativas.

    Cobre tanto a saída que não valida contra o contrato Pydantic quanto o
    estouro do limite de passos do agente ReAct.
    """


class FalhaDaExecucaoDeTestes(FalhaDeEstagio):
    """O Bloco 3 rodou a suíte e ela não passou, ou não deixou relatório desta execução.

    É falha **do recurso**, como as outras deste ramo: a suíte gerada para ele não
    se sustenta em runtime, mas os recursos seguintes continuam. O que não pode
    acontecer é o que acontecia antes — código de saída virar evento e o pipeline
    encerrar o recurso como sucesso.
    """


class FalhaDeGate(FalhaComArtefatos):
    """Um gate reprovou em todas as tentativas permitidas."""
