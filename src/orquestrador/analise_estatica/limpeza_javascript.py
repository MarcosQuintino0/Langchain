"""O que o `_support/` gerado faz ao apagar massa de teste — parser puro.

Sem dependência nenhuma do projeto: entra o texto dos módulos de apoio, sai um
`LimpezaEncontrada` com fatos. Quem decide se esses fatos bastam é `gates/`.

Por que isto existe
-------------------
A receita de limpeza é calculada deterministicamente (`dominio/limpeza.py`) e
entregue ao executor — e até aqui ninguém conferia se ele a seguiu. Cleanup que
chama `DELETE` e não olha a resposta é o defeito mais caro que existe nesta
suíte: ele **parece** funcionar, a massa vai se acumulando no ambiente, e um dia
a suíte quebra inteira por colisão de unicidade, num teste que não tem relação
nenhuma com a causa. Era a única parte da promessa "cobertura provada" que
seguia sendo esperança.

O que este módulo NÃO faz
-------------------------
Não interpreta JavaScript de verdade. Ele responde perguntas de superfície sobre
o texto — há um DELETE? aparece este cabeçalho? existe asserção perto dele? — e
essa limitação é deliberada: um parser completo de JS aqui seria um projeto
próprio, e o que se quer pegar (cleanup cego) é grosseiro o bastante para
aparecer na superfície. A consequência assumida é que o falso negativo existe:
código muito indireto pode escapar. O falso POSITIVO é o que se evita a todo
custo, porque ele reprovaria suíte correta — por isso cada pergunta aceita
várias formas de escrever a mesma coisa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["LimpezaEncontrada", "analisar_limpeza"]

# `cy.request({ method: 'DELETE' })`, `cy.request('DELETE', url)` e a forma
# encadeada `.delete(`. Três dialetos, porque o executor escolhe o dele.
_DELETE = re.compile(
    r"""method\s*:\s*['"`]DELETE['"`]      # method: 'DELETE'
      | request\s*\(\s*['"`]DELETE['"`]    # cy.request('DELETE', ...)
      | \.\s*delete\s*\(                   # api.delete(...)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Qualquer forma de olhar o desfecho: `expect(...)`, `.its('status')`,
# `should('eq', 204)`, `assert(...)`, ou comparação direta com `status`.
_CONFERE_RESULTADO = re.compile(
    r"""expect\s*\(
      | \bshould\s*\(
      | \bassert\w*\s*\(
      | \.\s*its\s*\(\s*['"`]status
      | \bstatus\s*[=!]==?
      | \bstatus\s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# `failOnStatusCode: false` desarma a checagem automática do Cypress. Não é
# defeito por si — é como se trata um 409 esperado —, mas exige asserção própria.
_SEM_FALHA_AUTOMATICA = re.compile(r"failOnStatusCode\s*:\s*false", re.IGNORECASE)

# Engolir de verdade: `catch` que não faz nada, ou que só loga.
_CATCH_VAZIO = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*(?://[^\n]*\s*)*\}")

_COMENTARIO_DE_LINHA = re.compile(r"//[^\n]*")
_COMENTARIO_DE_BLOCO = re.compile(r"/\*.*?\*/", re.DOTALL)


def _sem_comentarios(texto: str) -> str:
    """Comentário não é código: `// TODO: apagar com If-Match` não implementa nada."""
    return _COMENTARIO_DE_LINHA.sub(" ", _COMENTARIO_DE_BLOCO.sub(" ", texto))


@dataclass(frozen=True)
class LimpezaEncontrada:
    """Os fatos de superfície sobre a limpeza no `_support/` de um recurso."""

    tem_delete: bool = False
    confere_resultado: bool = False
    desarma_falha_automatica: bool = False
    tem_catch_vazio: bool = False
    cabecalhos: frozenset[str] = field(default_factory=frozenset[str])

    @property
    def engole_falha(self) -> bool:
        """Chama DELETE e não olha o desfecho — o cleanup que mente.

        Duas formas: desarmar a checagem automática do Cypress sem asserção
        própria, ou capturar a exceção e não fazer nada com ela.
        """
        if not self.tem_delete:
            return False
        return self.tem_catch_vazio or (
            self.desarma_falha_automatica and not self.confere_resultado
        )


def analisar_limpeza(
    modulos: dict[str, str], cabecalhos_procurados: list[str]
) -> LimpezaEncontrada:
    """Lê os módulos de apoio e responde o que se sabe sobre a limpeza.

    `cabecalhos_procurados` vem do dossiê — são os cabeçalhos que a exclusão do
    recurso exige. A busca é por nome, sem diferenciar caixa, porque o executor
    pode escrever `If-Match`, `'if-match'` ou montá-lo numa constante.
    """
    texto = _sem_comentarios("\n".join(modulos.values()))
    presentes = {
        cabecalho
        for cabecalho in cabecalhos_procurados
        if re.search(re.escape(cabecalho), texto, re.IGNORECASE)
    }
    return LimpezaEncontrada(
        tem_delete=bool(_DELETE.search(texto)),
        confere_resultado=bool(_CONFERE_RESULTADO.search(texto)),
        desarma_falha_automatica=bool(_SEM_FALHA_AUTOMATICA.search(texto)),
        tem_catch_vazio=bool(_CATCH_VAZIO.search(texto)),
        cabecalhos=frozenset(presentes),
    )
