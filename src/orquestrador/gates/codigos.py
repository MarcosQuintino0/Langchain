"""Catálogo dos códigos de violação emitidos pelo próprio orquestrador.

Os `QAAPI-0xx` vêm dos scripts da skill e são catalogados lá. Os daqui usam o
prefixo `QAORQ-` para nunca colidirem com eles.

O catálogo mora num módulo próprio, e não no `__init__.py` do pacote, porque
importar um código de violação não pode custar a importação de todos os gates —
e porque `__init__` que carrega conteúdo é o lugar onde nomes vão parar por
acidente, em vez de por decisão.
"""

from __future__ import annotations

CODIGOS_DO_ORQUESTRADOR: dict[str, str] = {
    "QAORQ-001": "aviso: trecho do backend que o diff grafo × manifesto não conseguiu resolver",
    "QAORQ-002": "endpoint presente no backend e ausente do manifesto",
    "QAORQ-003": "endpoint do manifesto sem correspondente no backend",
    "QAORQ-010": "saída do modelo não valida contra o contrato Pydantic do estágio",
    "QAORQ-011": "o modelo não devolveu JSON no formato pedido",
    "QAORQ-020": "prettier reprovou a formatação",
    "QAORQ-021": "eslint reprovou o código",
    "QAORQ-022": "formatador configurado mas ausente do PATH",
    "QAORQ-030": "categoria declarada em cats sem nenhum `it` que a cubra",
    "QAORQ-031": "o recurso tem rota de exclusão e o `_support/` não implementa limpeza",
    "QAORQ-032": "a limpeza chama DELETE e não confere o resultado (cleanup cego)",
    "QAORQ-033": "a exclusão exige pré-condição e a limpeza não envia cabeçalho condicional",
    "QAORQ-040": "schema preservado do consumidor não declara campo que o mapeador achou",
    "QAORQ-050": "o plano de cenários não cobre uma categoria que o gabarito declara em cats",
    "QAORQ-051": "cenário de escrita sem prova de estado no espera (releitura ausente)",
    "QAORQ-052": "variações excedentes do mesmo campo na mesma categoria do plano",
    "QAORQ-060": "evidência do dossiê aponta arquivo ou linha que não existe no backend",
    "QAORQ-061": "o dossiê cita endpoint que não está no gabarito",
    "QAORQ-062": "a checklist negativa do dossiê não respondeu todos os aspectos",
    "QAORQ-063": "o mapeador não emitiu o dossiê do recurso",
    "QAORQ-070": "estrutura da suíte fora do padrão describe → context → it",
    "QAORQ-071": "título de teste fora do padrão de nomes",
    "QAORQ-072": "`expect` sem mensagem explicativa",
    "QAORQ-073": "o spec fura as camadas: HTTP direto, import proibido ou comando global",
    "QAORQ-074": "espera de tempo fixo ou condicional decidindo o que o teste verifica",
    "QAORQ-075": "URL, credencial ou segredo literal no código do teste",
    "QAORQ-076": "asserção que só prova existência, e passaria com o backend devolvendo lixo",
    "QAORQ-077": "identificador de uma letra",
    "QAORQ-078": "arquivo gerado sem o comentário de apresentação no topo",
    "QAORQ-079": "varredura por campo escrita `it` a `it`, sem tabela data-driven",
    "QAORQ-080": "spec que cria massa e não chama a limpeza",
    "QAORQ-081": "identificador chamado sem import nem declaração que o forneça",
    "QAORQ-082": "campo do schema de entrada sem nenhum `it` que o exercite",
}


def catalogo_markdown() -> str:
    """A tabela dos códigos `QAORQ-`, como ela é publicada.

    Gerada a partir do dicionário acima, e não escrita à mão, pelo mesmo motivo do
    catálogo de eventos: uma tabela mantida em paralelo diverge, e diverge no código
    novo — que é justamente o que ninguém conhece de cabeça.

    Os `QAAPI-` não saem daqui. Eles são da skill, e inventar uma tabela nossa para
    eles criaria uma segunda fonte de verdade sobre um contrato que não é nosso.
    """
    linhas = ["| Código | O que significa |", "| --- | --- |"]
    linhas += [
        f"| `{codigo}` | {texto} |" for codigo, texto in sorted(CODIGOS_DO_ORQUESTRADOR.items())
    ]
    return "\n".join(linhas)
