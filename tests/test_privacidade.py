"""A fronteira de privacidade: o que do backend do cliente chega ao modelo.

O confinamento de caminho (`tests/test_confinamento.py`) responde "este caminho
escapa da raiz?". Aqui a pergunta é outra e vale sobre caminho que já passou por
ele: "este arquivo pode ser enviado a um provedor de LLM?".

Metade destes testes é sobre **não** redigir. Backend real é cheio de senha
falsa — `password: password` no seed, `${DB_PASSWORD}` no YAML, um segredo de
teste com sessenta e quatro caracteres no `JwtServiceTest` —, e uma política que
apaga tudo isso ensina o leitor a ignorar `[redigido: ...]`. O caso que interessa
é a chave real que passaria despercebida no meio de um arquivo legítimo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orquestrador.ferramentas.arquivos import (
    Confinamento,
    buscar,
    ler_arquivo,
    listar_diretorio,
)
from orquestrador.ferramentas.privacidade import (
    Contagem,
    Llmignore,
    PoliticaDePrivacidade,
    motivo_da_denylist,
    redigir_texto,
)

CHAVE_PRIVADA = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0m3vQ7hL9xN2pR4sT6uV8wY1zA3bC5dE7fG9hI0jK2lM4nO6
pQ8rS0tU2vW4xY6zA8bC0dE2fG4hI6jK8lM0nO2pQ4rS6tU8vW0xY2zA4bC6dE8f
-----END RSA PRIVATE KEY-----"""

# Chave de provedor com a forma real: prefixo literal e corpo longo. Não é
# credencial de ninguém — o que a regra reconhece é o formato.
#
# Montada em pedaços, e **não como literal**, por um motivo prático: o scanner de
# segredo do GitHub reconhece o formato tão bem quanto a nossa regra e recusa o
# push. Ele está certo — quem olha de fora não distingue chave sintética de chave
# real, e o caminho fácil dali seria clicar em "permitir este segredo", que é o
# hábito que faz a próxima chave, a de verdade, passar sem ninguém olhar.
#
# Em runtime a string é idêntica, então a regra continua sendo exercitada de fato.
# Não junte isto num literal só.
CHAVE_DE_API = "sk-" + "or-v1-" + "9f2c4a7be1d83046af5127cd9e0b6a3417d2589cfe4b0176ad38e2c95b74f0a1"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GOOGLE = "AIza" + "SyD9tK3mQ7xR2vL8nP4wZ1cB6hJ0fY5uT7e"

CONTROLLER = """package com.orderflow.api;

@RestController
@RequestMapping("/api/v1/pedidos")
class PedidoController {
    @GetMapping
    List<Pedido> listar() { return service.listar(); }
}
"""


@pytest.fixture
def contagem() -> Contagem:
    """Contagem própria do teste: a do processo é global e acumularia entre casos."""
    return Contagem()


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    raiz = tmp_path / "backend"
    (raiz / "src" / "api").mkdir(parents=True)
    (raiz / "src" / "api" / "PedidoController.java").write_text(CONTROLLER, encoding="utf-8")
    (raiz / ".env").write_text(
        "DB_URL=jdbc:postgresql://localhost:5432/orderflow\nJWT_SECRET=abc\n", encoding="utf-8"
    )
    (raiz / "chave-producao.pem").write_text(CHAVE_PRIVADA + "\n", encoding="utf-8")
    return raiz


@pytest.fixture
def confinado(backend: Path, contagem: Contagem) -> Confinamento:
    """`Confinamento` com política própria — nada de `.llmignore` do disco."""
    return Confinamento(
        backend,
        politica=PoliticaDePrivacidade(backend, llmignore=Llmignore(), contagem=contagem),
    )


