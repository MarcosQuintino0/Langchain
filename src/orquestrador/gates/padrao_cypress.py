"""O código gerado obedece à norma de escrita? — checagem determinística.

`prompts/padrao-de-codigo-cypress.md` é a norma; este módulo é o pedaço dela que
um script consegue provar. O resto continua valendo e continua sendo julgamento.

Por que ele existe
------------------
Medido na suíte publicada de `customers`, uma execução, a mesma regra escrita no
prompt ("toda `expect` leva mensagem"): `crud.cy.js` obedeceu em 0 de 91,
`seguranca.cy.js` em 53 de 53, e `_support/asserts.js` em 0 de 20. De 0% a 100%
no mesmo run. O modelo conhece a regra — o que faltava era quem a cobrasse. Regra
sem fiscal é decoração, e o prompt até ameaçava gates que já não existiam.

O critério de entrada de cada regra
-----------------------------------
**Falso positivo é o defeito caro aqui**, não falso negativo: cada violação
inventada é uma volta de reparo paga, e o modelo gastando resposta para consertar
o que estava certo. Então só entra a regra que decide sem interpretar — e quando
uma delas precisa escolher entre acusar de menos e acusar de mais, ela acusa de
menos. É a mesma assimetria de `gates/limpeza.py`.

Por isso ficaram de fora, e estão em `docs/arquitetura/pendencias.md`: se o import
do spec veio de camada permitida (depende da superfície do projeto, que varia por
cliente), se a mensagem da asserção **explica** alguma coisa, e se o nome do teste
descreve comportamento em vez de mecanismo.
"""

from __future__ import annotations

import re

from orquestrador.analise_estatica.estrutura_de_suite import (
    Bloco,
    chamadas,
    extrair_estrutura,
    literais,
    neutralizar,
    percorrer,
)
from orquestrador.dominio.veredito import ResultadoGate, Violacao

NOME = "gate_b"

CODIGO_ESTRUTURA = "QAORQ-070"
CODIGO_TITULO = "QAORQ-071"
CODIGO_SEM_MENSAGEM = "QAORQ-072"
CODIGO_CAMADA = "QAORQ-073"
CODIGO_DETERMINISMO = "QAORQ-074"
CODIGO_SEGREDO = "QAORQ-075"
CODIGO_ORACULO = "QAORQ-076"
CODIGO_IDENTIFICADOR = "QAORQ-077"

LIMITE_DO_TITULO = 80

# O `context` declara uma circunstância, e circunstância em português começa com
# "quando". Lista fechada de uma palavra: qualquer sinônimo aceito aqui devolveria
# ao modelo a liberdade que a regra existe para tirar.
ABERTURA_DE_CONTEXTO = "quando"

# Verbo que descreve o teste em vez do sistema, ou que não descreve nada. `deve` é
# o caso de volume: 187 de 187 títulos da suíte publicada começavam com ele.
_VERBO_PROIBIDO = re.compile(
    r"^\s*(deve|devem|testa|testar|verifica|verificar|checa|checar|valida|validar"
    r"|garante|garantir|should|test)\b",
    re.IGNORECASE,
)
_CATEGORIA_NO_TITULO = re.compile(r"\bCAT-\d{2}\b")
_RESULTADO_NUMERICO = re.compile(r"\bretorn[ao]r?\s+(?:o\s+)?\d{3}\b", re.IGNORECASE)

# `cy.wait(300)` — espera de relógio. `cy.wait("@alias")` é intercept, e é legítimo.
_ESPERA_FIXA = re.compile(r"cy\.wait\s*\(\s*\d")
_CONDICIONAL = re.compile(r"(?<![\w$])(if|else)(?![\w$])")

_URL_LITERAL = re.compile(r"https?://", re.IGNORECASE)

# Segredo se reconhece pela FORMA do valor, não pelo nome da variável. A primeira
# versão desta regra casava `token`, `senha` e afins seguidos de literal, e acusou
# `token: "token-invalido-aleatorio"` — que é massa de teste negativo, escrita
# exatamente como deve ser. Nome de variável não distingue credencial de dado de
# teste; `Bearer` e o cabeçalho de um JWT distinguem.
_SEGREDO_LITERAL = re.compile(r"^(?:Bearer\s+\S|eyJ[\w-]{10,}\.)", re.IGNORECASE)

