"""Tudo que foi prometido virou teste? — reconciliação determinística.

É a maior perda do desacoplamento da skill voltando, e é o que separa este produto
de um gerador de testes qualquer: a promessa é **cobertura provada por
verificador**, e até aqui quem respondia por ela era o `QAORQ-050` — que mede o
PLANO, não o código. Entre planejar e entregar não havia ninguém.

A conta
-------
* **Denominador de categoria**: as `cats` que o gabarito declara para cada
  endpoint. O que está em `naoAplica` já foi justificado pelo mapeador e não conta.
* **Denominador de campo**: as `properties` do schema de entrada que o endpoint
  declara. O que está em `campos` do gabarito tem justificativa escrita e não conta.
* **Numerador**: as tags `@cat` e `@campo` dos specs, com as varreduras expandidas
  pelo resolvedor de `tags_cypress`.

Por que a tag e não o corpo do teste
------------------------------------
Nenhum parser estático deduz do corpo de um `it` qual campo recebeu o valor
inválido, nem qual categoria ele pretende provar. A tag é a declaração de intenção
— e é por isso que a norma a exige em toda `it`. Ela não prova que o teste é bom;
prova que ele **existe** para aquilo que foi prometido. Julgar se é bom continua
sendo outra conversa, e não é desta camada.

Quando a conta fica incompleta
------------------------------
Tag cuja interpolação não resolve estaticamente vira `dinamicas` no parser. Nesse
caso o veredito inteiro cai para **aviso**: um par ausente pode estar coberto por
uma tag que ninguém conseguiu ler, e reprovar sobre isso mandaria o executor
reescrever um teste que já existe. Falso positivo aqui é volta de reparo paga —
a mesma assimetria de `gates/limpeza.py` e `gates/padrao_cypress.py`.
"""

from __future__ import annotations

from typing import Any, cast

from orquestrador.analise_estatica.tags_cypress import extrair_tags
from orquestrador.dominio.manifesto import EndpointManifesto, Manifesto
from orquestrador.dominio.veredito import ResultadoGate, Violacao

NOME = "gate_b"

CODIGO_CATEGORIA = "QAORQ-030"
CODIGO_CAMPO = "QAORQ-082"


def conferir_cobertura(
    manifesto: Manifesto | None,
    specs: dict[str, str],
    schemas: dict[str, dict[str, Any]],
) -> ResultadoGate:
    """O que o gabarito prometeu contra o que os specs declaram cobrir.

    Sem gabarito não há denominador, e sem denominador não há conta: aprova, em vez
    de inventar uma régua a partir do que o executor resolveu escrever.
    """
    if manifesto is None or not specs:
        return ResultadoGate.aprovado_por(gate=NOME)

    pares: set[tuple[str, str]] = set()
    campos: set[tuple[str, str]] = set()
    dinamicas = 0
    for fonte in specs.values():
        tags = extrair_tags(fonte)
        pares |= tags.pares
        campos |= tags.campos
        dinamicas += tags.dinamicas

    achados: list[Violacao] = []
    for endpoint in manifesto.endpoints:
        achados += _categorias_faltantes(endpoint, pares)
        achados += _campos_faltantes(endpoint, campos, schemas)

    if not achados:
        return ResultadoGate.aprovado_por(gate=NOME)

    if dinamicas:
        # Conta incompleta não reprova. Ver a docstring do módulo.
        aviso = Violacao(
            codigo=CODIGO_CATEGORIA,
            mensagem=(
                f"{len(achados)} lacuna(s) de cobertura NÃO confirmadas: {dinamicas} tag(s) "
                "não resolvem estaticamente, então o que parece faltar pode estar coberto "
                "por uma delas. Para a conta fechar, a varredura precisa ser `forEach` "
                "sobre lista literal no próprio spec, com interpolação de identificador "
                "simples."
            ),
        )
        return ResultadoGate.aprovado_por(avisos=[aviso, *achados], gate=NOME)

    return ResultadoGate.reprovado_por(achados, gate=NOME)


def _categorias_faltantes(
    endpoint: EndpointManifesto, pares: set[tuple[str, str]]
) -> list[Violacao]:
    faltando = [cat for cat in endpoint.cats if (endpoint.endpoint, cat) not in pares]
    if not faltando:
        return []
    return [
        Violacao(
            codigo=CODIGO_CATEGORIA,
            arquivo="_support/cobertura.json",
            mensagem=(
                f"{endpoint.endpoint}: o gabarito declara {cat} e nenhum `it` a cobre. "
                "Escreva o teste com a tag `@cat` correspondente, ou registre a "
                "dispensa em `naoAplica` com a justificativa."
            ),
        )
        for cat in faltando
    ]


def _campos_faltantes(
    endpoint: EndpointManifesto,
    campos: set[tuple[str, str]],
    schemas: dict[str, dict[str, Any]],
) -> list[Violacao]:
    """Campo do schema de entrada sem nenhum `it` que o exercite.

    O denominador é o schema porque ele é o contrato do que a API aceita; a lista
    de campos do gabarito seria a nossa opinião sobre ele.
    """
    if endpoint.schema_entrada is None:
        return []
    schema = schemas.get(endpoint.schema_entrada)
    if not schema:
        return []
    propriedades = schema.get("properties")
    if not isinstance(propriedades, dict):
        return []
    # `cast` e não `str(chave)`: JSON Schema tem chave de objeto sempre string, e
    # converter escondria um schema malformado em vez de deixá-lo aparecer.
    nomes: list[str] = list(cast(dict[str, Any], propriedades))

    dispensados = set(endpoint.campos or {})
    faltando = [
        campo
        for campo in nomes
        if campo not in dispensados and (endpoint.endpoint, campo) not in campos
    ]
    return [
        Violacao(
            codigo=CODIGO_CAMPO,
            arquivo="_support/cobertura.json",
            mensagem=(
                f"{endpoint.endpoint}: o schema `{endpoint.schema_entrada}` declara o "
                f"campo `{campo}` e nenhum `it` o exercita. Um `@campo {campo}` numa "
                "varredura de CAT-02/03/04 resolve, ou registre a dispensa em `campos` "
                "com a justificativa."
            ),
        )
        for campo in faltando
    ]
