"""As três regras do manifesto de execução, fixadas uma a uma.

O manifesto existe para responder a *"ontem passou, hoje falhou"*. Ele só presta
esse serviço enquanto for verdade que:

1. **segredo nunca entra** — nem mascarado de forma reversível;
2. **código-fonte nunca entra** — de arquivo sai hash, nunca conteúdo;
3. **sonda que falha não derruba a execução** — o campo fica ausente com o motivo.

As duas primeiras são de segurança e valem como limite: o arquivo é justamente o
que alguém anexa a um chamado de suporte, para fora da empresa. A terceira é de
utilidade, e é a que mais tenta ser violada — a correção "óbvia" para um `git` que
falha é deixar a exceção subir.

Nada aqui abre subprocesso: `executar` é substituído por dublê. O manifesto do
`--dry-run` de verdade é exercitado em `tests/test_dry_run.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orquestrador.config import Config
from orquestrador.excecoes import ExecutavelAusente
from orquestrador.ferramentas.processo import SaidaProcesso
from orquestrador.observabilidade import manifesto_de_execucao
from orquestrador.observabilidade.manifesto_de_execucao import REDIGIDO, coletar, escrever, redigir

CHAVE_FALSA = "sk-" + "or-v1-" + "0123456789abcdef0123456789abcdef"


@pytest.fixture
def git_falso(monkeypatch: pytest.MonkeyPatch):
    """Substitui `executar` por um dublê que responde `git` e `node` sem subprocesso.

    Fixture e não dublê montado em cada teste porque **todo** teste deste arquivo
    precisa dela: sem ela a suíte unitária passaria a depender de `git` e de `node`
    instalados, que é exatamente o que o AGENTS.md proíbe.
    """

    def falso(argv: list[str], **_kwargs: Any) -> SaidaProcesso:
        resposta = {
            "rev-parse HEAD": "a" * 40,
            "rev-parse --abbrev-ref HEAD": "melhorias/etapa-1-seguranca",
            "rev-parse --show-toplevel": "C:/LangChainTestes-01/Langchain",
            "status --porcelain": " M src/orquestrador/cli.py",
            "--version": "v24.4.0",
        }.get(" ".join(argv[1:]), "")
        return SaidaProcesso(argv=argv, codigo=0, stdout=resposta, stderr="", duracao_s=0.01)

    monkeypatch.setattr(manifesto_de_execucao, "executar", falso)


@pytest.fixture
def config_com_prompts(config_falso: Config, tmp_path: Path) -> Config:
    """Config apontando os prompts para um diretório com conteúdo reconhecível."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "mapeador.md").write_text(
        "SEGREDO-DE-CONTEUDO-QUE-NAO-PODE-VAZAR\n", encoding="utf-8"
    )
    config_falso.caminhos.prompts = prompts
    return config_falso


def _coletar(config: Config, **extras: Any) -> dict[str, Any]:
    padrao: dict[str, Any] = {
        "run_id": "20260807-101112-4242",
        "dry_run": True,
        "recursos": ["pedidos"],
    }
    return coletar(config, **{**padrao, **extras})


# ---------------------------------------------------------------------------
# Regra 1 — segredo nunca entra
# ---------------------------------------------------------------------------