# Uma letra só, exceto `_`, que é o descarte convencional em desestruturação — e
# só quando o corpo do callback é um BLOCO. `itens.map((i) => i.externalCode)` cabe
# numa linha e não custa nada a quem lê; `.then((r) => { ...vinte linhas... })`
# obriga a subir o arquivo para lembrar o que é `r`, que é a razão da regra. Medido:
# das 31 acusações da primeira execução real, 31 eram do primeiro caso.
_PARAMETRO_CURTO = re.compile(r"(?:\(\s*([A-Za-z$])\s*\)|(?<![\w$.])([A-Za-z$]))\s*=>\s*\{")
_DECLARACAO_CURTA = re.compile(r"(?<![\w$])(?:const|let|var)\s+([A-Za-z$])\s*=[^=]")
# `for (let i = 0; ...)` fica de fora: índice de laço é o único nome de uma letra
# que a comunidade inteira lê sem hesitar, e o escopo dele é a própria linha. O
# recuo é conferido em código porque `re` não aceita lookbehind de tamanho variável.
_ABERTURA_DE_LACO = re.compile(r"for\s*\(\s*$")

# Asserção que passa com o backend devolvendo qualquer coisa daquele formato.
_ORACULO_FRACO = re.compile(
    r"to\.exist"
    r"|to\.be\.ok"
    r"|not\.be\.(?:undefined|null)"
    r"|to\.be\.an?\s*\("  # só o formato: "é um array"
    r"|(?:greaterThan|above)\s*\(\s*0\s*\)"  # "veio pelo menos um"
    r"|least\s*\(\s*1\s*\)"
    r"|(?:lessThan|below)\s*\(\s*[45]\d\d\s*\)",  # "pelo menos não deu erro"
    re.IGNORECASE,
)

SUFIXO_SPEC = ".cy.js"

# `import { validarX, validarY } from "./_support/asserts.js"` — o que o spec usa
# como oráculo sem escrever `expect`. Casado no fonte ORIGINAL: a neutralização
# apaga o conteúdo das aspas, e foi assim que a primeira versão desta regra nunca
# encontrou import nenhum e seguiu acusando o probe do CAT-04.
_IMPORT_DE_ASSERTS = re.compile(
    r"import\s*\{(?P<nomes>[^}]*)\}\s*from\s*[\"'][^\"']*asserts[^\"']*[\"']"
)


def conferir_padrao(arquivos: dict[str, str]) -> ResultadoGate:
    """As violações de norma nos arquivos do recurso.

    `arquivos` mapeia caminho relativo ao recurso → conteúdo. Dicionário vazio
    aprova: "não há código" não é "o código está errado", e quem cobra spec
    ausente é outro gate.
    """
    violacoes: list[Violacao] = []
    for caminho in sorted(arquivos):
        violacoes += _conferir_arquivo(caminho, arquivos[caminho])
    if violacoes:
        return ResultadoGate.reprovado_por(violacoes, gate=NOME)
    return ResultadoGate.aprovado_por(gate=NOME)


def _conferir_arquivo(caminho: str, fonte: str) -> list[Violacao]:
    neutro = neutralizar(fonte)
    linhas = _mapa_de_linhas(fonte)
    e_spec = caminho.endswith(SUFIXO_SPEC)

    violacoes: list[Violacao] = []
    violacoes += _assercoes_sem_mensagem(caminho, neutro, linhas)
    violacoes += _camadas(caminho, neutro, linhas)
    violacoes += _segredos(caminho, fonte, neutro, linhas)
    violacoes += _identificadores(caminho, neutro, linhas)

    if not e_spec:
        return violacoes

    blocos = extrair_estrutura(fonte)
    achatado = percorrer(blocos)
    violacoes += _estrutura(caminho, achatado)
    violacoes += _titulos(caminho, achatado)
    violacoes += _dentro_dos_testes(caminho, achatado, fonte, neutro, linhas)
    return violacoes


def _mapa_de_linhas(fonte: str) -> list[int]:
    posicoes = [0]
    for indice, caractere in enumerate(fonte):
        if caractere == "\n":
            posicoes.append(indice + 1)
    return posicoes


def _linha(posicoes: list[int], deslocamento: int) -> int:
    baixo, alto = 0, len(posicoes) - 1
    while baixo < alto:
        meio = (baixo + alto + 1) // 2
        if posicoes[meio] <= deslocamento:
            baixo = meio
        else:
            alto = meio - 1
    return baixo + 1


