"""As invariantes de organização do pacote, verificadas por AST.

Por que este arquivo existe
---------------------------
A tabela "Onde colocar código novo" do `AGENTS.md` e a árvore de módulos do
`README.md` descrevem uma estrutura. Descrição não sustenta estrutura: um agente
que lê o `AGENTS.md` pela metade acrescenta um arquivo na raiz do pacote, o
revisor não repara, e em três meses a raiz voltou a ter doze arquivos sem dono
declarado. Foi exatamente assim que `parser.py`, `textos.py` e `javascript.py`
chegaram onde estavam.

Cada checagem aqui é a forma executável de uma regra escrita naqueles dois
documentos. Se uma delas reprovar, a resposta é **mover o código ou atualizar a
regra**, nunca afrouxar a checagem.

Escrevendo mensagem de falha
----------------------------
Quem lê estas falhas é um agente daqui a seis meses, sem o contexto de hoje.
Então toda mensagem diz **o que fazer**, não só o que está errado: qual diretório
recebe o arquivo, qual documento atualizar, qual import trocar.

Por que AST e não regex
-----------------------
Um `grep` por `import` acha a palavra em docstring, em comentário e em string de
mensagem de erro — este módulo cita `from conftest import` na própria docstring, e
uma checagem por texto reprovaria a si mesma. O que interessa é a estrutura do
programa, e é ela que o `ast` devolve.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from orquestrador.gates.codigos import CODIGOS_DO_ORQUESTRADOR

pytestmark = pytest.mark.unit

# `Path(__file__)` aqui não contraria a regra do `raiz.py`: aquela regra protege o
# **pacote**, que não pode calcular a raiz do projeto por conta própria. Este
# módulo precisa da árvore de fontes ao lado dele, não da raiz configurável — que
# `ORQUESTRADOR_RAIZ` pode apontar para outro lugar justamente quando o pacote é
# instalado fora da árvore.
DIR_TESTES = Path(__file__).resolve().parent
RAIZ_DO_REPOSITORIO = DIR_TESTES.parent
PACOTE = RAIZ_DO_REPOSITORIO / "src" / "orquestrador"
README = RAIZ_DO_REPOSITORIO / "README.md"
AGENTS = RAIZ_DO_REPOSITORIO / "AGENTS.md"

# A raiz do pacote é lista fechada: arquivo novo aqui reprova, e é para reprovar.
# Escolher diretório é escolher o motivo dominante de mudança — a tabela do
# `AGENTS.md` diz qual. A raiz não é "onde ainda não decidi".
RAIZ_PERMITIDA = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "cli.py",
        "config.py",
        "excecoes.py",
        "raiz.py",
    }
)

# Direção de dependência. A chave é o pacote; o valor, os pacotes que ele não pode
# importar. Seta ao contrário não quebra teste nenhum hoje — ela só transforma
# dois módulos independentes num par que precisa ser lido junto para sempre.
#
# A matriz é fechada: todo pacote é chave. Um pacote fora dela não tem regra de
# direção nenhuma, e a ausência se lê como permissão.
DIRECAO_PROIBIDA: dict[str, frozenset[str]] = {
    # O vocabulário comum. Não conhece **ninguém** — nem quem produz o dado, nem
    # quem decide sobre ele. `excecoes` é a única aresta permitida, e não aparece
    # aqui porque só o que está proibido é listado.
    "dominio": frozenset(
        {
            "agentes",
            "gates",
            "ferramentas",
            "llm",
            "analise_estatica",
            "observabilidade",
            "aplicacao",
            "cli",
            "config",
        }
    ),
    # Lê código-fonte e devolve dado. Se precisasse de gate ou de agente, não
    # seria análise: seria decisão.
    "analise_estatica": frozenset({"agentes", "gates", "aplicacao", "cli"}),
    # Adaptador de I/O externo. Quem decide o que fazer com a saída é o chamador.
    "ferramentas": frozenset({"agentes", "gates", "aplicacao", "cli"}),
    # Registra o que aconteceu; nunca decide fluxo.
    "observabilidade": frozenset({"agentes", "gates", "aplicacao", "cli"}),
    # Cliente, saída estruturada e montagem de prompt. Não conhece gate nem recurso.
    "llm": frozenset({"agentes", "gates", "aplicacao", "cli"}),
    # Reprova determinística. Conhece quem executa, nunca quem cria.
    "gates": frozenset({"agentes", "aplicacao", "cli"}),
    # Monta e invoca um criador LLM. Não decide aprovação, e não coordena estágios.
    "agentes": frozenset({"gates", "aplicacao", "cli"}),
    # Coordena os estágios. Conhece todo o resto; a CLI é quem o conhece.
    "aplicacao": frozenset({"cli"}),
}

CODIGO_QAORQ = re.compile(r"QAORQ-\d{3}")

# A tabela "Onde colocar código novo" do `AGENTS.md`, localizada pelo cabeçalho
# literal e não por número de linha: acrescentar uma seção antes dela não pode cegar
# a checagem.
CABECALHO_DA_TABELA = "| Diretório | Motivo dominante | O que ele não faz |"
LINHA_DA_RAIZ = "raiz do pacote"

# `★ \`dominio/\`` — a estrela marca diretório que ainda não existe.
DIRETORIO_NA_TABELA = re.compile(r"^(?P<estrela>★\s*)?`(?P<nome>[a-z_]+)/`$")
ARQUIVO_NA_CELULA = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*\.py)`")


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def modulos_de_producao() -> list[Path]:
    """Todo `.py` sob `src/orquestrador/`, em ordem estável."""
    return sorted(PACOTE.rglob("*.py"))


def caminho_no_pacote(arquivo: Path) -> str:
    """`llm/montagem.py` — o nome pelo qual a documentação chama o módulo."""
    return arquivo.relative_to(PACOTE).as_posix()


def pacotes_reais() -> set[str]:
    """Subpacotes de `orquestrador` que existem no disco.

    Mesma definição de `subpacotes_reais` em `tests/test_invariante_empacotamento.py`, que a
    usa contra o `pyproject.toml`. Duas cópias de uma definição divergem; se você
    mudar uma, mude a outra — ou funda as duas numa fixture.
    """
    return {
        diretorio.name for diretorio in PACOTE.iterdir() if (diretorio / "__init__.py").is_file()
    }


def arvore(arquivo: Path) -> ast.Module:
    return ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))


def nomes_definidos(modulo: ast.Module) -> set[str]:
    """Nomes que o módulo **define** no topo — importar não é definir."""
    definidos: set[str] = set()
    for no in modulo.body:
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            definidos.add(no.name)
        elif isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    definidos.add(alvo.id)
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            definidos.add(no.target.id)
    return definidos


def literal_de_all(modulo: ast.Module) -> list[str] | None:
    """Conteúdo de `__all__`, ou `None` se o módulo não declara um."""
    for no in modulo.body:
        # O `isinstance` fica no `if`, e não numa expressão condicional sobre
        # `no.targets`: só depois dele o nó tem `value`, e é de `Assign` que se lê o
        # literal.
        if not isinstance(no, ast.Assign):
            continue
        if not any(isinstance(alvo, ast.Name) and alvo.id == "__all__" for alvo in no.targets):
            continue
        valor = no.value
        if isinstance(valor, ast.List | ast.Tuple):
            return [
                item.value
                for item in valor.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
    return None


def pacotes_importados(modulo: ast.Module) -> set[str]:
    """Subpacotes de `orquestrador` que este módulo importa, em qualquer nível.

    `ast.walk` em vez de `modulo.body`: import dentro de função ou sob
    `TYPE_CHECKING` acopla igual, e esconder a seta lá dentro é o caminho mais
    curto para o ciclo que ninguém vê.
    """
    achados: set[str] = set()
    for no in ast.walk(modulo):
        if isinstance(no, ast.ImportFrom) and no.module:
            partes = no.module.split(".")
        elif isinstance(no, ast.Import):
            for alias in no.names:
                partes = alias.name.split(".")
                if len(partes) >= 2 and partes[0] == "orquestrador":
                    achados.add(partes[1])
            continue
        else:
            continue
        if len(partes) >= 2 and partes[0] == "orquestrador":
            achados.add(partes[1])
    return achados


# ---------------------------------------------------------------------------
# 1 — a raiz do pacote é lista fechada
# ---------------------------------------------------------------------------


def test_raiz_do_pacote_e_lista_fechada():
    presentes = {arquivo.name for arquivo in PACOTE.glob("*.py")}

    intrusos = sorted(presentes - RAIZ_PERMITIDA)
    assert not intrusos, (
        f"arquivo(s) novo(s) na raiz de src/orquestrador/: {intrusos}.\n"
        "A raiz é lista fechada — ela não recebe arquivo novo. Escolha o diretório "
        "pelo motivo dominante de mudança, usando a tabela 'Onde colocar código "
        "novo' do AGENTS.md:\n"
        "  dominio/          contrato e regra pura, sem I/O\n"
        "  aplicacao/        coordena estágios e persistência\n"
        "  agentes/          monta e invoca um criador LLM\n"
        "  gates/            reprova determinística, sem LLM\n"
        "  ferramentas/      adaptador de disco, subprocesso e CLI externa\n"
        "  analise_estatica/ lê código-fonte sem executar\n"
        "  llm/              cliente, saída estruturada e montagem de prompt\n"
        "  observabilidade/  eventos e métricas\n"
        "Se o arquivo realmente pertence à raiz, a decisão é de arquitetura: "
        "atualize a tabela do AGENTS.md, a árvore do README.md e RAIZ_PERMITIDA "
        "aqui, na mesma mudança."
    )

    sumidos = sorted(RAIZ_PERMITIDA - presentes)
    assert not sumidos, (
        f"RAIZ_PERMITIDA lista arquivo(s) que não existem mais: {sumidos}.\n"
        "Se você acabou de movê-los para um subpacote, remova-os de RAIZ_PERMITIDA "
        "(a lista só encolhe) e atualize a árvore do README.md."
    )


# `test_a_raiz_ainda_carrega_o_que_sai_na_etapa_6` foi apagado aqui, junto com a
# constante `SAEM_NA_ETAPA_6`. Ele existia como lembrete executável de que a lista
# fechada de então não era a de destino — `contratos.py` virou `dominio/`,
# `pipeline.py` e `simulacao.py` viraram `aplicacao/`. Com o movimento feito, ele
# só podia passar, e teste que só pode passar não protege nada: ocupa uma linha na
# saída e ensina que a suíte tem itens decorativos.


# ---------------------------------------------------------------------------
# 2 — `__init__.py` não reexporta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arquivo", sorted(PACOTE.rglob("__init__.py")), ids=lambda p: caminho_no_pacote(p)
)
def test_init_nao_reexporta_nome_importado(arquivo: Path):
    modulo = arvore(arquivo)
    nome = caminho_no_pacote(arquivo)

    importados = [
        no
        for no in ast.walk(modulo)
        if isinstance(no, ast.Import | ast.ImportFrom)
        and not (isinstance(no, ast.ImportFrom) and no.module == "__future__")
    ]
    assert not importados, (
        f"{nome} importa nome de outro módulo (linha(s) "
        f"{[no.lineno for no in importados]}).\n"
        "Um `__init__.py` que reexporta cria uma segunda rota de import para o "
        "mesmo símbolo, e com duas rotas some a resposta para 'quem é o dono "
        "disto' — além de fazer `import orquestrador.gates` puxar todos os gates "
        "só para ler um código de violação.\n"
        "O que fazer: apague o import daqui e importe do módulo que define o "
        "símbolo (`from orquestrador.gates.saidas import ...`, não "
        "`from orquestrador.gates import ...`)."
    )

    assert literal_de_all(modulo) is None, (
        f"{nome} declara __all__.\n"
        "Num `__init__.py` isso só serve para reexportar. O pacote é um diretório, "
        "não uma fachada: deixe só a docstring que explica a fronteira e, no "
        "máximo, uma constante própria do pacote."
    )


# ---------------------------------------------------------------------------
# 3 — `__all__` não promete o que o módulo não define
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arquivo", modulos_de_producao(), ids=lambda p: caminho_no_pacote(p))
def test_all_so_lista_simbolo_que_o_modulo_define(arquivo: Path):
    modulo = arvore(arquivo)
    declarados = literal_de_all(modulo)
    if declarados is None:
        return

    falsos = sorted(set(declarados) - nomes_definidos(modulo))
    assert not falsos, (
        f"{caminho_no_pacote(arquivo)} declara em __all__ símbolo(s) que não "
        f"define: {falsos}.\n"
        "Um `__all__` que lista nome importado faz este módulo parecer o dono de "
        "algo que mora em outro lugar — é assim que uma busca por 'quem define X' "
        "para no arquivo errado.\n"
        "O que fazer: tire o nome do __all__ e deixe quem precisa dele importar do "
        "módulo que o define. Se ele realmente devia nascer aqui, mova a definição."
    )


# ---------------------------------------------------------------------------
# 4 — direção de dependência
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arquivo",
    [
        arquivo
        for arquivo in modulos_de_producao()
        if arquivo.relative_to(PACOTE).parts[0] in DIRECAO_PROIBIDA
    ],
    ids=lambda p: caminho_no_pacote(p),
)
def test_direcao_de_dependencia(arquivo: Path):
    pacote = arquivo.relative_to(PACOTE).parts[0]
    proibidos = DIRECAO_PROIBIDA[pacote]

    violados = sorted(pacotes_importados(arvore(arquivo)) & proibidos)
    assert not violados, (
        f"{caminho_no_pacote(arquivo)} importa {violados}, e `{pacote}/` está "
        f"proibido de depender de {sorted(proibidos)}.\n"
        "A seta aponta para baixo: quem decide (gates, agentes, pipeline) conhece "
        "quem executa (ferramentas, análise estática, observabilidade), nunca o "
        "contrário. Invertida, ela impede testar a camada de baixo sozinha e "
        "prepara o ciclo de import.\n"
        "O que fazer: passe o dado pronto como argumento em vez de importar quem "
        "o produz, ou mova a parte que precisa da decisão para o chamador. Se o "
        "acoplamento for mesmo necessário, é mudança de arquitetura: atualize "
        "DIRECAO_PROIBIDA e a tabela do AGENTS.md junto."
    )


def test_direcao_proibida_so_cita_pacote_que_existe():
    """A matriz não pode citar pacote que não existe — nem chave, nem valor.

    O valor é comparado contra o **segundo componente** do módulo importado, então
    ele deixa de casar no dia em que um módulo de topo vira subpacote: proibir
    `"pipeline"` funciona enquanto for `orquestrador.pipeline`, e passa a não
    proibir nada quando virar `orquestrador.aplicacao.pipeline` — sem que teste
    algum reprove. Esta checagem é a que transforma esse silêncio em falha.
    """
    reais = pacotes_reais() | {arquivo.stem for arquivo in PACOTE.glob("*.py")}
    citados = set(DIRECAO_PROIBIDA) | {
        valor for valores in DIRECAO_PROIBIDA.values() for valor in valores
    }

    fantasmas = sorted(citados - reais)
    assert not fantasmas, (
        f"DIRECAO_PROIBIDA cita {fantasmas}, que não é pacote nem módulo de "
        "src/orquestrador/.\n"
        "Uma entrada que não corresponde a nada nunca reprova — ela parece uma "
        "regra e não é. Se o alvo mudou de nome ou virou subpacote, atualize a "
        "chave e o valor; se a regra deixou de fazer sentido, remova a entrada."
    )


# Módulos cuja simples presença num `import` de `dominio/` já é acesso ao mundo.
MODULOS_DE_FORA = frozenset(
    {"subprocess", "os", "io", "shutil", "tempfile", "socket", "urllib", "requests", "httpx"}
)

# Métodos de `Path` (e afins) que leem ou escrevem. `resolve` e `expanduser` entram
# porque consultam o sistema de arquivos e o ambiente — `resolve()` segue link
# simbólico, e é justamente por isso que o confinamento de `ferramentas/arquivos.py`
# depende dele. Álgebra de caminho pura (`/`, `.parent`, `.name`, `.with_suffix`)
# continua permitida, e é o que `Recurso` usa.
#
# `Path.replace` fica **de fora**: `str.replace` tem o mesmo nome, é comum em
# normalização de caminho (`valor.replace("\\", "/")` em `artefatos.py`), e a AST
# não distingue os dois sem inferência de tipo. Uma checagem que reprova o inocente
# é desligada na primeira vez que atrapalha; `rename` cobre a mesma intenção e não
# colide com nada.
ACESSOS_A_DISCO = frozenset(
    {
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "open",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "touch",
        "iterdir",
        "glob",
        "rglob",
        "walk",
        "exists",
        "is_file",
        "is_dir",
        "is_symlink",
        "stat",
        "lstat",
        "chmod",
        "samefile",
        "resolve",
        "expanduser",
        "cwd",
        "home",
    }
)


@pytest.mark.parametrize(
    "arquivo", sorted((PACOTE / "dominio").glob("*.py")), ids=lambda p: caminho_no_pacote(p)
)
def test_dominio_nao_toca_no_disco(arquivo: Path):
    """A célula "O que ele não faz" de `dominio/` no AGENTS.md, executável.

    A regra é sobre **acesso**, não sobre o tipo: `Path` como anotação e como
    álgebra (`/`, `.parent`, `.with_suffix`) é permitido, porque é o que `Recurso`
    faz e proibir isso esvaziaria o pacote. O que não pode é o módulo consultar o
    mundo — e é essa fronteira que decide se um contrato pode ser construído e
    validado sem nenhum arquivo por perto.

    Falso positivo conhecido e aceito: um método **nosso** chamado `resolve` ou
    `open`. Não existe nenhum; se aparecer, a resposta é renomeá-lo, não afrouxar
    esta lista.
    """
    modulo = arvore(arquivo)
    nome = caminho_no_pacote(arquivo)

    importados = sorted(pacotes_importados(modulo) - {"dominio", "excecoes"})
    assert not importados, (
        f"{nome} importa {importados}. `dominio/` é o vocabulário comum: ele não "
        "conhece quem produz o dado nem quem decide sobre ele, e `excecoes` é a "
        "única aresta permitida.\n"
        "O que fazer: receba o dado pronto como argumento. Se a regra precisa "
        "mesmo de I/O, ela não é regra pura — mova-a para o chamador."
    )

    raizes_importadas = {
        alias.name.split(".")[0]
        for no in ast.walk(modulo)
        if isinstance(no, ast.Import)
        for alias in no.names
    } | {
        (no.module or "").split(".")[0] for no in ast.walk(modulo) if isinstance(no, ast.ImportFrom)
    }
    de_fora = sorted(raizes_importadas & MODULOS_DE_FORA)
    assert not de_fora, (
        f"{nome} importa {de_fora}, que é acesso ao sistema.\n"
        "Contrato que precisa de disco, processo ou rede para se validar não é "
        "contrato: é ferramenta com nome errado."
    )

    chamadas: list[str] = []
    for no in ast.walk(modulo):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "open":
            chamadas.append(f"linha {no.lineno}: open(...)")
        if (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr in ACESSOS_A_DISCO
        ):
            chamadas.append(f"linha {no.lineno}: .{no.func.attr}(...)")
    assert not chamadas, (
        f"{nome} consulta o sistema de arquivos:\n  " + "\n  ".join(sorted(chamadas)) + "\n"
        "`Path` aqui é valor, não acesso: montar caminho pode, abrir não. Quem "
        "abre é `ferramentas/`, e é lá que mora o confinamento."
    )


# ---------------------------------------------------------------------------
# 5 — todo código QAORQ vem do catálogo
# ---------------------------------------------------------------------------


def test_todo_codigo_qaorq_esta_catalogado():
    fora: dict[str, set[str]] = {}
    for arquivo in modulos_de_producao():
        if caminho_no_pacote(arquivo) == "gates/codigos.py":
            continue
        achados = set(CODIGO_QAORQ.findall(arquivo.read_text(encoding="utf-8")))
        desconhecidos = achados - set(CODIGOS_DO_ORQUESTRADOR)
        if desconhecidos:
            fora[caminho_no_pacote(arquivo)] = desconhecidos

    assert not fora, (
        f"código(s) QAORQ- emitido(s) sem estar no catálogo: {fora}.\n"
        "Código não catalogado vira folclore: ele aparece num delta, alguém procura "
        "o significado, não acha, e passa a adivinhar pelo contexto.\n"
        "O que fazer: acrescente a entrada em src/orquestrador/gates/codigos.py "
        "(CODIGOS_DO_ORQUESTRADOR) com a descrição de uma linha, e a linha "
        "correspondente na tabela 'Códigos de violação' do README.md."
    )


# ---------------------------------------------------------------------------
# 6 — a árvore do README é a árvore real
# ---------------------------------------------------------------------------


def blocos_cercados(arquivo: Path) -> list[str]:
    """Blocos de código do Markdown, alternando na cerca.

    Varredura por linha em vez de uma expressão sobre o texto inteiro: alguns
    blocos abrem com linguagem (```bash) e outros não, e um casamento por par de
    cercas pula os primeiros — o que emenda o fim de um bloco com o começo do
    seguinte e faz esta checagem ler prosa como se fosse árvore.
    """
    blocos: list[str] = []
    atual: list[str] | None = None
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if linha.lstrip().startswith("```"):
            if atual is None:
                atual = []
            else:
                blocos.append("\n".join(atual))
                atual = None
            continue
        if atual is not None:
            atual.append(linha)
    return blocos


def modulos_na_arvore_do_readme() -> set[str]:
    """Caminhos `.py` que a árvore de `## Estrutura` do README declara.

    A árvore é indentada, então o caminho de cada arquivo é reconstruído pela
    pilha de diretórios — comparar só o nome do arquivo deixaria passar um módulo
    listado no pacote errado, que é justamente o erro que manda o leitor procurar
    no lugar errado.
    """
    arvores = [bloco for bloco in blocos_cercados(README) if "src/orquestrador/" in bloco]
    assert arvores, (
        "não achei a árvore de módulos no README.md.\n"
        "Ela é um bloco de código cercado por ``` que contém a linha "
        "'src/orquestrador/'. Se você a removeu ou trocou a cerca por outra "
        "linguagem, esta checagem fica cega — restaure a árvore ou ajuste este "
        "teste junto."
    )

    declarados: set[str] = set()
    for bloco in arvores:
        pilha: list[tuple[int, str]] = []
        dentro_do_pacote = False
        for linha in bloco.splitlines():
            if not linha.strip():
                continue
            recuo = len(linha) - len(linha.lstrip())
            # A anotação depois do nome é prosa, não caminho.
            item = linha.strip().split()[0]

            if item == "src/orquestrador/":
                dentro_do_pacote, pilha = True, [(recuo, "")]
                continue
            if not dentro_do_pacote:
                continue
            # Voltou ao nível da raiz do bloco: saiu do pacote.
            if recuo <= pilha[0][0]:
                dentro_do_pacote = False
                continue

            while len(pilha) > 1 and recuo <= pilha[-1][0]:
                pilha.pop()
            if item.endswith("/"):
                pilha.append((recuo, pilha[-1][1] + item))
            elif item.endswith(".py"):
                declarados.add(pilha[-1][1] + item)
    return declarados


def test_a_arvore_do_readme_lista_todo_modulo_de_producao():
    reais = {caminho_no_pacote(arquivo) for arquivo in modulos_de_producao()}
    declarados = modulos_na_arvore_do_readme()

    faltando = sorted(reais - declarados)
    assert not faltando, (
        f"módulo(s) de produção ausente(s) da árvore do README.md: {faltando}.\n"
        "Módulo que não aparece na árvore é módulo que ninguém acha sem `grep` — "
        "foi a omissão de javascript.py e de superficie.py que fez um revisor "
        "externo procurar arquivo no lugar errado.\n"
        "O que fazer: acrescente a linha na árvore da seção '## Estrutura' do "
        "README.md, com a descrição curta do que o módulo é dono."
    )


def test_a_arvore_do_readme_nao_lista_modulo_que_nao_existe():
    reais = {caminho_no_pacote(arquivo) for arquivo in modulos_de_producao()}
    declarados = modulos_na_arvore_do_readme()

    fantasmas = sorted(declarados - reais)
    assert not fantasmas, (
        f"a árvore do README.md lista módulo(s) que não existem: {fantasmas}.\n"
        "Documentação que promete arquivo inexistente é pior que documentação "
        "ausente: quem procura conclui que a busca dele é que está errada.\n"
        "O que fazer: se o módulo foi movido ou renomeado, corrija a linha na "
        "árvore da seção '## Estrutura' do README.md; se ele foi apagado, remova a "
        "linha."
    )


# ---------------------------------------------------------------------------
# 7 — a tabela do AGENTS.md descreve o disco
# ---------------------------------------------------------------------------


def linhas_da_tabela_de_diretorios() -> list[list[str]]:
    """As linhas de corpo da tabela "Onde colocar código novo", célula a célula.

    Localizada pelo cabeçalho literal e não por número de linha, para que
    acrescentar uma seção antes dela não cegue a checagem.
    """
    linhas = AGENTS.read_text(encoding="utf-8").splitlines()
    assert CABECALHO_DA_TABELA in linhas, (
        f"não achei a tabela de diretórios no AGENTS.md.\n"
        f"Ela é localizada pelo cabeçalho literal {CABECALHO_DA_TABELA!r}. Se você "
        "renomeou uma coluna, esta checagem fica cega — ajuste CABECALHO_DA_TABELA "
        "aqui na mesma mudança."
    )

    corpo: list[list[str]] = []
    # +2 pula o cabeçalho e a linha de separação (`| --- | --- | --- |`).
    for linha in linhas[linhas.index(CABECALHO_DA_TABELA) + 2 :]:
        if not linha.startswith("|"):
            break
        celulas = [celula.strip() for celula in linha.strip().strip("|").split("|")]
        assert len(celulas) == 3, (
            f"linha da tabela do AGENTS.md com {len(celulas)} células: {linha!r}.\n"
            "Um `|` literal dentro de uma célula quebra a leitura — escreva-o como "
            "`\\|` ou reescreva a frase."
        )
        corpo.append(celulas)
    return corpo


def diretorios_da_tabela() -> dict[str, bool]:
    """Nome do diretório → se a linha está marcada com ★ (ainda não existe)."""
    marcados: dict[str, bool] = {}
    for primeira, _, _ in linhas_da_tabela_de_diretorios():
        casou = DIRETORIO_NA_TABELA.match(primeira)
        if casou:
            marcados[casou.group("nome")] = casou.group("estrela") is not None
    return marcados


def test_todo_subpacote_tem_linha_na_tabela():
    faltando = sorted(pacotes_reais() - set(diretorios_da_tabela()))
    assert not faltando, (
        f"subpacote(s) sem linha na tabela do AGENTS.md: {faltando}.\n"
        "Pacote sem linha é pacote sem regra de pertencimento: o próximo agente "
        "não tem como saber o que entra ali e o que não entra, e a resposta vira "
        "'o que já estiver dentro'.\n"
        "O que fazer: acrescente a linha declarando o motivo dominante de mudança "
        "e o que o pacote deliberadamente não faz."
    )


def test_a_estrela_marca_exatamente_o_que_nao_existe():
    """★ significa "planejado, ainda não criado" — e só isso.

    É esta checagem que obriga a Etapa 6 a tirar a estrela: no instante em que
    `dominio/` nasce, a linha estrelada reprova. Sem ela, o `AGENTS.md` continuaria
    dizendo "ainda não existe" sobre um diretório cheio de código, e a regra que
    manda não criá-lo por conta própria seguiria valendo contra o próprio repositório.
    """
    reais = pacotes_reais()
    tabela = diretorios_da_tabela()

    estrela_mas_existe = sorted(
        nome for nome, estrela in tabela.items() if estrela and nome in reais
    )
    assert not estrela_mas_existe, (
        f"a tabela do AGENTS.md marca com ★ diretório(s) que já existem: "
        f"{estrela_mas_existe}.\n"
        "★ quer dizer 'ainda não existe'. Se o diretório foi criado, tire a estrela "
        "da linha e apague o parágrafo que manda não criá-lo por conta própria."
    )

    sem_estrela_e_ausente = sorted(
        nome for nome, estrela in tabela.items() if not estrela and nome not in reais
    )
    assert not sem_estrela_e_ausente, (
        f"a tabela do AGENTS.md descreve diretório(s) que não existem: "
        f"{sem_estrela_e_ausente}.\n"
        "Ou o diretório foi removido e a linha deve sair, ou ele é planejado e a "
        "linha precisa da ★."
    )


def test_a_celula_da_raiz_e_a_lista_fechada():
    """O terceiro vértice: AGENTS.md ↔ RAIZ_PERMITIDA ↔ disco.

    Os outros dois já têm teste (`test_raiz_do_pacote_e_lista_fechada` fecha
    constante ↔ disco). Sem este, o `AGENTS.md` pode listar sete arquivos enquanto
    a constante lista nove, e quem lê o documento — que é justamente quem vai
    decidir onde pôr um arquivo novo — recebe a versão errada.
    """
    celulas = [
        motivo
        for primeira, motivo, _ in linhas_da_tabela_de_diretorios()
        if LINHA_DA_RAIZ in primeira
    ]
    assert len(celulas) == 1, (
        f"esperava exatamente uma linha {LINHA_DA_RAIZ!r} na tabela do AGENTS.md, "
        f"achei {len(celulas)}."
    )

    declarados = set(ARQUIVO_NA_CELULA.findall(celulas[0]))
    assert declarados == set(RAIZ_PERMITIDA), (
        "a célula 'raiz do pacote' do AGENTS.md e RAIZ_PERMITIDA discordam.\n"
        f"  só no AGENTS.md:  {sorted(declarados - RAIZ_PERMITIDA)}\n"
        f"  só na constante:  {sorted(RAIZ_PERMITIDA - declarados)}\n"
        "As duas descrevem a mesma decisão. Quando a raiz encolhe, encolhem juntas."
    )


# ---------------------------------------------------------------------------
# 8 — o nome do arquivo de teste diz o que ele protege
# ---------------------------------------------------------------------------

# `test_invariante_delta.py` cruza módulos; `test_e2e_dry_run.py` é o pipeline
# inteiro. Nenhum dos dois promete um módulo, então nenhum dos dois é conferido
# contra o disco — o prefixo é a declaração de que a promessa é outra.
PREFIXOS_SEM_MODULO = ("invariante_", "e2e_")


def composicoes(partes: list[str]) -> set[Path]:
    """Todo jeito de ler `a_b_c` como caminho: `a_b_c.py`, `a/b_c.py`, `a/b/c.py`…

    O sublinhado é ambíguo por construção — `analise_estatica_exports_javascript`
    é `analise_estatica/exports_javascript.py`, e `gates_gate_a` é
    `gates/gate_a.py`. Em vez de adivinhar onde cortar, tenta-se todo corte: é
    frouxo o bastante para nunca dar falso positivo e apertado o bastante para
    pegar módulo renomeado sem o teste correspondente.
    """
    total = len(partes)
    candidatos: set[Path] = set()
    for mascara in range(1 << (total - 1)):
        grupos: list[str] = []
        atual = [partes[0]]
        for indice in range(1, total):
            if mascara >> (indice - 1) & 1:
                grupos.append("_".join(atual))
                atual = [partes[indice]]
            else:
                atual.append(partes[indice])
        grupos.append("_".join(atual))
        candidatos.add(Path(*grupos[:-1], grupos[-1] + ".py"))
    return candidatos


def modulo_prometido_existe(nome: str) -> bool:
    """O maior prefixo do nome resolve para um módulo real.

    O resto é descritor de fatia: `test_pipeline_loop_reparo.py` promete
    `pipeline.py` e diz qual pedaço dele exercita. Sem isso, três arquivos que
    testam fatias diferentes do mesmo módulo teriam de disputar um nome só.
    """
    partes = nome.split("_")
    for tamanho in range(len(partes), 0, -1):
        if any((PACOTE / candidato).is_file() for candidato in composicoes(partes[:tamanho])):
            return True
    return False


@pytest.mark.parametrize("arquivo", sorted(DIR_TESTES.rglob("test_*.py")), ids=lambda p: p.name)
def test_nome_de_teste_aponta_para_modulo_que_existe(arquivo: Path):
    """A convenção: o nome do arquivo nomeia o que ele protege.

    * protege um módulo → caminho achatado por sublinhado, `test_gates_lacunas.py`;
    * protege uma invariante que atravessa módulos → `test_invariante_<nome>.py`;
    * exercita o pipeline inteiro → `test_e2e_<nome>.py`.

    Achatado e não `tests/gates/test_lacunas.py`: sem `__init__.py` em cada
    diretório, dois arquivos de mesmo nome-base em pastas diferentes produzem
    `import file mismatch` — erro de coleta, não de teste.
    """
    nome = arquivo.stem.removeprefix("test_")
    if nome.startswith(PREFIXOS_SEM_MODULO):
        return

    assert modulo_prometido_existe(nome), (
        f"{arquivo.name} promete um módulo que não existe em src/orquestrador/.\n"
        "Ou o módulo foi renomeado e o teste não acompanhou — foi o que aconteceu "
        "com test_parser.py depois que gates/parser.py virou gates/saidas.py —, ou "
        "o arquivo protege uma invariante que atravessa módulos e o nome precisa do "
        "prefixo `test_invariante_`.\n"
        "O nome é lido como caminho: `test_gates_lacunas.py` promete "
        "`gates/lacunas.py`. Um sufixo depois do módulo é permitido e descreve a "
        "fatia: `test_pipeline_loop_reparo.py` promete `pipeline.py`."
    )


# ---------------------------------------------------------------------------
# 9 — `conftest` não é módulo de biblioteca
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arquivo", sorted(DIR_TESTES.rglob("test_*.py")), ids=lambda p: p.name)
def test_nenhum_teste_importa_de_conftest(arquivo: Path):
    modulo = arvore(arquivo)

    linhas = [
        no.lineno
        for no in ast.walk(modulo)
        if (isinstance(no, ast.ImportFrom) and (no.module or "").split(".")[0] == "conftest")
        or (
            isinstance(no, ast.Import)
            and any(alias.name.split(".")[0] == "conftest" for alias in no.names)
        )
    ]
    assert not linhas, (
        f"{arquivo.name} importa de conftest (linha(s) {linhas}).\n"
        "`conftest.py` é um arquivo que o pytest injeta, não um módulo de "
        "biblioteca. O import só resolve porque o rootdir entra no sys.path: ele "
        "quebra quando os testes ganham um subdiretório, e faz ruff, mypy e IDE "
        "resolverem um módulo que não existe como pacote.\n"
        "O que fazer: transforme o helper numa fixture no conftest.py e receba-a "
        "como parâmetro do teste. Helper que é fábrica vira fixture que devolve a "
        "função — veja `saida_de_processo` em tests/conftest.py."
    )