def test_o_valor_da_chave_nunca_aparece_no_manifesto(
    config_com_prompts: Config, git_falso, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(config_com_prompts.openrouter.api_key_env, CHAVE_FALSA)

    texto = json.dumps(_coletar(config_com_prompts), ensure_ascii=False, default=str)

    assert CHAVE_FALSA not in texto
    # E o **nome** da variável continua lá: sem ele o manifesto não responde "de
    # onde a chave deveria ter vindo?", que é metade dos chamados de autenticação.
    assert config_com_prompts.openrouter.api_key_env in texto


def test_a_varredura_apaga_o_segredo_que_a_redacao_por_nome_deixou_passar(
    config_com_prompts: Config, git_falso, monkeypatch: pytest.MonkeyPatch
):
    """A rede de segurança pega o campo que ninguém previu — e denuncia o defeito.

    Simula o caminho que a redação por nome não cobre: a chave chegando por um
    campo cujo nome nada tem de suspeito (aqui, um caminho de configuração).
    """
    monkeypatch.setenv(config_com_prompts.openrouter.api_key_env, CHAVE_FALSA)
    config_com_prompts.caminhos.dir_recursos = f"cypress/e2e/{CHAVE_FALSA}"

    manifesto = _coletar(config_com_prompts)

    assert CHAVE_FALSA not in json.dumps(manifesto, default=str)
    assert REDIGIDO in json.dumps(manifesto, default=str)
    assert "seguranca.varredura" in manifesto["campos_ausentes"]


@pytest.mark.parametrize(
    ("campo", "esperado"),
    [
        ("senha", REDIGIDO),
        ("api_key", REDIGIDO),
        ("Authorization", REDIGIDO),
        ("chave_do_provedor", REDIGIDO),
        # Nome de variável de ambiente é configuração, não valor.
        ("api_key_env", "OPENROUTER_API_KEY"),
        ("modelo", "OPENROUTER_API_KEY"),
        # `tokens` no plural é contagem de uso do modelo, não credencial. Redigir
        # aqui ensinaria o leitor a ignorar `[redigido]` — e é assim que se perde a
        # redação que importa.
        ("max_tokens", "OPENROUTER_API_KEY"),
        ("tokens_de_entrada", "OPENROUTER_API_KEY"),
    ],
)
def test_redigir_apaga_por_nome_de_campo_e_preserva_nome_de_variavel(campo: str, esperado: str):
    assert redigir({campo: "OPENROUTER_API_KEY"})[campo] == esperado


def test_redigir_atravessa_lista_e_dicionario_aninhados():
    bruto = {"blocos": [{"token": "abc"}, {"modelo": "familia/modelo"}]}
    assert redigir(bruto) == {"blocos": [{"token": REDIGIDO}, {"modelo": "familia/modelo"}]}


# ---------------------------------------------------------------------------
# Regra 2 — código-fonte nunca entra
# ---------------------------------------------------------------------------


def test_do_prompt_sai_hash_e_nao_conteudo(config_com_prompts: Config, git_falso):
    manifesto = _coletar(config_com_prompts)

    texto = json.dumps(manifesto, ensure_ascii=False, default=str)
    assert "SEGREDO-DE-CONTEUDO-QUE-NAO-PODE-VAZAR" not in texto
    hashes = manifesto["hashes"]["prompts"]
    assert set(hashes) == {"mapeador.md"}
    assert len(hashes["mapeador.md"]) == 64


def test_o_hash_do_prompt_muda_quando_o_prompt_muda(config_com_prompts: Config, git_falso):
    antes = _coletar(config_com_prompts)["hashes"]["prompts"]["mapeador.md"]
    (config_com_prompts.caminhos.prompts / "mapeador.md").write_text("outro\n", encoding="utf-8")
    depois = _coletar(config_com_prompts)["hashes"]["prompts"]["mapeador.md"]

    assert antes != depois, (
        "o hash do prompt não mudou depois de o prompt mudar — o manifesto deixou de "
        "responder 'foi o prompt que mudou entre ontem e hoje?'"
    )


def test_os_artefatos_entram_como_hash_quando_o_diretorio_e_passado(
    config_com_prompts: Config, git_falso, tmp_path: Path
):
    artefatos = tmp_path / "artefatos" / "pedidos"
    artefatos.mkdir(parents=True)
    (artefatos / "inventario.json").write_text('{"endpoints": []}', encoding="utf-8")

    manifesto = _coletar(config_com_prompts, dir_artefatos=tmp_path / "artefatos")

    assert set(manifesto["hashes"]["artefatos"]) == {"pedidos/inventario.json"}
    assert '{"endpoints": []}' not in json.dumps(manifesto, default=str)


# ---------------------------------------------------------------------------
# Regra 3 — sonda que falha não derruba a execução
# ---------------------------------------------------------------------------


def test_backend_que_nao_e_repositorio_git_deixa_o_campo_ausente_com_motivo(
    config_com_prompts: Config, monkeypatch: pytest.MonkeyPatch
):
    def sem_git(argv: list[str], **_kwargs: Any) -> SaidaProcesso:
        raise ExecutavelAusente('executável não encontrado no PATH: "git"')

    monkeypatch.setattr(manifesto_de_execucao, "executar", sem_git)

    manifesto = _coletar(config_com_prompts)

    assert "repositorios.backend" in manifesto["campos_ausentes"]
    assert "git" in manifesto["campos_ausentes"]["repositorios.backend"]
    # O caminho sobrevive mesmo sem `git`: saber qual diretório foi usado já é
    # metade da reprodução, e essa parte não depende de ferramenta nenhuma.
    assert manifesto["repositorios"]["backend"]["caminho"] == str(
        config_com_prompts.caminhos.backend
    )
    assert "commit" not in manifesto["repositorios"]["backend"]


def test_node_indisponivel_nao_derruba_a_coleta(
    config_com_prompts: Config, monkeypatch: pytest.MonkeyPatch
):
    def so_git(argv: list[str], **_kwargs: Any) -> SaidaProcesso:
        if argv[0] != "git":
            raise ExecutavelAusente(f'executável não encontrado no PATH: "{argv[0]}"')
        return SaidaProcesso(argv=argv, codigo=0, stdout="b" * 40, stderr="", duracao_s=0.01)

    monkeypatch.setattr(manifesto_de_execucao, "executar", so_git)

    manifesto = _coletar(config_com_prompts)

    assert manifesto["ambiente"]["node"] is None
    assert "ambiente.node" in manifesto["campos_ausentes"]
    assert manifesto["ambiente"]["python"]


def test_diretorio_de_prompts_inexistente_vira_ausencia(config_falso: Config, git_falso):
    config_falso.caminhos.prompts = config_falso.caminhos.backend / "nao-existe"

    manifesto = _coletar(config_falso)

    assert manifesto["hashes"]["prompts"] is None
    assert "hashes.prompts" in manifesto["campos_ausentes"]


# ---------------------------------------------------------------------------
# O conteúdo mínimo e a escrita
# ---------------------------------------------------------------------------


def test_o_manifesto_carrega_o_minimo_para_reproduzir(config_com_prompts: Config, git_falso):
    manifesto = _coletar(config_com_prompts)

    assert manifesto["schema_version"] == manifesto_de_execucao.ESQUEMA_DO_MANIFESTO
    assert manifesto["run_id"] == "20260807-101112-4242"
    assert manifesto["dry_run"] is True
    assert manifesto["recursos"] == ["pedidos"]
    assert manifesto["ambiente"]["node"] == "v24.4.0"
    assert manifesto["ambiente"]["python"]
    assert manifesto["repositorios"]["orquestrador"]["commit"] == "a" * 40
    # Árvore suja é a informação que mais falta num chamado: commit limpo é
    # reproduzível por qualquer um, commit sujo não é reproduzível por ninguém.
    assert manifesto["repositorios"]["orquestrador"]["estado_sujo"] is True
    # A raiz do repositório separa "o backend é um checkout" de "o backend está
    # dentro do checkout de outra coisa" — o sandbox do --dry-run é o segundo caso.
    assert manifesto["repositorios"]["backend"]["raiz_do_repositorio"]
    assert manifesto["skill"]["impressao"]
    assert manifesto["modelos_por_estagio"]["mapeador"]["modelo"] == "fake/mapeador"
    assert manifesto["configuracao"]["caminhos"]["backend"]


def test_escrever_deixa_json_legivel_no_destino(
    config_com_prompts: Config, git_falso, tmp_path: Path
):
    destino = tmp_path / "execucao" / manifesto_de_execucao.NOME_DO_MANIFESTO

    devolvido = escrever(
        config_com_prompts,
        destino,
        run_id="20260807-101112-4242",
        dry_run=True,
        recursos=["pedidos"],
    )

    assert json.loads(destino.read_text(encoding="utf-8")) == devolvido


def test_destino_impossivel_nao_levanta(config_com_prompts: Config, git_falso, tmp_path: Path):
    """Escrita que falha vira ausência, não exceção.

    Um arquivo no lugar onde o diretório precisaria existir é a forma portátil de
    forçar o erro — `chmod` não vale no Windows, que é a plataforma de referência
    deste projeto.
    """
    obstaculo = tmp_path / "execucao"
    obstaculo.write_text("não sou um diretório", encoding="utf-8")

    manifesto = escrever(
        config_com_prompts,
        obstaculo / manifesto_de_execucao.NOME_DO_MANIFESTO,
        run_id="20260807-101112-4242",
        dry_run=True,
        recursos=["pedidos"],
    )

    assert any(campo.startswith("escrita[") for campo in manifesto["campos_ausentes"])
