"""Critério de aceite: o pipeline roda ponta a ponta sem chamar nenhum modelo.

Integração de verdade: os gates leem e escrevem arquivos em disco. O que ele
prova é o ciclo reprova-repara-aprova completo, com os roteiros de
`fixtures/roteiros/` no lugar das respostas do modelo.

Não precisa mais de Node nem do checkout da skill. Enquanto precisava, estes
casos PULAVAM em qualquer máquina sem os dois — e depois do desacoplamento
passaram a pular em todas, porque o caminho da skill saiu da configuração.
Teste que pula não reprova nada.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.aplicacao.simulacao import Roteiros
from orquestrador.cli import principal as modulo_cli
from orquestrador.cli.codigos_de_saida import ERRO_DE_USO
from orquestrador.dominio.manifesto import Manifesto
from orquestrador.gates.cobertura import conferir_cobertura
from orquestrador.raiz import DIR_FIXTURES

# Módulo misto: três casos exercitam o pipeline inteiro em disco, e o quarto só lê
# `fixtures/roteiros/`. Por isso o marker vem por função e não em
# `pytestmark` — o pytest **soma** os markers de módulo e de função, e um
# `pytestmark = e2e` faria `-m e2e` selecionar também o caso que não precisa de nada.
e2e = pytest.mark.e2e


@pytest.fixture
def config_toml(tmp_path: Path) -> Path:
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(
        f"""
[caminhos]
backend = {str(tmp_path / "backend-ignorado")!r}
projeto_testes = {str(tmp_path / "projeto-ignorado")!r}
dir_recursos = "cypress/e2e/apis"
graph = ".agents/state/qa-api/graphify-out/graph.json"
saida = {str(tmp_path / "execucoes")!r}

[estagios.mapeador]
modelo = "<placeholder: dry-run não chama modelo>"
[estagios.executor]
modelo = "<placeholder: dry-run não chama modelo>"