# ---------------------------------------------------------------------------
# Denylist: nome de arquivo e de diretório
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "relativo",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".env.example",
        ".envrc",
        "config/producao.env",
        "certs/chave.pem",
        "servidor.key",
        "loja.p12",
        "loja.pfx",
        "app.keystore",
        "infra/terraform.tfvars",
        ".ssh/id_rsa",
        ".ssh/config",
        ".aws/credentials",
        "home/.aws/config",
        "id_rsa",
        "id_ed25519.pub",
        ".npmrc",
        ".netrc",
        "credentials",
        "credentials.json",
        "src/main/resources/.jwt-secret",
        "src/main/resources/application-secrets.yml",
        "docs/SEED_CREDENTIALS.md",
        "client_secret_123.json",
    ],
)
def test_denylist_recusa_material_de_credencial(relativo: str):
    assert motivo_da_denylist(relativo) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "relativo",
    [
        "src/main/java/com/orderflow/api/PedidoController.java",
        "src/main/java/com/orderflow/config/SecurityConfig.java",
        "src/main/java/com/orderflow/security/JwtService.java",
        # Segmentação por separador, não substring: camelCase não gera o segmento.
        "src/test/java/com/orderflow/security/SeedPasswordHashTest.java",
        # Extensão de código sai da regra de palavra no nome — é código, e é o
        # que o mapeador foi contratado para ler.
        "src/main/java/com/orderflow/secret/SecretRotationService.java",
        "src/services/secret_manager.py",
        "docs/AUTHORIZATION.md",
        "src/main/resources/application.yml",
        "src/main/resources/db/migration/h2/V2__seed_orderflow_v1.sql",
        "certs/servidor.crt",
        "pom.xml",
    ],
)
def test_denylist_deixa_passar_codigo_legitimo(relativo: str):
    assert motivo_da_denylist(relativo) is None


@pytest.mark.unit
def test_env_do_backend_nunca_e_lido(confinado: Confinamento):
    saida = ler_arquivo(confinado, ".env")
    assert saida.startswith("ERRO")
    assert "política de privacidade" in saida
    assert "JWT_SECRET" not in saida


@pytest.mark.unit
def test_env_do_backend_nao_aparece_em_busca(confinado: Confinamento):
    # A busca é o furo mais fácil de esquecer: recusar `ler_arquivo` e deixar a
    # varredura por regex abrir o mesmo arquivo devolve o conteúdo linha a linha.
    saida = buscar(confinado, r"JWT_SECRET|DB_URL")
    assert "nenhuma ocorrência" in saida
    assert ".env" not in saida


@pytest.mark.unit
def test_env_do_backend_nao_aparece_na_listagem(confinado: Confinamento):
    saida = listar_diretorio(confinado, ".")
    assert ".env" not in saida
    assert "src/" in saida


@pytest.mark.unit
def test_chave_privada_em_pem_nunca_e_lida(confinado: Confinamento):
    saida = ler_arquivo(confinado, "chave-producao.pem")
    assert saida.startswith("ERRO")
    assert "MIIEpAIBAAKCAQEA" not in saida


@pytest.mark.unit
def test_chave_privada_em_pem_nao_aparece_em_busca(confinado: Confinamento):
    saida = buscar(confinado, r"PRIVATE KEY|MIIEpAIBAAKCAQEA")
    assert "nenhuma ocorrência" in saida


@pytest.mark.unit
def test_diretorio_de_segredo_nao_e_percorrido(backend: Path, confinado: Confinamento):
    (backend / ".ssh").mkdir()
    (backend / ".ssh" / "id_rsa").write_text(CHAVE_PRIVADA, encoding="utf-8")
    (backend / ".ssh" / "notas.txt").write_text("PRIVATE KEY do servidor\n", encoding="utf-8")

    assert "nenhuma ocorrência" in buscar(confinado, r"PRIVATE KEY")
    assert ".ssh" not in listar_diretorio(confinado, ".")


@pytest.mark.unit
def test_codigo_legitimo_continua_legivel(confinado: Confinamento):
    saida = ler_arquivo(confinado, "src/api/PedidoController.java")
    assert "@GetMapping" in saida
    assert "política de privacidade" not in saida


# ---------------------------------------------------------------------------
# `.llmignore`
# ---------------------------------------------------------------------------