def _assercoes_sem_mensagem(caminho: str, neutro: str, linhas: list[int]) -> list[Violacao]:
    """`expect(valor)` sem o segundo argumento que diz o que se esperava.

    Vale também — principalmente — dentro de `_support/asserts.js`: é lá que a
    mensagem mais importa, porque quem lê o spec não vê aquela linha.
    """
    violacoes: list[Violacao] = []
    for chamada in chamadas(neutro, "expect"):
        tem_mensagem = len(chamada.argumentos) >= 2 and chamada.argumentos[1][:1] in "\"'`"
        if tem_mensagem:
            continue
        violacoes.append(
            Violacao(
                codigo=CODIGO_SEM_MENSAGEM,
                arquivo=caminho,
                linha=_linha(linhas, chamada.inicio),
                mensagem=(
                    "expect sem mensagem: a falha vai dizer só os dois valores "
                    'comparados. Use expect(valor, "o que se esperava").to...'
                ),
            )
        )
    return violacoes


def _camadas(caminho: str, neutro: str, linhas: list[int]) -> list[Violacao]:
    violacoes: list[Violacao] = []
    for termo, explicacao in (
        (
            "cy.request",
            "o recurso não fala HTTP direto: a request mora no client compartilhado "
            "do projeto, e o `_support/api.js` é quem o consome",
        ),
        (
            "Cypress.Commands.add",
            "comando global não é verificável — nenhum gate consegue provar que um "
            "spec respeitou as camadas se tudo é global. Use função importada",
        ),
    ):
        for casamento in re.finditer(re.escape(termo), neutro):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_CAMADA,
                    arquivo=caminho,
                    linha=_linha(linhas, casamento.start()),
                    mensagem=f"{termo}: {explicacao}.",
                )
            )
    return violacoes


def _segredos(caminho: str, fonte: str, neutro: str, linhas: list[int]) -> list[Violacao]:
    violacoes: list[Violacao] = []
    for deslocamento, conteudo in literais(fonte, neutro):
        if _URL_LITERAL.search(conteudo):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_SEGREDO,
                    arquivo=caminho,
                    linha=_linha(linhas, deslocamento),
                    mensagem=(
                        "endereço de API literal no código: ele vem da configuração "
                        "do projeto (Cypress.env), ou a suíte só roda num ambiente."
                    ),
                )
            )
        # `Bearer ${token}` monta o cabeçalho a partir de uma variável: o segredo
        # não está aqui. Só o literal fechado é credencial no código.
        if "${" not in conteudo and _SEGREDO_LITERAL.search(conteudo):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_SEGREDO,
                    arquivo=caminho,
                    linha=_linha(linhas, deslocamento),
                    mensagem=(
                        "credencial literal no código: identidade vem de variável "
                        "CYPRESS_, nunca do código nem de arquivo .env."
                    ),
                )
            )
    return violacoes


def _identificadores(caminho: str, neutro: str, linhas: list[int]) -> list[Violacao]:
    violacoes: list[Violacao] = []
    for padrao in (_PARAMETRO_CURTO, _DECLARACAO_CURTA):
        for casamento in padrao.finditer(neutro):
            if _ABERTURA_DE_LACO.search(neutro[max(0, casamento.start() - 8) : casamento.start()]):
                continue
            nome = next(grupo for grupo in casamento.groups() if grupo)
            violacoes.append(
                Violacao(
                    codigo=CODIGO_IDENTIFICADOR,
                    arquivo=caminho,
                    linha=_linha(linhas, casamento.start()),
                    mensagem=(
                        f"identificador `{nome}`: quem lê `{nome}.body.id` precisa subir "
                        "linhas para descobrir o que ele é. Use resposta, criacao, consulta."
                    ),
                )
            )
    return violacoes


def _estrutura(caminho: str, achatado: list[tuple[Bloco, tuple[Bloco, ...]]]) -> list[Violacao]:
    violacoes: list[Violacao] = []
    for bloco, ancestrais in achatado:
        if len(ancestrais) >= 3:
            violacoes.append(
                Violacao(
                    codigo=CODIGO_ESTRUTURA,
                    arquivo=caminho,
                    linha=bloco.linha,
                    mensagem=(
                        f"{bloco.tipo} no quarto nível: a estrutura é describe (a "
                        "operação) → context (a circunstância) → it, e mais nada."
                    ),
                )
            )
            continue
        if bloco.tipo == "context" and not bloco.titulo.strip().lower().startswith(
            ABERTURA_DE_CONTEXTO
        ):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_ESTRUTURA,
                    arquivo=caminho,
                    linha=bloco.linha,
                    mensagem=(
                        f'context "{bloco.titulo}" precisa começar com "quando": ele '
                        "nomeia a circunstância, e é o que faz os três níveis virarem "
                        "uma frase."
                    ),
                )
            )
        if bloco.tipo in {"it", "specify"} and not any(
            ancestral.tipo == "context" for ancestral in ancestrais
        ):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_ESTRUTURA,
                    arquivo=caminho,
                    linha=bloco.linha,
                    mensagem=(
                        f'o teste "{bloco.titulo}" está fora de um context. Todo teste '
                        "mora numa circunstância, mesmo que o arquivo tenha uma só."
                    ),
                )
            )
    return violacoes


