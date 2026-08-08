"""Exceções do pipeline, num lugar só.

A distinção que importa é **quem errou**:

* `ErroDeConfiguracao` — o arquivo de configuração ou o ambiente estão errados.
  Nada foi tentado ainda; quem corrige é o operador.
* `ErroDeFerramenta` e derivadas — erro **operacional**: comando mal montado,
  executável ausente, tempo esgotado, script que não devolveu veredito, provedor
  fora do ar. Nunca vira delta para o LLM; o pipeline falha alto para que seja
  corrigido no código ou no ambiente. Também não é isolado por recurso: ferramenta
  quebrada está quebrada para todos, e insistir só queima token — `Pipeline.rodar`
  interrompe o laço e entrega o que já tinha terminado.
* `FalhaDeEstagio` e `FalhaDeGate` — o LLM não entregou artefato válido dentro do
  limite de tentativas. Falha **daquele recurso**, não da execução: `Pipeline`
  captura, registra e segue para o próximo recurso.

`ErroDeProvedor` é o ramo operacional que mais se confunde com falha de artefato, e
por isso está separado: quando o provedor recusa, atrasa ou some, o artefato que
estava sendo gerado não é ruim — ele não chegou a existir. Tratá-lo como reprovação
aciona reparo indevido, e a tentativa é queimada consertando algo que ninguém
mediu.

As duas últimas carregam os arquivos que ficaram em disco em estado reprovado.
Os arquivos não são apagados (apagar arquivo do usuário é pior que deixar, e o
artefato reprovado é o que se quer inspecionar) — mas o efeito não pode ser
silencioso, então quem falha diz o que deixou para trás.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita ciclo de import em tempo de execução
    from orquestrador.dominio.veredito import Violacao


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


class CategoriaDeProvedor(StrEnum):
    """Por que a chamada ao provedor não devolveu resposta utilizável.

    A categoria existe para responder duas perguntas que um traceback não responde:
    **o operador faz o quê?** e **insistir adianta?** São eixos independentes —
    limite de taxa passa sozinho e autenticação não passa nunca —, e é a combinação
    dos dois que decide se o orquestrador espera e tenta de novo ou interrompe na
    primeira.

    `CONTRATO_INVALIDO` é a que mais se confunde, e a confusão é cara. Ela é o
    **provedor** rompendo o contrato da API: rota que recusa o mecanismo de saída
    estruturada, corpo que não é uma resposta de chat, 4xx sobre a requisição que
    montamos. Não é o modelo respondendo algo que não valida contra o contrato
    Pydantic do estágio — isso é resposta recebida, o mini-loop de schema de
    `llm/estruturado.py` a conserta reenviando só o delta, e converge barato.
    Colapsar as duas transformaria erro de operação em tentativa de reparo, ou
    reparo barato em execução interrompida.
    """

    AUTENTICACAO = "autenticacao"
    LIMITE_DE_TAXA = "limite_de_taxa"
    TEMPO_ESGOTADO = "tempo_esgotado"
    TRANSITORIO = "transitorio"
    CONTRATO_INVALIDO = "contrato_invalido"

    @property
    def retentavel(self) -> bool:
        """Se repetir a **mesma** requisição tem chance de mudar o desfecho."""
        return self in {
            CategoriaDeProvedor.LIMITE_DE_TAXA,
            CategoriaDeProvedor.TEMPO_ESGOTADO,
            CategoriaDeProvedor.TRANSITORIO,
        }

    @property
    def rotulo(self) -> str:
        match self:
            case CategoriaDeProvedor.AUTENTICACAO:
                return "provedor recusou a credencial"
            case CategoriaDeProvedor.LIMITE_DE_TAXA:
                return "provedor aplicou limite de taxa"
            case CategoriaDeProvedor.TEMPO_ESGOTADO:
                return "tempo esgotado esperando o provedor"
            case CategoriaDeProvedor.TRANSITORIO:
                return "provedor indisponível"
            case CategoriaDeProvedor.CONTRATO_INVALIDO:
                return "provedor devolveu resposta fora do contrato da API"

    @property
    def orientacao(self) -> str:
        """O que quem opera faz a respeito. Sem isto a categoria é só um rótulo."""
        match self:
            case CategoriaDeProvedor.AUTENTICACAO:
                return (
                    "Confira a variável de ambiente de `[openrouter].api_key_env`: chave "
                    "ausente, revogada, sem permissão para o modelo do estágio ou conta sem "
                    "crédito. Nenhuma tentativa a mais resolve. Para rodar sem provedor, use "
                    "--dry-run."
                )
            case CategoriaDeProvedor.LIMITE_DE_TAXA:
                return (
                    "Reduza a concorrência ou espere a janela do provedor abrir. Se for "
                    "recorrente, suba o limite na conta do OpenRouter ou aponte o estágio "
                    "para outro modelo em `[estagios]`."
                )
            case CategoriaDeProvedor.TEMPO_ESGOTADO:
                return (
                    "Suba `[openrouter].timeout_s` ou escolha um modelo mais rápido para o "
                    "estágio. Prompt muito grande também estoura o tempo: confira o tamanho "
                    "da entrada na telemetria da tentativa."
                )
            case CategoriaDeProvedor.TRANSITORIO:
                return (
                    "Indisponibilidade do provedor ou da rede, não do artefato. Confira o "
                    "status do OpenRouter e a conectividade; nada foi reprovado, e nenhum "
                    "arquivo do seu projeto foi tocado."
                )
            case CategoriaDeProvedor.CONTRATO_INVALIDO:
                return (
                    "A rota não honrou o contrato da API. O caso comum é o modelo do estágio "
                    "não suportar o mecanismo de `[estagios.<nome>].modo_estruturado`: troque "
                    'para "prompt", que é o portátil. Não é o artefato que está errado.'
                )


class ErroDeProvedor(ErroDeFerramenta):
    """A chamada ao provedor não devolveu resposta utilizável.

    Herda de `ErroDeFerramenta` porque o tratamento é o mesmo, e é esse tratamento
    que a taxonomia existe para garantir: **nunca vira `delta.violacoes`** e nunca é
    isolado por recurso. Um provedor fora do ar continua fora do ar no recurso
    seguinte, e mandar o modelo "corrigir" um artefato que ele não chegou a produzir
    é tentativa queimada sem chance de convergir — a mesma razão que já move
    `ResultadoGate.exigir_veredito`, aqui na fronteira de cima.

    O que ela acrescenta ao ramo é a categoria (o que o operador faz), a contagem de
    tentativas já gastas e o identificador da requisição, que é o que o suporte do
    provedor pede.
    """

    def __init__(
        self,
        categoria: CategoriaDeProvedor,
        *,
        detalhe: str,
        estagio: str = "",
        recurso: str = "",
        tentativas: int = 1,
        request_id: str = "",
        status: int | None = None,
    ) -> None:
        onde = " ".join(
            parte
            for parte in (
                f"no estágio {estagio}" if estagio else "",
                f"para o recurso {recurso!r}" if recurso else "",
            )
            if parte
        )
        identificacao = " ".join(
            parte
            for parte in (
                f"HTTP {status}" if status is not None else "",
                f"request_id={request_id}" if request_id else "",
            )
            if parte
        )
        super().__init__(
            f"{categoria.rotulo}{' ' + onde if onde else ''} após {tentativas} tentativa(s)"
            f"{' [' + identificacao + ']' if identificacao else ''}: {detalhe}\n"
            f"{categoria.orientacao}"
        )
        self.categoria = categoria
        self.detalhe = detalhe
        self.estagio = estagio
        self.recurso = recurso
        self.tentativas = tentativas
        self.request_id = request_id
        self.status = status


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


class FalhaDePublicacao(FalhaComArtefatos):
    """A publicação no projeto do consumidor foi abortada e desfeita.

    Duas causas: o arquivo de destino mudou entre o começo do recurso e a
    publicação (alguém editou, outra ferramenta escreveu), ou a substituição em si
    falhou no meio. Nos dois casos o projeto volta ao estado anterior — a falha é
    **do recurso**, e os artefatos continuam no staging para inspeção.

    É irmã de `FalhaDeGate`, e não filha de `ErroDeFerramenta`: não há ferramenta
    quebrada nem ambiente errado, e o recurso seguinte pode perfeitamente publicar.
    """