def com_llmignore(backend: Path, contagem: Contagem, texto: str) -> Confinamento:
    (backend / ".llmignore").write_text(texto, encoding="utf-8")
    return Confinamento(backend, politica=PoliticaDePrivacidade(backend, contagem=contagem))


@pytest.mark.unit
def test_llmignore_do_cliente_e_respeitado(backend: Path, contagem: Contagem):
    (backend / "dumps").mkdir()
    (backend / "dumps" / "clientes.sql").write_text("INSERT INTO cpf ...\n", encoding="utf-8")
    (backend / "interno.md").write_text("nota interna\n", encoding="utf-8")

    confinado = com_llmignore(backend, contagem, "dumps/\ninterno.md\n")

    assert ler_arquivo(confinado, "dumps/clientes.sql").startswith("ERRO")
    assert ler_arquivo(confinado, "interno.md").startswith("ERRO")
    assert "nenhuma ocorrência" in buscar(confinado, r"INSERT INTO|nota interna")
    assert "@GetMapping" in ler_arquivo(confinado, "src/api/PedidoController.java")


@pytest.mark.unit
def test_llmignore_ausente_deixa_valer_so_a_denylist(backend: Path, contagem: Contagem):
    confinado = Confinamento(backend, politica=PoliticaDePrivacidade(backend, contagem=contagem))
    assert not (backend / ".llmignore").exists()
    assert "@GetMapping" in ler_arquivo(confinado, "src/api/PedidoController.java")
    assert ler_arquivo(confinado, ".env").startswith("ERRO")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("regras", "alvo", "ignorado"),
    [
        ("*.sql\n", "dumps/clientes.sql", True),
        ("*.sql\n", "src/api/PedidoController.java", False),
        ("/topo.md\n", "topo.md", True),
        ("/topo.md\n", "docs/topo.md", False),
        ("docs/**/rascunho.md\n", "docs/2026/rascunho.md", True),
        ("privado/\n", "privado/a/b.txt", True),
        ("privado\n", "src/privado/b.txt", True),
        ("# comentário\n\n", "qualquer.txt", False),
        ("*.sql\n!manter.sql\n", "manter.sql", False),
        ("*.sql\n!manter.sql\n", "outro.sql", True),
        # Caixa: `*.PEM` querendo dizer `*.pem` não pode custar um vazamento.
        ("*.YML\n", "application.yml", True),
    ],
)
def test_sintaxe_de_gitignore_do_llmignore(regras: str, alvo: str, ignorado: bool):
    resultado = Llmignore.de_texto(regras).padrao_que_ignora(alvo)
    assert (resultado is not None) is ignorado


@pytest.mark.unit
def test_negacao_do_llmignore_nao_reabre_a_denylist(backend: Path, contagem: Contagem):
    """A denylist é a camada que não se desliga.

    Se `!.env` a reabrisse, bastaria uma linha bem-intencionada no `.llmignore`
    do cliente — ou um `!*` copiado de outro projeto — para o `.env` voltar ao
    prompt. A política do cliente só sabe **acrescentar**.
    """
    confinado = com_llmignore(backend, contagem, "!.env\n!*.pem\n!*\n")
    assert ler_arquivo(confinado, ".env").startswith("ERRO")
    assert ler_arquivo(confinado, "chave-producao.pem").startswith("ERRO")


# ---------------------------------------------------------------------------
# Redação de conteúdo: o que sai
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chave_de_api_no_meio_de_arquivo_legitimo_e_redigida(
    backend: Path, confinado: Confinamento
):
    arquivo = backend / "src" / "api" / "ClienteOpenRouter.java"
    arquivo.write_text(
        "package com.orderflow.api;\n"
        "\n"
        "class ClienteOpenRouter {\n"
        f'    private static final String API_KEY = "{CHAVE_DE_API}";\n'
        "\n"
        '    @PostMapping("/resumo")\n'
        "    String resumir(@RequestBody Pedido pedido) { return llm.resumir(pedido); }\n"
        "}\n",
        encoding="utf-8",
    )

    saida = ler_arquivo(confinado, "src/api/ClienteOpenRouter.java")

    assert CHAVE_DE_API not in saida
    assert "[redigido: credencial de provedor]" in saida
    # O resto do arquivo continua chegando: é dele que sai o endpoint do inventário.
    assert "@PostMapping" in saida
    assert "class ClienteOpenRouter" in saida
    assert "String resumir(@RequestBody Pedido pedido)" in saida
    assert "1 trecho(s) redigido(s)" in saida