def _titulos(caminho: str, achatado: list[tuple[Bloco, tuple[Bloco, ...]]]) -> list[Violacao]:
    violacoes: list[Violacao] = []
    vistos: dict[int, set[str]] = {}
    for bloco, ancestrais in achatado:
        if bloco.tipo not in {"it", "specify"}:
            continue
        titulo = bloco.titulo.strip()

        if _VERBO_PROIBIDO.match(titulo):
            violacoes.append(
                _violacao_de_titulo(
                    caminho,
                    bloco,
                    'comece pelo verbo do resultado ("recusa o cadastro sem e-mail"). '
                    '"deve" e "testa" se repetem em toda linha do relatório sem '
                    "carregar informação nenhuma",
                )
            )
        if _CATEGORIA_NO_TITULO.search(titulo):
            violacoes.append(
                _violacao_de_titulo(caminho, bloco, "a categoria é tag, não faz parte do nome")
            )
        if _RESULTADO_NUMERICO.search(titulo):
            violacoes.append(
                _violacao_de_titulo(
                    caminho,
                    bloco,
                    "o número do status é o mecanismo; o nome diz o comportamento "
                    '("recusa o cadastro"), e o status vive na mensagem da asserção',
                )
            )
        if not bloco.dinamico and len(titulo) > LIMITE_DO_TITULO:
            violacoes.append(
                _violacao_de_titulo(
                    caminho,
                    bloco,
                    f"tem {len(titulo)} caracteres e o limite é {LIMITE_DO_TITULO}. "
                    "Título que quebra a linha do relatório deixa de ser escaneável, "
                    "que é a razão de ele existir. Não repita o que describe e "
                    "context já disseram",
                )
            )

        # A unicidade é dentro do próprio `context`: dois endpoints diferentes podem
        # ter, legitimamente, o mesmo comportamento com o mesmo nome.
        pai = id(ancestrais[-1]) if ancestrais else 0
        if not bloco.dinamico:
            if titulo in vistos.setdefault(pai, set()):
                violacoes.append(
                    _violacao_de_titulo(
                        caminho,
                        bloco,
                        "repetido dentro do mesmo context: dois testes com o mesmo "
                        "nome significam que pelo menos um não diz o que prova",
                    )
                )
            vistos[pai].add(titulo)
    return violacoes


def _violacao_de_titulo(caminho: str, bloco: Bloco, motivo: str) -> Violacao:
    return Violacao(
        codigo=CODIGO_TITULO,
        arquivo=caminho,
        linha=bloco.linha,
        mensagem=f'título "{bloco.titulo}": {motivo}.',
    )


