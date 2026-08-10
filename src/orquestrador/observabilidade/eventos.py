"""O vocabulário fechado dos eventos do JSONL, e a versão do formato deles.

Fronteira: este módulo é o **dono do nome de cada evento** e da descrição de uma
linha que o acompanha. Ele não escreve nada em disco — quem escreve é
`registro.py` — e não conhece pipeline, gate nem agente: um enum que soubesse de
quem o emite inverteria a seta de dependência que
`tests/test_invariante_estrutura_do_codigo.py` protege.

Por que um enum, e não a string solta que havia antes
-----------------------------------------------------
O tipo do evento é o índice do log: é por ele que se filtra `execucao.jsonl` para
responder "o que aconteceu nesta execução". Enquanto era literal, o nome vivia
espalhado por sete módulos e o catálogo do README era mantido à mão — e divergiu
duas vezes no mesmo dia (faltavam `execucao_abortada` e `cypress`, sobrava
`artefatos_removidos`; `publicacao`, `staging_mantido` e `schemas_divergentes`
nunca chegaram a ser documentados). Uma lista mantida à mão ao lado de outra lista
sempre diverge; a saída é ter **uma** lista e gerar a outra a partir dela, que é o
que `catalogo_markdown` faz.

A descrição fica junto do membro, e não num dicionário paralelo, pelo mesmo
motivo: um dicionário paralelo é a segunda lista de novo, só que em Python.

Invariante do formato
---------------------
Toda linha do JSONL carrega `schema_version`. Sem ela, mudar a forma de um campo
transforma todo log anterior em dado ambíguo — quem lê não consegue distinguir
"campo ausente porque não havia" de "campo ausente porque o formato era outro".
Incremente `ESQUEMA_DOS_EVENTOS` quando a forma de um campo mudar ou um campo sair;
acrescentar campo novo é compatível e não exige incremento.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

__all__ = ["ESQUEMA_DOS_EVENTOS", "Evento", "TipoDeEvento", "catalogo_markdown"]

ESQUEMA_DOS_EVENTOS = 2


@unique
class TipoDeEvento(StrEnum):
    """Tipo de uma linha do `execucao.jsonl`, com a descrição que vai ao README.

    `StrEnum` de propósito: o membro **é** a string que vai para o JSONL, então
    nenhum log muda de forma por causa deste enum e nenhum leitor de log precisa
    saber que ele existe.
    """

    # Anotação sem valor não vira membro do enum — é só a declaração do atributo
    # que `__new__` preenche.
    descricao: str

    def __new__(cls, valor: str, descricao: str) -> TipoDeEvento:
        membro = str.__new__(cls, valor)
        membro._value_ = valor
        membro.descricao = descricao
        return membro

    # -- execução -----------------------------------------------------------
    EXECUCAO_INICIADA = (
        "execucao_iniciada",
        "abertura: dry-run, recursos pedidos, arquivo de configuração e os dois repositórios",
    )
    MANIFESTO_DE_EXECUCAO = (
        "manifesto_de_execucao",
        "onde o `manifesto-execucao.json` foi escrito e quais campos não puderam ser coletados",
    )
    PULSO = ("pulso", "sinal periódico de que a execução continua viva")
    OPERACAO_INICIADA = (
        "operacao_iniciada",
        "abertura de uma operação correlacionada por trace e span",
    )
    OPERACAO_CONCLUIDA = (
        "operacao_concluida",
        "fechamento bem-sucedido de uma operação, com sua duração",
    )
    OPERACAO_FALHOU = (
        "operacao_falhou",
        "fechamento com falha de uma operação, sem conteúdo sensível da exceção",
    )

    # -- Bloco 0 ------------------------------------------------------------
    BLOCO0 = ("bloco0", "preparação determinística: se o `graph.json` ficou utilizável, e por quê")
    SUPERFICIE = (
        "superficie",
        "módulos compartilhados do projeto de testes e os exports que o executor pode importar",
    )

    # -- loop de reparo -----------------------------------------------------
    ESTAGIO_TENTATIVA = (
        "estagio_tentativa",
        "uma tentativa de um estágio: tamanho da instrução fixa, da entrada e uso de tools",
    )
    CHAMADA_LLM = (
        "chamada_llm",
        "uma chamada ao modelo: estágio, recurso, tentativa, modelo e tokens de entrada e saída",
    )
    REQUISICAO_LLM_INICIADA = (
        "requisicao_llm_iniciada",
        "uma requisição real ao provedor começou, com contexto da unidade de trabalho",
    )
    REQUISICAO_LLM_CONCLUIDA = (
        "requisicao_llm_concluida",
        "uma requisição real ao provedor terminou, com uso, duração e motivo de parada",
    )
    REQUISICAO_LLM_FALHOU = (
        "requisicao_llm_falhou",
        "uma requisição real ao provedor falhou, com categoria e identificador de suporte",
    )
    TOOL = ("tool", "uma chamada de tool do mapeador: ordem, argumentos, tamanho do retorno e erro")
    GATE = ("gate", "veredito de um gate numa tentativa, com violações e avisos")
    DELTA = ("delta", "o delta enviado ao reparo: códigos de violação e tamanho do artefato atual")

    # -- artefatos ----------------------------------------------------------
    ARTEFATOS = ("artefatos", "arquivos que um estágio escreveu na área de staging da execução")
    SCHEMAS_PRESERVADOS = (
        "schemas_preservados",
        "schemas que já eram do consumidor e o mapeador não sobrescreveu",
    )
    SCHEMAS_DIVERGENTES = (
        "schemas_divergentes",
        "campos que o mapeador achou no backend e o schema preservado não declara",
    )
    PUBLICACAO = (
        "publicacao",
        "o que a publicação fez no projeto do consumidor, arquivo a arquivo, com hash e classificação",
    )
    ARTEFATOS_REPROVADOS = (
        "artefatos_reprovados",
        "o que ficou em disco em estado reprovado, e se chegou a ser publicado",
    )
    STAGING_MANTIDO = (
        "staging_mantido",
        "o staging do recurso sobreviveu ao fim porque tem artefato para inspecionar",
    )

    # -- Bloco 3 ------------------------------------------------------------
    CYPRESS = (
        "cypress",
        "execução da suíte: código de saída e relatório desta execução, ou o motivo de não rodar",
    )
    PROCESSO = (
        "processo",
        "subprocesso externo medido por código, duração, bytes, timeout e hash do comando",
    )
    COBERTURA = ("cobertura", "contadores do `qa-cobertura.mjs` e se houve execução de runtime")

    # -- desfecho -----------------------------------------------------------
    RECURSO_FALHOU = ("recurso_falhou", "o recurso terminou reprovado, com o motivo")
    RECURSO_CONCLUIDO = (
        "recurso_concluido",
        "desfecho do recurso: estado, tentativas e execução de testes",
    )
    TELEMETRIA = (
        "telemetria",
        "agregados de token e de caracteres por estágio, recurso e tentativa",
    )
    EXECUCAO_INTERROMPIDA = (
        "execucao_interrompida",
        "o laço de recursos parou no meio por ferramenta indisponível; lista quem não rodou",
    )
    EXECUCAO_ABORTADA = ("execucao_abortada", "a execução terminou sem veredito, com o motivo")
    EXECUCAO_CONCLUIDA = (
        "execucao_concluida",
        "fechamento: sucesso, interrupção e o resumo por recurso",
    )


class Evento(BaseModel):
    """Evento antes de receber o envelope de execução do escritor JSONL.

    O payload aceita somente valores JSON. Isso mantém `Path`, modelos Pydantic e
    objetos de terceiros do lado de fora do contrato persistido; a camada de
    compatibilidade de `Registro.evento` converte esses valores antes de construir
    este modelo.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tipo: TipoDeEvento
    dados: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    @classmethod
    def de_campos(cls, tipo: TipoDeEvento, campos: dict[str, Any]) -> Evento:
        return cls(tipo=tipo, dados=campos)


def catalogo_markdown() -> str:
    """Tabela Markdown do catálogo, na ordem de declaração do enum.

    O README consome esta saída entre marcadores, e `tests/test_observabilidade_eventos.py`
    compara os dois. Gerar em vez de conferir por lista: conferir exigiria manter a
    lista de nomes num terceiro lugar, e a divergência que este item resolve
    nasceu exatamente de um segundo lugar.
    """
    linhas = ["| Evento | O que registra |", "| --- | --- |"]
    linhas += [f"| `{tipo.value}` | {tipo.descricao} |" for tipo in TipoDeEvento]
    return "\n".join(linhas)