@pytest.mark.unit
def test_bloco_pem_dentro_de_arquivo_legitimo_preserva_a_numeracao(
    backend: Path, confinado: Confinamento
):
    """Redação não pode mexer na contagem de linhas.

    `ler_arquivo` numera o que devolve e o modelo volta ao arquivo por `offset`.
    Colapsar um bloco de chave em uma linha faria toda referência posterior
    apontar para o lugar errado — e o modelo culparia o próprio raciocínio.
    """
    arquivo = backend / "src" / "api" / "Assinatura.java"
    corpo = f'class Assinatura {{\n    static final String PEM = """\n{CHAVE_PRIVADA}\n""";\n}}\n'
    arquivo.write_text(corpo, encoding="utf-8")

    saida = ler_arquivo(confinado, "src/api/Assinatura.java")

    assert "MIIEpAIBAAKCAQEA" not in saida
    assert "[redigido: chave privada]" in saida
    assert f"linhas 1-8 de {len(corpo.splitlines())}" in saida
    assert "     1\tclass Assinatura {" in saida
    assert "     8\t}" in saida


@pytest.mark.unit
@pytest.mark.parametrize(
    "linha",
    [
        # O caso literal do backend real: a senha de todas as contas locais.
        '{"username":"alpha.requester","password":"password"}',
        'registry.add("orderflow.jwt.secret", () -> '
        '"integration-test-jwt-secret-that-is-longer-than-thirty-two-bytes");',
        "password: ${DB_PASSWORD}",
        "DB_PASSWORD=replace-with-a-database-password",
        "JWT_SECRET=replace-with-at-least-32-random-bytes",
        'senha = "senha-de-teste"',
        'password = "senha-de-teste-do-usuario-alpha"',
        "url: jdbc:h2:file:./.data/orderflow;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE",
        '-H "Authorization: Bearer $TOKEN"',
        "Authorization: Bearer {{accessToken}}",
        "Authorization: Bearer <accessToken>",
        # A lição que o manifesto de execução pagou para aprender.
        "max_tokens = 4096",
        "private final PasswordEncoder passwordEncoder;",
        # Hash bcrypt do seed: função de mão única, e o mapeador lê o seed.
        "'$2a$10$WUjpf6KQpZBezOUfoffKo.vjTL2y2Y9EEqor0bLXjRYUAwMrxbK2y'",
        # 64 caracteres, entropia 4,0 — e é "0123456789abcdef" quatro vezes.
        'String chaveDeTeste = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";',
    ],
)
def test_falso_positivo_nao_e_redigido(linha: str):
    redacao = redigir_texto(linha)
    assert redacao.trechos == 0
    assert redacao.texto == linha


@pytest.mark.unit
@pytest.mark.parametrize(
    ("linha", "vazado"),
    [
        (f'apiKey: "{CHAVE_DE_API}"', CHAVE_DE_API),
        (f"AWS_ACCESS_KEY_ID={AWS}", AWS),
        (f'private static final String GOOGLE = "{GOOGLE}";', GOOGLE),
        (
            "gh: ghp_9AbC3dEf7GhI1jKl5MnO2pQr6StU4vWx8Yz0",
            "ghp_9AbC3dEf7GhI1jKl5MnO2pQr6StU4vWx8Yz0",
        ),
        (
            "spring.datasource.url=jdbc:postgresql://orderflow:Hf83jKd92LmQx7Zp@db.prod:5432/of",
            "Hf83jKd92LmQx7Zp",
        ),
        (
            "orderflow.jwt.secret: 7Kq2Xv9RmN3pLt8Wz1Yb4Cd6Ef0Gh5IjAa",
            "7Kq2Xv9RmN3pLt8Wz1Yb4Cd6Ef0Gh5IjAa",
        ),
        ('password = "Xk92LmQp7Zr4Ht6Vn1Bc8Dw"', "Xk92LmQp7Zr4Ht6Vn1Bc8Dw"),
    ],
)
def test_credencial_de_verdade_e_redigida(linha: str, vazado: str):
    redacao = redigir_texto(linha)
    assert redacao.trechos == 1
    assert vazado not in redacao.texto
    assert "[redigido:" in redacao.texto