def _dentro_dos_testes(
    caminho: str,
    achatado: list[tuple[Bloco, tuple[Bloco, ...]]],
    fonte: str,
    neutro: str,
    linhas: list[int],
) -> list[Violacao]:
    """As regras que só valem dentro do corpo de um `it`.

    Fora dele as mesmas construções são legítimas: `helpers.js` precisa de `if`
    para decidir se registra um id, e um `afterEach` lê `.status` para conferir a
    própria limpeza. O que a norma proíbe é o TESTE escolher o que verifica.
    """
    violacoes: list[Violacao] = []
    testes = [bloco for bloco, _ in achatado if bloco.tipo in {"it", "specify"}]
    expects = chamadas(neutro, "expect")
    verificadores = _verificadores_importados(fonte)

    for teste in testes:
        corpo = neutro[teste.inicio : teste.fim]
        base = teste.inicio

        for casamento in _ESPERA_FIXA.finditer(corpo):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_DETERMINISMO,
                    arquivo=caminho,
                    linha=_linha(linhas, base + casamento.start()),
                    mensagem=(
                        "cy.wait com tempo fixo: espera de relógio é a origem clássica "
                        "de teste intermitente. Espere pelo resultado, não pelo tempo."
                    ),
                )
            )
        for casamento in _CONDICIONAL.finditer(corpo):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_DETERMINISMO,
                    arquivo=caminho,
                    linha=_linha(linhas, base + casamento.start()),
                    mensagem=(
                        f"`{casamento.group(1)}` dentro do teste: quem escolhe o próprio "
                        "oráculo em tempo de execução não prova nada. Se há dois "
                        "caminhos, são dois testes."
                    ),
                )
            )

        do_teste = [chamada for chamada in expects if teste.contem(chamada.inicio)]
        if (
            do_teste
            and all(_e_fraco(neutro, chamada.fim) for chamada in do_teste)
            and not _usa_verificador_compartilhado(corpo, verificadores)
        ):
            violacoes.append(
                Violacao(
                    codigo=CODIGO_ORACULO,
                    arquivo=caminho,
                    linha=teste.linha,
                    mensagem=(
                        f'o teste "{teste.titulo}" só prova que a resposta existe ou tem '
                        "o formato esperado — ele passa com o backend devolvendo dados "
                        "errados. Verifique valor, e releia o estado depois de escrever."
                    ),
                )
            )

    for teste in testes:
        # `(?!\()` separa a LEITURA da propriedade (`resposta.status`) da chamada de
        # um helper que por acaso se chama assim (`BaseAssert.status(lista, 200)`) —
        # medido na suíte publicada, era a origem da maioria dos falsos positivos
        # desta regra, e chamar o verificador compartilhado é o certo, não o errado.
        for casamento in re.finditer(r"\.status(?![\w$(])", neutro[teste.inicio : teste.fim]):
            posicao = teste.inicio + casamento.start()
            # O critério é o STATEMENT ter uma asserção, não o `.status` cair dentro
            # dos parênteses do `expect`: em `expect([403, 404], "...")
            # .to.include(resposta.status)` a leitura está depois do fecha-parêntese
            # e continua sendo asserção. O que a regra procura é `.status` lido para
            # o teste decidir sozinho o que fazer.
            if _tem_assercao_no_statement(neutro, posicao):
                continue
            violacoes.append(
                Violacao(
                    codigo=CODIGO_CAMADA,
                    arquivo=caminho,
                    linha=_linha(linhas, posicao),
                    mensagem=(
                        "o spec lê `.status` fora de um expect: interpretar resposta é "
                        "trabalho do `_support/asserts.js`, e é lá que a mensagem de "
                        "falha precisa nascer."
                    ),
                )
            )
    return violacoes


def _tem_assercao_no_statement(neutro: str, posicao: int) -> bool:
    """O statement que contém `posicao` chama `expect`?"""
    inicio = max(neutro.rfind(";", 0, posicao), neutro.rfind("{", 0, posicao)) + 1
    fim = neutro.find(";", posicao)
    trecho = neutro[inicio : fim if fim != -1 else len(neutro)]
    return bool(re.search(r"(?<![\w$.])expect\s*\(", trecho))


def _verificadores_importados(neutro: str) -> frozenset[str]:
    """Os nomes que o spec importa de `_support/asserts.js`.

    Sem isto, um teste cuja prova mora num verificador compartilhado passaria por
    "só prova existência": o gate conta `expect`, e a camada de verificação existe
    justamente para tirar o `expect` de dentro do spec. Foi assim que a regra
    acusou o probe de magnitude do `CAT-04`, que faz `lessThan(500)` **e**
    `validarNaoVazaInterno(resposta)` — exatamente o que o contrato dele manda.
    """
    nomes: set[str] = set()
    for casamento in _IMPORT_DE_ASSERTS.finditer(neutro):
        for nome in casamento.group("nomes").split(","):
            limpo = nome.split(" as ")[-1].strip()
            if limpo:
                nomes.add(limpo)
    return frozenset(nomes)


def _usa_verificador_compartilhado(corpo: str, verificadores: frozenset[str]) -> bool:
    return any(re.search(rf"(?<![\w$.]){re.escape(nome)}\s*\(", corpo) for nome in verificadores)


def _e_fraco(neutro: str, desde: int) -> bool:
    """A cadeia de asserção que segue este `expect` só prova existência ou formato."""
    fim = neutro.find(";", desde)
    cadeia = neutro[desde : fim if fim != -1 else len(neutro)]
    return bool(_ORACULO_FRACO.search(cadeia))
