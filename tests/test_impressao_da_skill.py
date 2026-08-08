"""A skill externa não pode mudar em silêncio debaixo do consumidor.

`qa-api` mora em outro repositório e o acordo com ela é implícito: formato dos
argumentos, código de saída, forma do JSON e semântica dos códigos `QAAPI-`. Nada
disso está declarado em lugar nenhum.

E há uma segunda dependência, menos visível: os `prompts/*.md` são condensação
**manual** das `references/*.md` da skill. Duas fontes de verdade, uma copiando a
outra, sem nada detectando divergência.

A impressão é a âncora. Mesma proteção que o `qa-reindex.mjs` já faz com a versão
do Graphify — e que, quando falhou aqui, evitou uma execução contra um extrator
incompatível.
"""

from __future__ import annotations

import pytest

from orquestrador.config import Config
from orquestrador.excecoes import ErroDeConfiguracao

pytestmark = pytest.mark.unit


@pytest.fixture
def skill(config_falso: Config) -> Config:
    """Semeia os `.mjs` que a impressão resume, incluindo um módulo aninhado."""
    raiz = config_falso.caminhos.script("qa-cobertura.mjs").parent
    (raiz / "cobertura").mkdir(parents=True, exist_ok=True)
    for nome in ("validar-suite-gerada.mjs", "qa-cobertura.mjs", "qa-reindex.mjs"):
        (raiz / nome).write_text(f"// {nome}\n", encoding="utf-8")
    (raiz / "cobertura" / "matriz.mjs").write_text("// matriz\n", encoding="utf-8")
    return config_falso


def com_impressao(config: Config, valor: str) -> Config:
    return config.model_copy(
        update={"skill": config.skill.model_copy(update={"impressao_esperada": valor})}
    )


def test_impressao_muda_quando_um_script_muda(skill: Config):
    antes = skill.impressao_da_skill()

    alvo = skill.caminhos.script("cobertura/matriz.mjs")
    alvo.write_text(alvo.read_text(encoding="utf-8") + "// linha nova\n", encoding="utf-8")

    assert skill.impressao_da_skill() != antes


def test_impressao_muda_quando_um_script_e_renomeado(skill: Config):
    # O caminho relativo entra no hash: mover um módulo de `cobertura/` sem alterar
    # uma linha dele também muda o contrato — os imports do script mudam junto.
    antes = skill.impressao_da_skill()

    alvo = skill.caminhos.script("cobertura/matriz.mjs")
    alvo.rename(alvo.with_name("renomeado.mjs"))

    assert skill.impressao_da_skill() != antes


def test_impressao_e_estavel_entre_chamadas(skill: Config):
    assert skill.impressao_da_skill() == skill.impressao_da_skill()


def test_divergencia_recusa_com_o_hash_novo_na_mensagem(skill: Config):
    # A mensagem precisa ser acionável: quem conferiu o contrato tem de conseguir
    # colar o valor novo sem calcular nada à mão.
    atual = skill.impressao_da_skill()
    config = com_impressao(skill, "0000000000000000")

    with pytest.raises(ErroDeConfiguracao) as erro:
        config.validar_impressao_da_skill()

    mensagem = str(erro.value)
    assert atual in mensagem
    assert "impressao_esperada" in mensagem


def test_impressao_conferida_nao_reclama(skill: Config):
    config = com_impressao(skill, skill.impressao_da_skill())
    config.validar_impressao_da_skill()


def test_campo_vazio_desliga_a_verificacao(skill: Config):
    # Padrão para quem desenvolve a skill e o consumidor ao mesmo tempo: ali o hash
    # mudaria a cada salvamento e a trava viraria ruído que se aprende a ignorar.
    assert skill.skill.impressao_esperada == ""
    skill.validar_impressao_da_skill()


def test_validar_caminhos_carrega_a_verificacao(skill: Config):
    # A verificação não pode depender de alguém lembrar de chamá-la: ela entra pelo
    # mesmo portão que já confere skill, scripts, backend e projeto de testes.
    config = com_impressao(skill, "0000000000000000")

    with pytest.raises(ErroDeConfiguracao, match="mudou desde a última verificação"):
        config.validar_caminhos(exigir_backend=False)