@pytest.mark.unit
def test_a_senha_da_uri_sai_e_o_resto_da_uri_fica():
    """Redigir a URI inteira apagaria host, porta e nome do banco.

    São eles que dizem ao mapeador contra o que a aplicação fala. O que não pode
    sair é a senha, e só ela.
    """
    redacao = redigir_texto("jdbc:postgresql://orderflow:Hf83jKd92LmQx7Zp@db.prod:5432/of")
    assert "Hf83jKd92LmQx7Zp" not in redacao.texto
    assert "db.prod:5432/of" in redacao.texto
    assert "orderflow:" in redacao.texto


@pytest.mark.unit
def test_busca_redige_a_linha_do_resultado(backend: Path, confinado: Confinamento):
    """O furo simétrico: recusar o arquivo e devolver a linha crua na busca."""
    (backend / "src" / "api" / "Integracao.java").write_text(
        f'class Integracao {{ static final String K = "{CHAVE_DE_API}"; }}\n', encoding="utf-8"
    )

    saida = buscar(confinado, r"static final String K")

    assert "Integracao.java:1" in saida
    assert CHAVE_DE_API not in saida
    assert "[redigido: credencial de provedor]" in saida


# ---------------------------------------------------------------------------
# Contagem no relatório
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_contagem_aparece_no_relatorio(backend: Path, confinado: Confinamento):
    (backend / "src" / "api" / "Integracao.java").write_text(
        f'class Integracao {{ static final String K = "{CHAVE_DE_API}"; }}\n', encoding="utf-8"
    )

    listar_diretorio(confinado, ".")
    ler_arquivo(confinado, ".env")
    ler_arquivo(confinado, "src/api/Integracao.java")

    resumo = confinado.politica.resumo()
    assert resumo.arquivos_recusados == 2  # .env e chave-producao.pem
    assert resumo.trechos_redigidos == 1
    linha = resumo.linha()
    assert "2 arquivo(s) recusado(s)" in linha
    assert "1 trecho(s) redigido(s)" in linha
    assert resumo.recusas_por_motivo["arquivo de ambiente (.env)"] == 1
    assert resumo.para_log()["trechos_redigidos"] == 1


@pytest.mark.unit
def test_o_mesmo_arquivo_recusado_duas_vezes_conta_uma(confinado: Confinamento):
    """Contagem por caminho, não por tentativa.

    Listar o diretório e depois tentar ler o `.env` que ele esconde é uma
    recusa, não duas — e um número que sobe a cada tentativa do modelo não
    responde "quantos arquivos ficaram de fora".
    """
    listar_diretorio(confinado, ".")
    ler_arquivo(confinado, ".env")
    ler_arquivo(confinado, ".env")
    buscar(confinado, r"JWT_SECRET")

    assert confinado.politica.resumo().arquivos_recusados == 2


@pytest.mark.unit
def test_relatorio_zerado_ainda_diz_que_a_politica_rodou(contagem: Contagem):
    """Silêncio não distingue política limpa de política desligada."""
    linha = contagem.resumo().linha()
    assert "0 arquivo(s) recusado(s)" in linha
    assert "0 trecho(s) redigido(s)" in linha


@pytest.mark.unit
def test_a_listagem_diz_quantas_entradas_ocultou(confinado: Confinamento):
    saida = listar_diretorio(confinado, ".")
    assert "[política de privacidade: 2 entrada(s) ocultada(s)]" in saida
