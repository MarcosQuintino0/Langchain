"""`orquestrador init` — o `config.toml` que uma pessoa vai preencher.

O template é o arquivo, comentado, e não um dicionário serializado: o valor dele
está justamente nos comentários que explicam cada campo, e um `tomllib.dumps` os
perderia."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from orquestrador.cli.codigos_de_saida import ERRO_DE_USO, SUCESSO

# Marca dos campos de caminho que não têm padrão possível. Ela é um caminho
# inválido de propósito: `""` viraria `Path(".")` na resolução da configuração, e
# então o diretório do próprio `config.toml` passaria por "backend existe" — o valor
# não preenchido aprovaria justamente a checagem que existe para pegá-lo.
MARCA_DE_PREENCHIMENTO = "PREENCHA"
# `modelo` fica vazio, e não com a marca acima, porque os dois tipos de campo têm
# formas diferentes de "não preenchido". Um caminho falso é detectável e inofensivo;
# um identificador de modelo falso seria enviado ao provedor como se fosse real. E
# nenhum nome de modelo pode aparecer aqui (princípio 6): string vazia é o único
# valor que o `doctor` consegue distinguir de uma escolha deliberada.
MODELO_DE_CONFIG_DE_PROJETO = """# Configuração do orquestrador — gerada por `orquestrador init`.
#
# Caminhos relativos são resolvidos contra o diretório DESTE arquivo.
# Nenhum nome de modelo aparece em código: todos vêm daqui.
#
# Os campos marcados com PREENCHA (e os `modelo` vazios) não têm padrão possível.
# Rode `orquestrador doctor` depois de preencher: ele confere cada um e diz o que
# fazer em cada falha.

[caminhos]
# A skill `qa-api` mora em OUTRO repositório e é apenas consumida, nunca modificada.
# Caminho absoluto: os dois projetos são independentes e não têm posição relativa
# garantida.
skill = "PREENCHA/caminho/para/skills/qa-api"
# scripts = ".../skills/qa-api/scripts"   # derivado de `skill` quando omitido

# O backend a mapear e o projeto Cypress que recebe os testes gerados.
backend = "PREENCHA/caminho/para/o/backend"
projeto_testes = "PREENCHA/caminho/para/o/projeto-de-testes"

# Relativos ao projeto de testes. Os padrões abaixo são a convenção da skill;
# troque-os se o seu projeto usa outro layout.
dir_recursos = "cypress/e2e/apis"
dir_schemas = "cypress/fixtures/schemas"
support_compartilhado = "cypress/support/api"
graph = ".agents/state/qa-api/graphify-out/graph.json"

# Relativo a este diretório: logs, artefatos e sandbox de cada execução.
saida = ".execucoes"

# O JSONL local continua sendo a fonte primária. OTLP/HTTP é apenas uma cópia de
# traces e métricas sanitizadas para um Collector, desligada por padrão.
[observabilidade]
intervalo_pulso_s = 30.0

[observabilidade.otlp]
habilitado = false
endpoint = "http://localhost:4318"
timeout_s = 5.0
service_name = "orquestrador"
headers_env = "OTEL_EXPORTER_OTLP_HEADERS"
incluir_identificadores = false

[skill]
# Hash dos `.mjs` que este orquestrador invoca. Vazio desliga a trava — que é o
# certo até você conferir o contrato pela primeira vez. `orquestrador doctor`
# imprime o hash atual para você colar aqui.
impressao_esperada = ""

[openrouter]
base_url = "https://openrouter.ai/api/v1"
# A chave vem SEMPRE do ambiente, nunca deste arquivo. Crie um `.env` ao lado dele.
api_key_env = "OPENROUTER_API_KEY"
timeout_s = 180.0
max_retries = 2

# Para onde o código-fonte do seu backend pode ir. Conjunto FECHADO: uma `base_url`
# fora daqui não carrega, e não há fallback. Trocar o destino é a mudança de uma
# linha que passa despercebida numa revisão — declarar o host novo aqui, na mesma
# mudança, é o que a torna revisável.
hosts_permitidos = ["openrouter.ai"]

# O OpenRouter é um ROTEADOR: o endpoint é um só, e quem executa a inferência é
# escolhido por ele a cada requisição. Estes dois campos são o que se pede a esse
# roteador, e viajam no corpo de toda chamada.
#
# "deny" restringe a provedores que não retêm o conteúdo enviado. É o padrão, e é o
# que a maioria das empresas precisa antes de aprovar mandar código para fora.
retencao_de_dados = "deny"

# Vazio = o roteador escolhe. Preenchido = só estes, e o fallback é DESLIGADO junto
# — restringir a lista sem desligar o fallback não restringe nada, porque o primeiro
# provedor indisponível faz o roteador cair para outro qualquer.
provedores_permitidos = []