[gates.a]
max_tentativas = 3
[gates.b]
max_tentativas = 3
""",
        encoding="utf-8",
    )
    return arquivo


def ultima_execucao(base: Path) -> Path:
    # Só diretório: a raiz de saída também guarda o diário de propriedade, que é
    # um arquivo e é reescrito por último — sem o filtro, ele seria "a execução".
    diretorios = [caminho for caminho in base.iterdir() if caminho.is_dir()]
    return max(diretorios, key=lambda caminho: caminho.stat().st_mtime)


@e2e
def test_dry_run_completo_com_reparo_no_gate_a(config_toml: Path, tmp_path: Path):
    """O ciclo reprova-repara-aprova nos DOIS gates, com o delta que os alimentou.

    O Gate B ficou sem reprovar por um tempo: os códigos que o faziam vinham do
    `validar-suite-gerada.mjs`, que saiu com o desacoplamento da skill. Com a
    reconciliação de cobertura de volta (`gates/cobertura.py`), ele reprova de novo
    — e por conta própria. Ver `test_gate_b_cobra_o_que_o_gabarito_prometeu`.
    """
    codigo = modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)])
    assert codigo == 0

    execucao = ultima_execucao(tmp_path / "execucoes")
    eventos = []
    for linha in (execucao / "execucao.jsonl").read_text(encoding="utf-8").splitlines():
        evento = json.loads(linha)
        eventos.append({**evento, **evento.get("dados", {})})
    gates = [evento for evento in eventos if evento["tipo"] == "gate"]

    do_a = [evento for evento in gates if evento["gate"] == "gate_a"]
    assert [evento["aprovado"] for evento in do_a] == [False, True]
    assert do_a[0]["violacoes"], "o gate_a precisa reprovar com violações reais"

    # O delta que foi ao reparo carrega o código que o produziu — é ele, e só ele,
    # que o princípio 2 permite mandar de volta ao modelo. QAORQ-062 (checklist
    # negativa incompleta) é dono da fatia `dossie`: o reparo da tentativa 2
    # reemite SÓ essa fatia, sem re-explorar o backend.
    delta_a = next(
        evento for evento in eventos if evento["tipo"] == "delta" and evento["estagio"] == "gate_a"
    )
    assert "QAORQ-062" in delta_a["codigos"], "a 1ª tentativa erra a checklist do dossiê"

    # A prova do fatiamento: a tentativa de reparo não tem chamada de exploração —
    # só a fatia dona da violação.
    chamadas_t2 = [
        evento
        for evento in eventos
        if evento["tipo"] == "requisicao_llm_concluida"
        and evento.get("estagio") == "mapeador"
        and evento.get("tentativa") == 2
    ]
    assert chamadas_t2, "o reparo precisa ter acontecido na tentativa 2"
    assert all(evento.get("fatia") == "fatia:dossie" for evento in chamadas_t2), (
        f"reparo deveria reemitir só a fatia dossie: {[e.get('fatia') for e in chamadas_t2]}"
    )

    # O Gate B reprova a primeira tentativa por cobertura e aprova a segunda —
    # ver test_gate_b_cobra_o_que_o_gabarito_prometeu.
    assert [evento["aprovado"] for evento in gates if evento["gate"] == "gate_b"] == [
        False,
        True,
    ]

    # Nenhum modelo foi chamado.
    chamadas = [evento for evento in eventos if evento["tipo"] == "requisicao_llm_concluida"]
    assert chamadas and all(evento["simulado"] for evento in chamadas)

    # Telemetria por estágio no log.
    telemetria = next(evento for evento in eventos if evento["tipo"] == "telemetria")
    assert set(telemetria["por_estagio"]) == {"mapeador", "planejador", "executor"}
    assert telemetria["total"]["entrada"] > 0


@e2e
def test_o_reparo_nao_cresce_o_contexto(config_toml: Path, tmp_path: Path):
    # A prova prática do princípio 2: a tentativa de reparo do mapeador entra com
    # MENOS tokens que a primeira, porque recebe só o artefato e as violações —
    # não a exploração inteira que a antecedeu.
    assert modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)]) == 0
    execucao = ultima_execucao(tmp_path / "execucoes")
    chamadas = []
    for linha in (execucao / "execucao.jsonl").read_text(encoding="utf-8").splitlines():
        evento = json.loads(linha)
        if evento["tipo"] == "requisicao_llm_concluida":
            chamadas.append({**evento, **evento.get("dados", {})})
    mapeador = [evento for evento in chamadas if evento["estagio"] == "mapeador"]
    por_tentativa: dict[int, int] = {}
    for evento in mapeador:
        por_tentativa[evento["tentativa"]] = (
            por_tentativa.get(evento["tentativa"], 0) + evento["uso"]["entrada"]
        )
    assert por_tentativa[2] < por_tentativa[1]


@e2e
def test_artefatos_ficam_em_disco(config_toml: Path, tmp_path: Path):
    modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)])
    execucao = ultima_execucao(tmp_path / "execucoes")
    recurso = execucao / "sandbox" / "projeto-testes" / "cypress" / "e2e" / "apis" / "pedidos"

    manifesto = json.loads((recurso / "_support" / "cobertura.json").read_text("utf-8"))
    assert manifesto["recurso"] == "pedidos"
    # O manifesto reparado contabiliza as 12 categorias em todos os endpoints.
    for entrada in manifesto["endpoints"]:
        assert len(set(entrada["cats"]) | set(entrada["naoAplica"])) == 12

    # Um spec por operação, com o nome derivado do endpoint por código.
    for spec in ("listar-pedidos.cy.js", "criar-pedidos.cy.js"):
        assert (recurso / spec).is_file()
    for artefato in ("inventario.json", "plano.json", "dossie.json", "dossie.md"):
        assert (execucao / "artefatos" / "pedidos" / artefato).is_file()


@e2e
def test_gate_b_cobra_o_que_o_gabarito_prometeu(config_toml: Path, tmp_path: Path):
    """A maior perda do desacoplamento, agora como exigência.

    Onde este teste afirmava a AUSÊNCIA da reconciliação de cobertura — e existia
    para quebrar no dia em que ela voltasse —, ele agora prova o ciclo inteiro: a
    primeira tentativa do executor deixa categorias prometidas sem `it`, o Gate B
    reprova com `QAORQ-030`, e a segunda entrega e aprova.

    É a promessa do produto rodando de ponta a ponta sem custar um token: "tudo
    que foi prometido virou teste?" respondido por script, não por afirmação.
    """
    assert modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)]) == 0
    execucao = ultima_execucao(tmp_path / "execucoes")

    gates = [evento for evento in eventos_de(execucao) if evento["tipo"] == "gate"]
    do_b = [evento for evento in gates if evento["gate"] == "gate_b"]
    assert [evento["aprovado"] for evento in do_b] == [False, True], (
        "a primeira tentativa precisa reprovar por cobertura e a segunda aprovar"
    )

    codigos = {codigo for evento in do_b for codigo in evento.get("violacoes_codigos", [])}
    if not codigos:
        codigos = {
            violacao.get("code", violacao.get("codigo"))
            for evento in do_b
            for violacao in evento.get("violacoes", [])
        }
    assert "QAORQ-030" in codigos, f"a reprovação precisa ser de cobertura: {codigos}"

    # E o que foi publicado cobre o gabarito inteiro: nenhuma categoria prometida
    # ficou sem `it` na versão que chegou ao projeto do consumidor.
    recurso = execucao / "sandbox" / "projeto-testes" / "cypress" / "e2e" / "apis" / "pedidos"
    manifesto = Manifesto.model_validate_json(
        (recurso / "_support" / "cobertura.json").read_text("utf-8")
    )
    specs = {spec.name: spec.read_text("utf-8") for spec in recurso.glob("*.cy.js")}
    assert conferir_cobertura(manifesto, specs, {}).aprovado


def eventos_de(execucao: Path) -> list[dict]:
    linhas = (execucao / "execucao.jsonl").read_text(encoding="utf-8").splitlines()
    brutos = [json.loads(linha) for linha in linhas]
    return [{**evento, **evento.get("dados", {})} for evento in brutos]


@e2e
def test_reaproveitar_comeca_no_executor(config_toml: Path, tmp_path: Path):
    """A prova prática do princípio 1: os artefatos de ontem bastam para hoje.

    Se o handoff entre estágios é mesmo arquivo em disco, então o Bloco 2 roda a
    partir do que ficou gravado, sem re-explorar o backend nem replanejar. É o que
    torna viável iterar no executor sem pagar o pipeline inteiro a cada volta.
    """
    assert modulo_cli.main(["--dry-run", "--recurso", "pedidos", "--config", str(config_toml)]) == 0
    primeira = ultima_execucao(tmp_path / "execucoes")

    codigo = modulo_cli.main(
        [
            "--dry-run",
            "--recurso",
            "pedidos",
            "--config",
            str(config_toml),
            "--reaproveitar",
            primeira.name,
        ]
    )
    assert codigo == 0

    segunda = ultima_execucao(tmp_path / "execucoes")
    assert segunda != primeira

    chamadas = [
        evento for evento in eventos_de(segunda) if evento["tipo"] == "requisicao_llm_concluida"
    ]
    assert chamadas, "o executor precisa ter rodado"
    assert {evento["estagio"] for evento in chamadas} == {"executor"}, (
        "mapeador e planejador não podem ser chamados numa execução reaproveitada"
    )

    # Os dois gates continuam mandando: reaproveitar decisão não é reaproveitar
    # veredito. O Gate A não roda porque não há artefato de mapeador para julgar.
    gates = {evento["gate"] for evento in eventos_de(segunda) if evento["tipo"] == "gate"}
    assert gates == {"gate_b"}

    # A execução nova é auto-contida: quem a comparar amanhã não depende de a
    # execução de origem ainda existir.
    for artefato in ("manifesto.json", "plano.json", "dossie.json", "inventario.json"):
        assert (segunda / "artefatos" / "pedidos" / artefato).is_file(), artefato


@e2e
def test_reaproveitar_execucao_inexistente_e_erro_de_uso(config_toml: Path, tmp_path: Path):
    # Falha fechada e antes de qualquer chamada: run_id errado que "só rodasse o
    # pipeline inteiro" gastaria os dois estágios caros sem ninguém pedir.
    codigo = modulo_cli.main(
        [
            "--dry-run",
            "--recurso",
            "pedidos",
            "--config",
            str(config_toml),
            "--reaproveitar",
            "20260101-000000-0-inexistente",
        ]
    )
    assert codigo == ERRO_DE_USO


@pytest.mark.unit
def test_roteiros_repetem_o_ultimo_quando_a_tentativa_excede():
    roteiros = Roteiros(DIR_FIXTURES / "roteiros")
    assert list(roteiros.recursos()) == ["pedidos"]
    ultimo = roteiros.carregar("pedidos", "mapeador", 2)
    assert roteiros.carregar("pedidos", "mapeador", 9) == ultimo
