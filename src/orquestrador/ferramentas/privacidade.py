"""O que do backend do cliente pode chegar ao modelo — e o que nunca sai da máquina.

Fronteira
---------
Este módulo é o **dono da política de privacidade** das tools de leitura. Ele
responde a duas perguntas, e só a essas duas:

* este caminho pode ser aberto? (`motivo_de_recusa`)
* este texto pode ser enviado como está? (`redigir`)

Ele **não** confina caminho. Confinamento — `sob_a_raiz`, `Confinamento`,
`confinar` — é de `ferramentas/arquivos.py`, é outra pergunta ("este caminho
escapa da raiz autorizada?") e continua valendo antes desta camada. Um caminho
pode estar perfeitamente dentro da raiz e ainda assim não ter o que fazer num
prompt: `.env`, chave privada, `credentials.json`. A privacidade é a camada de
cima; ela filtra o que o confinamento já aprovou, nunca o substitui.

Ele também não decide fluxo, não fala com o modelo e não conhece agente nem gate:
devolve texto redigido e motivo, e quem chamou é que resolve o que fazer.

As três camadas, em ordem de aplicação
--------------------------------------
1. **Denylist, sempre ativa e não desligável.** Nome de arquivo, nome de
   diretório e extensão. Ela é deliberadamente difícil de furar por acidente:
   não há configuração que a afrouxe, e um `!padrao` do `.llmignore` **não** a
   reabre. Reabrir segredo por engano de sintaxe é o furo que este item existe
   para fechar.
2. **`.llmignore` da raiz do backend**, sintaxe de `.gitignore`. É como o cliente
   declara o que é dele e não sai — o diretório de dumps, a pasta de exportação
   do ERP, o que for. Ausente, vale só a denylist.
3. **Redação de segredo no conteúdo**, aplicada ao texto que a tool devolveria.
   Redige o trecho e conta; **não** descarta o arquivo. O mapeador precisa do
   resto do arquivo para trabalhar, e um arquivo que some em silêncio vira um
   endpoint que o inventário não declara.

Por que precisão, e não recall, no detector de conteúdo
-------------------------------------------------------
Backend de verdade é cheio de senha falsa: `password: password` no seed, "a senha
de todas as contas locais é a literal `password`" na documentação,
`orderflow.jwt.secret = "integration-test-jwt-secret-that-is-longer-than-thirty-two-bytes"`
no teste de integração. Redigir isso não protege ninguém e ensina o leitor a
ignorar `[redigido: ...]` — que é exatamente como se perde a redação que importa.
O manifesto de execução aprendeu a mesma lição apagando `max_tokens` porque
`token` casava.

Daí as três exigências cumulativas da regra de entropia: o **nome** do campo tem
de denunciar credencial (por segmento, nunca por substring), o **valor** tem de
ter diversidade de caixa e dígito (o que derruba frase-com-hífens), e não pode
parecer marcador (`${...}`, `<...>`, `changeme`, `example`). Valor que começa com
`$` sai fora de graça — cobre tanto `${DB_PASSWORD}` quanto o hash bcrypt
`$2a$10$...` do seed, que é função de mão única e não é credencial em claro.

As regras de prefixo conhecido (`sk-ant-`, `AKIA...`, `ghp_`) não passam por esse
crivo: prefixo literal com comprimento mínimo não tem falso positivo plausível, e
o custo de deixar passar é uma chave de produção num prompt.

Sobre a duplicação da segmentação de nome
-----------------------------------------
`observabilidade/manifesto_de_execucao.py` tem heurística parecida para nome de
campo de JSON. Não é a mesma função e não deve virar uma: lá o alvo é chave de
dicionário de configuração nossa; aqui é identificador de código-fonte de
terceiro, com camelCase, YAML pontuado e `.env`. Além disso o manifesto importa
`ferramentas/processo.py` — unificar aqui fecharia um ciclo de import.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

__all__ = [
    "CONTAGEM_DO_PROCESSO",
    "DIRETORIOS_DE_SEGREDO",
    "EXTENSOES_DE_SEGREDO",
    "NOMES_DE_SEGREDO",
    "NOME_DO_LLMIGNORE",
    "PALAVRAS_DE_SEGREDO_NO_NOME",
    "Contagem",
    "Llmignore",
    "PoliticaDePrivacidade",
    "Redacao",
    "ResumoDaPolitica",
    "motivo_da_denylist",
    "redigir_texto",
]

NOME_DO_LLMIGNORE = ".llmignore"

# ---------------------------------------------------------------------------
# Denylist
# ---------------------------------------------------------------------------

# Diretório que, em qualquer nível do caminho, condena tudo abaixo dele. Todos
# começam com ponto porque é assim que ferramenta de credencial nomeia o próprio
# diretório — e porque um `secrets/` sem ponto é nome plausível de pacote Java.
DIRETORIOS_DE_SEGREDO = frozenset(
    {
        ".aws",
        ".azure",
        ".credentials",
        ".docker",
        ".gcloud",
        ".gnupg",
        ".gpg",
        ".keys",
        ".kube",
        ".pki",
        ".secrets",
        ".ssh",
        ".vault",
    }
)

# Extensão que só existe para guardar material de chave. `.crt`, `.cer` e `.csr`
# ficam de fora de propósito: certificado e pedido de assinatura são públicos, e
# recusá-los cegaria o mapeador em backend com mTLS sem proteger nada.
EXTENSOES_DE_SEGREDO = frozenset(
    {
        ".asc",
        ".der",
        ".gpg",
        ".jks",
        ".kdbx",
        ".key",
        ".keystore",
        ".p12",
        ".p8",
        ".pem",
        ".pfx",
        ".pgp",
        ".pk8",
        ".ppk",
        ".tfstate",
        ".tfvars",
        ".truststore",
    }
)

NOMES_DE_SEGREDO = frozenset(
    {
        ".dockercfg",
        ".git-credentials",
        ".htpasswd",
        ".my.cnf",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".pypirc",
        "_netrc",
        "authorized_keys",
        "credentials",
        "credentials.json",
        "gcp-key.json",
        "sa-key.json",
        "service-account.json",
    }
)

# Nome que **começa** assim é chave SSH. O `.pub` entra junto: ele é público, mas
# casa com o mesmo prefixo e nada se perde recusando-o.
PREFIXOS_DE_SEGREDO = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "client_secret")

# Palavra que, sozinha num segmento do nome, denuncia arquivo de credencial:
# `.jwt-secret`, `SEED_CREDENTIALS.md`, `application-secrets.yml`.
#
# Comparação por segmento — separadores `.`, `-`, `_` —, nunca por substring. Um
# `re.search("password")` recusaria `SeedPasswordHashTest.java`, que é teste
# legítimo; a segmentação o salva porque camelCase não gera o segmento
# `password`. `auth` e `authorization` **não** entram: `AuthController.java` e
# `AUTHORIZATION.md` são exatamente o que o mapeador precisa ler.
PALAVRAS_DE_SEGREDO_NO_NOME = frozenset(
    {
        "apikey",
        "credencial",
        "credenciais",
        "credential",
        "credentials",
        "keystore",
        "passwd",
        "password",
        "passwords",
        "secret",
        "secrets",
        "segredo",
        "senha",
        "senhas",
    }
)

# Par adjacente que só junto significa credencial. `key` sozinho aparece em nome
# legítimo demais (`KeyGenerator.java`, `keyboard.js`) para entrar acima.
PARES_DE_SEGREDO_NO_NOME = frozenset({("api", "key"), ("private", "key"), ("secret", "key")})

# Extensão de código-fonte fica de fora da regra de palavra no nome. `SecretRotationService.java`
# e `secret_manager.py` são código, e código é o que o mapeador foi contratado
# para ler — o que houver de segredo dentro deles ainda passa pela redação de
# conteúdo. `.sql`, `.yml` e `.json` NÃO entram aqui: `secrets.yml` e `seed.sql`
# guardam valor, não lógica.
EXTENSOES_DE_CODIGO = frozenset(
    {
        ".c",
        ".cc",
        ".cjs",
        ".cpp",
        ".cs",
        ".go",
        ".groovy",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
    }
)

_SEPARADOR_DE_SEGMENTO = re.compile(r"[^a-z0-9]+")


def _segmentos(nome: str) -> list[str]:
    return [parte for parte in _SEPARADOR_DE_SEGMENTO.split(nome.lower()) if parte]


def _palavra_de_segredo(nome: str) -> str | None:
    segmentos = _segmentos(nome)
    for parte in segmentos:
        if parte in PALAVRAS_DE_SEGREDO_NO_NOME:
            return parte
    for anterior, seguinte in pairwise(segmentos):
        if (anterior, seguinte) in PARES_DE_SEGREDO_NO_NOME:
            return f"{anterior} {seguinte}"
    return None


def _e_arquivo_de_ambiente(nome: str) -> bool:
    """`.env`, `.env.local`, `.env.production`, `.envrc`, `producao.env`.

    Sem exceção para `.env.example`, e é deliberado: a lista de exceções é
    justamente onde a chave real acaba parando, porque copiar o `.env` por cima
    do exemplo é o erro mais comum que existe. O custo de recusá-lo é baixo — os
    nomes das variáveis reaparecem no `application.yml` e na documentação —, e a
    recusa aparece contada no relatório em vez de acontecer em silêncio.
    """
    minusculo = nome.lower()
    return minusculo.startswith(".env") or minusculo.endswith(".env")


def motivo_da_denylist(relativo: str) -> str | None:
    """Motivo pelo qual a denylist recusa este caminho, ou `None` se ela o aceita.

    `relativo` é o caminho em POSIX a partir da raiz do backend. A comparação
    ignora a caixa em qualquer sistema: `.ENV` e `Id_Rsa` são o mesmo arquivo no
    Windows, e num Linux errar para o lado de recusar a mais custa um arquivo,
    enquanto errar para o outro custa uma credencial.
    """
    partes = [parte for parte in relativo.split("/") if parte not in ("", ".")]
    if not partes:
        return None

    for diretorio in partes[:-1]:
        if diretorio.lower() in DIRETORIOS_DE_SEGREDO:
            return f"diretório de segredo {diretorio}/"

    nome = partes[-1]
    minusculo = nome.lower()
    if minusculo in DIRETORIOS_DE_SEGREDO:
        return f"diretório de segredo {nome}/"
    if _e_arquivo_de_ambiente(nome):
        return "arquivo de ambiente (.env)"
    if minusculo in NOMES_DE_SEGREDO:
        return f"nome de credencial {nome}"
    if minusculo.startswith(PREFIXOS_DE_SEGREDO):
        return f"nome de credencial {nome}"
    sufixo = Path(minusculo).suffix
    if sufixo in EXTENSOES_DE_SEGREDO:
        return f"extensão de material de chave {sufixo}"
    if sufixo not in EXTENSOES_DE_CODIGO and (palavra := _palavra_de_segredo(nome)):
        return f"{palavra!r} no nome do arquivo"
    return None


# ---------------------------------------------------------------------------
# .llmignore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Regra:
    padrao: str
    regex: re.Pattern[str]
    negacao: bool
    so_diretorio: bool


def _traduzir_glob(corpo: str) -> str:
    """Glob de `.gitignore` para expressão regular, `*` sem atravessar `/`."""
    saida: list[str] = []
    indice = 0
    while indice < len(corpo):
        caractere = corpo[indice]
        if corpo.startswith("**/", indice):
            saida.append("(?:.*/)?")
            indice += 3
        elif corpo.startswith("/**", indice):
            saida.append("(?:/.*)?")
            indice += 3
        elif corpo.startswith("**", indice):
            saida.append(".*")
            indice += 2
        elif caractere == "*":
            saida.append("[^/]*")
            indice += 1
        elif caractere == "?":
            saida.append("[^/]")
            indice += 1
        elif caractere == "[":
            fim = corpo.find("]", indice + 1)
            if fim == -1:
                saida.append(re.escape(caractere))
                indice += 1
            else:
                classe = corpo[indice + 1 : fim]
                if classe.startswith("!"):
                    classe = "^" + classe[1:]
                saida.append(f"[{classe}]")
                indice = fim + 1
        else:
            saida.append(re.escape(caractere))
            indice += 1
    return "".join(saida)


def _compilar_regra(linha: str) -> _Regra | None:
    padrao = linha.rstrip()
    if not padrao.strip() or padrao.lstrip().startswith("#"):
        return None

    original = padrao
    negacao = padrao.startswith("!")
    if negacao:
        padrao = padrao[1:]
    if padrao.startswith(("\\!", "\\#")):
        padrao = padrao[1:]

    so_diretorio = padrao.endswith("/")
    padrao = padrao.rstrip("/")
    if not padrao:
        return None

    ancorado = padrao.startswith("/") or "/" in padrao
    padrao = padrao.removeprefix("/")

    corpo = _traduzir_glob(padrao)
    prefixo = "" if ancorado else "(?:.*/)?"
    # `re.IGNORECASE` porque o `.llmignore` é declaração de intenção do cliente, e
    # `*.PEM` querendo dizer `*.pem` é erro que não pode custar um vazamento.
    return _Regra(
        padrao=original,
        regex=re.compile(f"^{prefixo}{corpo}$", re.IGNORECASE),
        negacao=negacao,
        so_diretorio=so_diretorio,
    )


class Llmignore:
    """Regras de `.llmignore`, na semântica de `.gitignore` que importa aqui.

    O subconjunto implementado: comentário, linha em branco, negação com `!`,
    âncora na raiz com `/` inicial, restrição a diretório com `/` final, `*`,
    `?`, `**` e classe de caractere. A última regra que casa decide, e diretório
    já excluído não é reaberto por negação de filho — igual ao Git.

    O que **não** é suportado, de propósito: `.llmignore` aninhado em
    subdiretório. Uma raiz, um arquivo, uma resposta para "o que não sai daqui";
    política espalhada em dez arquivos é política que ninguém consegue auditar.
    """

    def __init__(self, regras: list[_Regra] | None = None) -> None:
        self.regras: list[_Regra] = regras or []

    @classmethod
    def de_texto(cls, texto: str) -> Llmignore:
        regras = [regra for linha in texto.splitlines() if (regra := _compilar_regra(linha))]
        return cls(regras)

    @classmethod
    def da_raiz(cls, raiz: Path) -> Llmignore:
        """Lê `<raiz>/.llmignore`; ausente ou ilegível vira conjunto vazio.

        Ilegível vira vazio, e não exceção, porque a denylist continua valendo:
        um `.llmignore` corrompido não pode impedir a execução, e também não pode
        ser confundido com permissão para ler tudo.
        """
        try:
            return cls.de_texto((raiz / NOME_DO_LLMIGNORE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return cls()

    def __bool__(self) -> bool:
        return bool(self.regras)

    def padrao_que_ignora(self, relativo: str, *, diretorio: bool = False) -> str | None:
        """Padrão que exclui este caminho, ou `None`.

        Cada ancestral é testado antes do próprio caminho: no Git, excluir
        `dumps/` exclui `dumps/2026/clientes.sql` sem que ninguém precise
        escrever a regra do neto.
        """
        partes = [parte for parte in relativo.split("/") if parte not in ("", ".")]
        veredito: str | None = None
        for indice, _ in enumerate(partes):
            alvo = "/".join(partes[: indice + 1])
            e_diretorio = diretorio or indice < len(partes) - 1
            for regra in self.regras:
                if regra.so_diretorio and not e_diretorio:
                    continue
                if regra.regex.match(alvo):
                    veredito = None if regra.negacao else regra.padrao
            if veredito is not None and indice < len(partes) - 1:
                return veredito
        return veredito


# ---------------------------------------------------------------------------
# Detector de segredo no conteúdo
# ---------------------------------------------------------------------------

MOTIVO_CHAVE_PRIVADA = "chave privada"
MOTIVO_CREDENCIAL_DE_PROVEDOR = "credencial de provedor"
MOTIVO_SENHA_EM_URI = "senha em string de conexão"
MOTIVO_CREDENCIAL_ATRIBUIDA = "credencial atribuída"

_INICIO_DE_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_FIM_DE_PEM = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")

# Prefixo literal de credencial emitida por provedor, com comprimento mínimo. Não
# passa pelo crivo de placeholder: `sk-ant-` seguido de trinta caracteres não é
# exemplo de ninguém, e deixar passar custa uma chave de produção.
_CREDENCIAL_DE_PROVEDOR = re.compile(
    r"""
    (?:
        sk-or-v1-[A-Za-z0-9]{20,}
      | sk-ant-[A-Za-z0-9_-]{20,}
      | sk-proj-[A-Za-z0-9_-]{20,}
      | sk-[A-Za-z0-9]{32,}
      | gh[pousr]_[A-Za-z0-9]{30,}
      | github_pat_[A-Za-z0-9_]{40,}
      | glpat-[A-Za-z0-9_-]{20,}
      | xox[baprse]-[A-Za-z0-9-]{12,}
      | (?:AKIA|ASIA)[0-9A-Z]{16}
      | AIza[0-9A-Za-z_-]{35}
      | ya29\.[0-9A-Za-z_-]{20,}
      | hf_[A-Za-z0-9]{30,}
      | npm_[A-Za-z0-9]{36}
      | dop_v1_[0-9a-f]{60,}
      | (?:sk|pk|rk)_live_[0-9A-Za-z]{20,}
      | SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}
      | eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}
    )
    """,
    re.VERBOSE,
)

_URI_COM_SENHA = re.compile(
    r"""(?P<esquema>[a-zA-Z][a-zA-Z0-9+.\-]*://)
        (?P<usuario>[^\s:/@"'<>]{1,120})
        :(?P<senha>[^\s:/@"'<>]{1,200})@""",
    re.VERBOSE,
)

_CABECALHO_DE_AUTORIZACAO = re.compile(
    r"\b(?P<tipo>Bearer|Basic)\s+(?P<valor>[A-Za-z0-9._~+/=-]{20,})"
)

_ATRIBUICAO = re.compile(
    r"""(?P<chave>[A-Za-z_][A-Za-z0-9_.\-]{0,60})["']?
        \s*(?:[:=]{1,2}|=>)\s*
        (?P<aspas>["'`]?)
        (?P<valor>[^"'`\s,;)\]}]{16,400})
        (?P=aspas)""",
    re.VERBOSE,
)

# Nome de campo que denuncia credencial. `token` no singular é credencial;
# `tokens` no plural é contagem de uso do modelo — a distinção que o manifesto de
# execução pagou para aprender.
_SEGMENTOS_DE_CREDENCIAL = frozenset(
    {
        "apikey",
        "credential",
        "credentials",
        "passwd",
        "password",
        "secret",
        "segredo",
        "senha",
        "token",
    }
)
_PARES_DE_CREDENCIAL = frozenset({("api", "key"), ("private", "key"), ("secret", "key")})

# Sufixo que marca **nome de variável de ambiente**, não valor.
_SUFIXO_DE_NOME_DE_VARIAVEL = "_env"

_TAMANHO_MINIMO_DO_SEGREDO = 20
_ENTROPIA_MINIMA = 3.2

_MARCADOR = re.compile(r"\$\{|\{\{|%\(|#\{|<[A-Za-z]|\.\.\.|x{4,}", re.IGNORECASE)

# Palavra que, presente no valor, denuncia exemplo. Vale só para as regras que
# dependem de heurística; prefixo de provedor e bloco PEM ignoram esta lista.
_PALAVRAS_DE_EXEMPLO = (
    "aqui",
    "changeme",
    "change-me",
    "dummy",
    "example",
    "exemplo",
    "fake",
    "foobar",
    "insert",
    "mock",
    "placeholder",
    "replace",
    "sample",
    "seu-",
    "seu_",
    "test",
    "teste",
    "todo",
    "your",
)

_HEX_LONGO = re.compile(r"^[0-9a-f]{32,}$", re.IGNORECASE)


def _e_nome_de_credencial(chave: str) -> bool:
    nome = chave.lower()
    if nome.endswith(_SUFIXO_DE_NOME_DE_VARIAVEL):
        return False
    segmentos = _segmentos(nome)
    if any(parte in _SEGMENTOS_DE_CREDENCIAL for parte in segmentos):
        return True
    return any(par in _PARES_DE_CREDENCIAL for par in pairwise(segmentos))


def _parece_marcador(valor: str) -> bool:
    minusculo = valor.lower()
    if _MARCADOR.search(valor):
        return True
    return any(palavra in minusculo for palavra in _PALAVRAS_DE_EXEMPLO)


def _entropia(valor: str) -> float:
    total = len(valor)
    if total == 0:
        return 0.0
    return -sum(
        (ocorrencias / total) * math.log2(ocorrencias / total)
        for ocorrencias in Counter(valor).values()
    )


def _e_periodico(valor: str) -> bool:
    """O valor é a repetição de um bloco menor?

    `"0123456789abcdef" * 4` tem entropia 4,0 — mais alta que a de muita chave de
    verdade — e é o segredo de teste que o `JwtServiceTest` do backend real usa.
    Entropia mede distribuição de caractere e é cega para ordem; a periodicidade
    é o que separa "sorteado" de "digitado até encher trinta e dois bytes".

    O truque do `(valor + valor).find(valor, 1)`: a posição em que a string
    reaparece em si mesma duplicada é o menor período dela. Igual ao comprimento
    significa aperiódica.
    """
    return (valor + valor).find(valor, 1) < len(valor)


def _parece_segredo(valor: str) -> bool:
    """O valor parece credencial de verdade, e não senha de teste nem marcador?

    As condições são cumulativas de propósito. Cada uma sozinha erra:

    * só entropia aprova `integration-test-jwt-secret-that-is-longer-than-...`,
      que tem vinte caracteres distintos e nenhum segredo;
    * só comprimento aprova qualquer caminho de arquivo;
    * só diversidade aprova `Senha123` repetido.

    Valor que começa com `$` sai antes de tudo: cobre `${DB_PASSWORD}`,
    `$env:JWT_SECRET` e o hash bcrypt `$2a$10$...` do seed, que é função de mão
    única — redigi-lo apagaria dado que o mapeador lê para entender o login sem
    proteger nenhuma senha em claro.
    """
    if len(valor) < _TAMANHO_MINIMO_DO_SEGREDO:
        return False
    if valor.startswith(("$", "/", "~", ".", "-", "@")):
        return False
    if "://" in valor or "\\" in valor:
        return False
    if _parece_marcador(valor) or _e_periodico(valor):
        return False
    if not (
        _HEX_LONGO.match(valor)
        or (any(c.isupper() for c in valor) and any(c.isdigit() for c in valor))
    ):
        return False
    return _entropia(valor) >= _ENTROPIA_MINIMA


def _redigido(motivo: str) -> str:
    return f"[redigido: {motivo}]"


@dataclass(frozen=True)
class Redacao:
    """Texto já redigido, quantos trechos saíram e por qual regra."""

    texto: str
    trechos: int
    por_motivo: dict[str, int] = field(default_factory=dict[str, int])


def _redigir_linha(linha: str, contagem: Counter[str]) -> str:
    def apagar_provedor(achado: re.Match[str]) -> str:
        del achado
        contagem[MOTIVO_CREDENCIAL_DE_PROVEDOR] += 1
        return _redigido(MOTIVO_CREDENCIAL_DE_PROVEDOR)

    def apagar_senha_de_uri(achado: re.Match[str]) -> str:
        senha = achado.group("senha")
        if _parece_marcador(senha) or len(senha) < 4:
            return achado.group(0)
        contagem[MOTIVO_SENHA_EM_URI] += 1
        return (
            f"{achado.group('esquema')}{achado.group('usuario')}:{_redigido(MOTIVO_SENHA_EM_URI)}@"
        )

    def apagar_cabecalho(achado: re.Match[str]) -> str:
        if not _parece_segredo(achado.group("valor")):
            return achado.group(0)
        contagem[MOTIVO_CREDENCIAL_ATRIBUIDA] += 1
        return f"{achado.group('tipo')} {_redigido(MOTIVO_CREDENCIAL_ATRIBUIDA)}"

    def apagar_atribuicao(achado: re.Match[str]) -> str:
        if not _e_nome_de_credencial(achado.group("chave")):
            return achado.group(0)
        if not _parece_segredo(achado.group("valor")):
            return achado.group(0)
        contagem[MOTIVO_CREDENCIAL_ATRIBUIDA] += 1
        inteiro = achado.group(0)
        return inteiro.replace(achado.group("valor"), _redigido(MOTIVO_CREDENCIAL_ATRIBUIDA))

    linha = _CREDENCIAL_DE_PROVEDOR.sub(apagar_provedor, linha)
    linha = _URI_COM_SENHA.sub(apagar_senha_de_uri, linha)
    linha = _CABECALHO_DE_AUTORIZACAO.sub(apagar_cabecalho, linha)
    return _ATRIBUICAO.sub(apagar_atribuicao, linha)


def redigir_texto(texto: str) -> Redacao:
    """Redige segredo preservando **a contagem de linhas** do original.

    Preservar linha é requisito, não capricho: `ler_arquivo` numera o que
    devolve e o modelo volta ao arquivo por `offset`. Uma redação que colapsasse
    um bloco PEM de vinte e cinco linhas em uma faria toda referência de linha
    posterior apontar para o lugar errado — e o modelo culparia o próprio
    raciocínio, não a redação.
    """
    contagem: Counter[str] = Counter()
    saida: list[str] = []
    dentro_de_pem = False

    for linha in texto.split("\n"):
        if dentro_de_pem:
            saida.append("")
            if _FIM_DE_PEM.search(linha):
                dentro_de_pem = False
            continue
        if (inicio := _INICIO_DE_PEM.search(linha)) is not None:
            contagem[MOTIVO_CHAVE_PRIVADA] += 1
            saida.append(linha[: inicio.start()] + _redigido(MOTIVO_CHAVE_PRIVADA))
            dentro_de_pem = not _FIM_DE_PEM.search(linha, inicio.end())
            continue
        saida.append(_redigir_linha(linha, contagem))

    return Redacao(
        texto="\n".join(saida),
        trechos=sum(contagem.values()),
        por_motivo=dict(contagem),
    )


# ---------------------------------------------------------------------------
# Política, contagem e relatório
# ---------------------------------------------------------------------------


@dataclass
class Contagem:
    """O que a política fez, acumulado — a base da linha de relatório.

    Arquivo recusado é contado **por caminho**, não por tentativa: listar um
    diretório e depois tentar ler o `.env` que ele esconde é uma recusa, não
    duas. Trecho redigido é contado por ocorrência, porque cada um é uma redação
    distinta no texto que o modelo recebeu.
    """

    recusados: dict[str, str] = field(default_factory=dict[str, str])
    redacoes: Counter[str] = field(default_factory=Counter[str])

    def recusar(self, caminho: str, motivo: str) -> None:
        self.recusados.setdefault(caminho, motivo)

    def redigir(self, por_motivo: dict[str, int]) -> None:
        self.redacoes.update(por_motivo)

    def resumo(self) -> ResumoDaPolitica:
        por_motivo: Counter[str] = Counter(self.recusados.values())
        return ResumoDaPolitica(
            arquivos_recusados=len(self.recusados),
            trechos_redigidos=sum(self.redacoes.values()),
            recusas_por_motivo=dict(por_motivo),
            redacoes_por_motivo=dict(self.redacoes),
        )

    def zerar(self) -> None:
        self.recusados.clear()
        self.redacoes.clear()


@dataclass(frozen=True)
class ResumoDaPolitica:
    """Os dois números que o relatório precisa, e a quebra por motivo."""

    arquivos_recusados: int
    trechos_redigidos: int
    recusas_por_motivo: dict[str, int] = field(default_factory=dict[str, int])
    redacoes_por_motivo: dict[str, int] = field(default_factory=dict[str, int])

    def linha(self) -> str:
        """Uma linha para o relatório. Sempre sai, inclusive zerada.

        Zerada também é informação: "nenhum arquivo recusado" prova que a
        política rodou, enquanto silêncio não distingue política limpa de
        política desligada.
        """
        return (
            f"política de privacidade: {self.arquivos_recusados} arquivo(s) recusado(s), "
            f"{self.trechos_redigidos} trecho(s) redigido(s)"
        )

    def para_log(self) -> dict[str, object]:
        return {
            "arquivos_recusados": self.arquivos_recusados,
            "trechos_redigidos": self.trechos_redigidos,
            "recusas_por_motivo": self.recusas_por_motivo,
            "redacoes_por_motivo": self.redacoes_por_motivo,
        }


# A contagem é do processo, e não de quem monta o relatório, porque a política é
# construída lá no fundo — dentro do `Confinamento` que `criar_ferramentas`
# instancia por tentativa — e quem escreve o relatório nunca vê esse objeto.
# Sem um ponto de encontro, o número existiria e não seria visto por ninguém, que
# é a definição de política silenciosa. Teste que precisa de isolamento passa a
# própria `Contagem` ao construtor.
CONTAGEM_DO_PROCESSO = Contagem()


class PoliticaDePrivacidade:
    """Denylist + `.llmignore` + redação de conteúdo, para uma raiz de backend."""

    def __init__(
        self,
        raiz: Path | str,
        *,
        llmignore: Llmignore | None = None,
        contagem: Contagem | None = None,
    ) -> None:
        self.raiz = Path(raiz)
        self._raiz_canonica = self.raiz.resolve()
        self.llmignore = Llmignore.da_raiz(self.raiz) if llmignore is None else llmignore
        self.contagem = CONTAGEM_DO_PROCESSO if contagem is None else contagem

    def relativo(self, alvo: Path) -> str:
        """Caminho POSIX a partir da raiz, sem pagar `resolve()` por arquivo.

        A busca consulta a política uma vez por arquivo do backend, e
        `Path.resolve()` no Windows é ida ao sistema de arquivos. Quem chama já
        trabalha com caminho canônico — `Confinamento.resolver` o devolve assim e
        o `os.walk` desce de uma raiz já canônica —, então a tentativa direta
        acerta quase sempre e o `resolve()` fica para o caso raro.
        """
        try:
            return alvo.relative_to(self._raiz_canonica).as_posix()
        except ValueError:
            pass
        try:
            return alvo.resolve().relative_to(self._raiz_canonica).as_posix()
        except (OSError, ValueError):
            return Path(alvo).name

    def motivo_de_recusa(self, alvo: Path, *, diretorio: bool | None = None) -> str | None:
        """Motivo pelo qual este caminho não pode ser lido, ou `None`.

        Registra a recusa na contagem: consultar a política **é** o momento em
        que a recusa acontece, e separar consulta de registro só cria o caminho
        em que alguém consulta sem contar.
        """
        relativo = self.relativo(alvo)
        motivo = motivo_da_denylist(relativo)
        if motivo is None:
            e_diretorio = alvo.is_dir() if diretorio is None else diretorio
            if padrao := self.llmignore.padrao_que_ignora(relativo, diretorio=e_diretorio):
                motivo = f"{NOME_DO_LLMIGNORE}: {padrao}"
        if motivo is None:
            return None
        self.contagem.recusar(str(alvo), motivo)
        return motivo

    def recusa_nome_de_diretorio(self, nome: str) -> bool:
        """Atalho para a varredura: este nome de diretório é de segredo?

        Existe para `os.walk` poder podar a árvore **antes** de descer nela. Sem
        ele, `.ssh/` seria percorrido inteiro e cada arquivo recusado um por um —
        o mesmo veredito, com o disco lido à toa.
        """
        return nome.lower() in DIRETORIOS_DE_SEGREDO

    def redigir(self, texto: str) -> tuple[str, int]:
        """Texto redigido e quantos trechos saíram; acumula na contagem."""
        redacao = redigir_texto(texto)
        if redacao.trechos:
            self.contagem.redigir(redacao.por_motivo)
        return redacao.texto, redacao.trechos

    def resumo(self) -> ResumoDaPolitica:
        return self.contagem.resumo()