# ---------------------------------------------------------------------------
# Orçamento. Sem nada aqui, não há teto — e é esse o padrão: um orçamento que
# aparece sem ninguém pedir interrompe execução legítima e ensina a desligá-lo.
#
# O que ele promete: nenhuma chamada nova começa depois que o teto foi alcançado.
# O que ele NÃO promete: que o teto não será ultrapassado. O custo de uma chamada
# só se conhece depois dela, então a última pode passar.
#
# Dois escopos, e os dois valem juntos. Só o de execução deixaria um recurso
# patológico consumir tudo antes de o segundo começar; só o de recurso não pararia
# uma lista de trinta que sangram devagar.
#
# Campo ausente = sem teto. Zero é um teto legítimo ("não me deixe chamar o
# modelo") e é diferente de ausente.
#
# [orcamento.por_execucao]
# chamadas = 200
# tokens = 5_000_000
# caracteres_de_tools = 20_000_000
# segundos = 3600
#
# [orcamento.por_recurso]
# chamadas = 40
# tokens = 800_000

# A régua do `orquestrador --estimar`, que conta os endpoints do backend e devolve
# uma faixa de token sem chamar modelo nenhum. Os padrões saíram de UMA execução
# medida — troque-os pelos números da sua, que é para isso que eles estão aqui.
[orcamento.estimativa]
tokens_por_endpoint_min = 40000
tokens_por_endpoint_max = 120000

# Identificador do modelo no provedor, na forma que ele espera.
# O mapeador tem o julgamento mais difícil e o menor volume de saída: pede o modelo
# mais capaz. O executor tem o volume de tokens e trabalho mecânico se o gabarito
# for bom: aceita um modelo mais barato, porque o Gate B pega os erros de forma
# determinística.
[estagios.mapeador]
modelo = ""
temperatura = 0.0
modo_estruturado = "prompt"
max_tentativas_schema = 3
limite_passos = 60

[estagios.executor]
modelo = ""
temperatura = 0.0
modo_estruturado = "prompt"
max_tentativas_schema = 3

[gates.a]
flags = ["--so-manifesto"]
max_tentativas = 3

[gates.b]
flags = ["--exigir-campos"]
max_tentativas = 3
exigir_cobertura = true

[execucao]
node = "node"
graphify = "graphify"
timeout_s = 600

# Prettier e ESLint do Gate B. Lista vazia desliga a etapa; ligue apontando para o
# toolchain do projeto consumidor. O diretório do recurso entra como último argumento.
#   prettier = ["npx", "--no-install", "prettier", "--check"]
#   eslint   = ["npx", "--no-install", "eslint", "--format", "json"]
prettier = []
eslint = []
exigir_formatadores = false

# Bloco 3, só com --rodar-cypress. O comando PRECISA conter a marca {relatorio} no
# argumento que diz ao repórter onde escrever o JSON.
#   cypress = ["npx", "--no-install", "cypress", "run",
#              "--reporter", "json", "--reporter-options", "output={relatorio}"]
cypress = []

max_bytes_arquivo = 2000000
max_resultados_busca = 40
"""


def comando_init(argv: list[str], console: Console) -> int:
    analisador = argparse.ArgumentParser(
        prog="orquestrador init",
        description=(
            "Escreve um config.toml de projeto, comentado, com os campos que "
            "precisam ser preenchidos."
        ),
    )
    analisador.add_argument(
        "--em",
        type=Path,
        default=None,
        help="diretório onde escrever (padrão: o diretório atual).",
    )
    analisador.add_argument(
        "--forcar",
        action="store_true",
        help="sobrescreve um config.toml existente.",
    )
    args = analisador.parse_args(argv)

    destino = (args.em or Path.cwd()).expanduser().resolve()
    if not destino.is_dir():
        console.print(f"[red]diretório não encontrado:[/red] {destino}")
        return ERRO_DE_USO

    arquivo = destino / "config.toml"
    if arquivo.exists() and not args.forcar:
        # Recusar é o padrão porque este arquivo é do usuário: ele tem caminhos,
        # escolha de modelo e a impressão da skill conferida à mão. Sobrescrever em
        # silêncio apagaria trabalho que não temos como recuperar.
        console.print(
            f"[red]já existe:[/red] {arquivo}\n"
            "Não sobrescrevo configuração existente. Use --forcar se for isso mesmo, "
            "ou --em para escrever em outro diretório."
        )
        return ERRO_DE_USO

    arquivo.write_text(MODELO_DE_CONFIG_DE_PROJETO, encoding="utf-8")
    console.print(f"[green]escrito:[/green] {escape(str(arquivo))}")
    # `escape`: o texto cita blocos do TOML, e o Rich leria "[estagios.*]" como tag
    # de estilo e o engoliria — o passo 1 sairia mandando preencher "os dois .modelo".
    console.print(
        escape(
            "\nPróximos passos:\n"
            f"  1. preencha os campos marcados com {MARCA_DE_PREENCHIMENTO} e os dois "
            "[estagios.*].modelo;\n"
            "  2. crie um .env ao lado com a chave do provedor "
            f"(OPENROUTER_API_KEY=...) — ela nunca vai para o {arquivo.name};\n"
            "  3. rode `orquestrador doctor` e conserte o que ele apontar."
        )
    )
    return SUCESSO
