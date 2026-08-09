"""O que se mede de uma execução: tokens, chamadas de modelo e chamadas de tool.

Três modelos puros, sem I/O e sem dependência de nenhum outro subpacote. Quem os
acumula é `telemetria.py`; quem os escreve é `registro.py`; quem os renderiza é
`tabelas.py`. Separá-los daqueles três é o que permite um agente construir um
`RegistroDeChamada` sem arrastar console, arquivo aberto e formatação junto.

Eles não são contrato de domínio, e por isso não moram em `dominio/`: nada aqui
decide aprovação, cobertura ou o que vai para o disco do cliente. São a resposta
para "quanto custou e em que ordem", que é pergunta de observabilidade.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UsoDeTokens(BaseModel):
    """Tokens de uma chamada, e quanto deles o cache do provedor cobriu.

    `cache_lido` e `cache_escrito` são **subconjuntos de `entrada`**, não parcelas
    a somar: um prefixo reaproveitado continua contando como entrada, só que
    cobrado a uma fração. Somá-los ao total contaria duas vezes.

    Os dois, e não só o primeiro, porque a conta só fecha com os dois. Gravar
    cache costuma custar **mais** que uma entrada normal; um agente que escreve
    cache toda volta e nunca o lê paga a mais para não economizar nada. Sem
    `cache_escrito` esse caso se parece com sucesso.
    """

    entrada: int = 0
    saida: int = 0
    cache_lido: int = 0
    cache_escrito: int = 0

    @property
    def total(self) -> int:
        return self.entrada + self.saida

    @property
    def taxa_de_cache(self) -> float:
        """Fração da entrada que veio do cache, de 0 a 1. Zero sem entrada."""
        return self.cache_lido / self.entrada if self.entrada else 0.0

    def __add__(self, outro: UsoDeTokens) -> UsoDeTokens:
        return UsoDeTokens(
            entrada=self.entrada + outro.entrada,
            saida=self.saida + outro.saida,
            cache_lido=self.cache_lido + outro.cache_lido,
            cache_escrito=self.cache_escrito + outro.cache_escrito,
        )


class RegistroDeChamada(BaseModel):
    """Uma chamada de modelo, para provar (ou refutar) o custo linear.

    `caracteres_instrucao` e `caracteres_entrada` medem o que foi **efetivamente
    enviado**: a instrução fixa do estágio (constante, serve de linha de base) e a
    entrada da tentativa. É por essa dupla que se verifica o princípio 2 a partir
    do log — a entrada de um reparo não pode crescer com o número da tentativa.
    """

    estagio: str
    recurso: str
    tentativa: int
    modelo: str
    uso: UsoDeTokens = Field(default_factory=UsoDeTokens)
    duracao_s: float = 0.0
    simulado: bool = False
    detalhe: str = ""
    caracteres_instrucao: int = 0
    caracteres_entrada: int = 0


class RegistroDeTool(BaseModel):
    """Uma chamada de tool do mapeador — o instrumento do custo de exploração.

    O Graphify existe para localizar código sem gastar token varrendo o backend, e a
    instrução do estágio manda consultá-lo **antes** de ler arquivo. Sem este
    registro, "ele obedeceu?" e "que fatia da entrada veio de resposta de tool?" são
    dedução, não medida — e é sobre elas que se decide a otimização do mapeador, que
    é onde mora quase todo o custo do pipeline.

    `ordem` é a posição na sequência da tentativa: é ela, e não o total, que responde
    se o grafo foi consultado antes ou depois da leitura de arquivo.

    `caracteres` é o tamanho do retorno. É o número que importa: ele entra na próxima
    volta do ReAct e é reenviado em todas as seguintes, então resposta de tool grande
    é multiplicador, não parcela.
    """

    estagio: str
    recurso: str
    tentativa: int
    ordem: int
    nome: str
    argumentos: dict[str, Any] = Field(default_factory=dict)
    caracteres: int = 0
    duracao_s: float = 0.0
    # As tools devolvem a falha como texto (`ERRO: ...`) para o modelo poder se
    # corrigir sozinho. Sem esta marca, grafo quebrado passa por exploração
    # bem-sucedida no relatório — e o custo de reindexar aparece como custo de LLM.
    erro: bool = False
