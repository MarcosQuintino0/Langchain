"""Escrita transacional no projeto do consumidor: staging, diário e publicação.

Fronteira
---------
Este módulo é o **único** lugar do orquestrador que grava dentro do projeto de
testes do cliente. O executor e o pipeline escrevem numa área de staging desta
execução e nunca no destino final; a passagem de um para o outro acontece aqui,
uma vez por recurso, depois que os dois gates aprovaram.

O que ele deliberadamente não faz: decidir *se* o artefato merece ser publicado.
Isso é do gate. Aqui a pergunta é outra — como escrever sem poder destruir o que
já era do consumidor, e como desfazer quando a escrita falha no meio.

Por que o staging do recurso mora **dentro** do projeto do cliente
------------------------------------------------------------------
A escolha óbvia seria pôr tudo em `.execucoes/<ts>/staging/`, fora do projeto. Ela
não funciona, e o motivo não é estético:

* os specs importam os módulos compartilhados por caminho **relativo**
  (`../../../../support/api/client.js`), e o `validar-suite-gerada.mjs` resolve
  esses imports a partir do arquivo. Num staging de outra profundidade — ou de
  outra árvore — todo spec reprova com `QAAPI-004` ("import relativo nao
  resolvido"), e o loop de reparo gasta as tentativas tentando consertar um import
  que está correto;
* o `cobertura/handlers.mjs` **sobe** do diretório do recurso procurando
  `<raiz>/.agents/config/qa-api/handlers.json`, e não tem flag para receber o
  caminho pronto. Fora do projeto ele não acha o registro, e a coerência de
  profundidade (`QAAPI-032`) passa a ser conferida contra um registro inexistente.

Por isso o staging do recurso é um **irmão** do diretório real, no mesmo nível:
`<dir_recursos>/.qa-staging-<execucao>-<recurso>`. Mesma profundidade, mesma raiz
de projeto — os dois mecanismos acima continuam resolvendo exatamente como
resolveriam no destino, que é o ponto de validar o staging em vez do destino.

O ponto inicial é deliberado: o `specPattern` padrão do Cypress
(`cypress/e2e/**/*.cy.js`) não casa componente que começa com ponto, então um
staging esquecido por uma execução que morreu não entra na suíte do consumidor.

Os schemas, esses, moram fora do projeto (`<execucao>/staging/<recurso>/schemas`)
— podem, porque as duas ferramentas que os leem aceitam o diretório pronto:
`validar-suite-gerada.mjs --schemas=<dir>` e `qa-cobertura.mjs --schemas <dir>`.

Confinamento é de `ferramentas/arquivos.py`, o dono da regra; aqui só se chama
`confinar`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from orquestrador.contratos import (
    Classificacao,
    DiarioDePropriedade,
    EntradaDoDiario,
)
from orquestrador.excecoes import FalhaDePublicacao
from orquestrador.ferramentas.arquivos import confinar, sob_a_raiz

# O ponto inicial esconde o staging do `specPattern` do Cypress; o resto do nome
# existe para que dois staging da mesma execução (recursos diferentes) e de
# execuções diferentes nunca colidam no mesmo diretório-pai.
PREFIXO_DE_STAGING = ".qa-staging-"

NOME_DO_DIARIO = "diario-de-propriedade.json"

_TAMANHO_DO_BLOCO = 1 << 20


def hash_do_texto(texto: str) -> str:
    """sha256 do texto como ele será gravado (UTF-8, sem BOM)."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_do_arquivo(caminho: Path) -> str | None:
    """sha256 do conteúdo, ou `None` quando não existe arquivo naquele caminho.

    `None` não é hash de arquivo vazio: é a ausência dele. É essa diferença que
    separa "criamos este arquivo" de "reescrevemos o do consumidor", e é sobre ela
    que a remoção automática decide o que pode apagar.
    """
    if not caminho.is_file():
        return None
    digestor = hashlib.sha256()
    with caminho.open("rb") as fluxo:
        while bloco := fluxo.read(_TAMANHO_DO_BLOCO):
            digestor.update(bloco)
    return digestor.hexdigest()


@dataclass(frozen=True)
class _Pendente:
    """Um arquivo do staging e o destino que ele ocupará (ou de onde veio)."""

    origem: Path
    destino: Path
    hash_anterior: str | None
    publicavel: bool


@dataclass
class AreaDeStaging:
    """Onde as tentativas de um recurso escrevem antes de existir veredito.

    Um objeto por recurso e por execução. Ele guarda, para cada destino tocado, o
    hash que aquele caminho tinha **no início do recurso** — não no momento da
    escrita. É o que torna a decisão "isto era do cliente?" estável entre as
    tentativas do loop: a primeira tentativa não pode mudar a resposta da segunda.
    """

    recurso: str
    execucao: str
    dir_recurso: Path
    destino_recurso: Path
    dir_schemas: Path
    destino_schemas: Path
    dir_reserva: Path
    # Destinos que uma execução anterior registrou como criados por nós. Sem isto,
    # regenerar um spec nosso o rebaixaria a "modificado" e ele viraria intocável.
    criados_antes: frozenset[Path] = frozenset()

    _inicial: dict[Path, str | None] = field(default_factory=dict[Path, str | None], init=False)
    _pendentes: dict[Path, _Pendente] = field(default_factory=dict[Path, _Pendente], init=False)
    publicada: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for diretorio in (self.dir_recurso, self.dir_schemas):
            diretorio.mkdir(parents=True, exist_ok=True)

    # -- consulta -----------------------------------------------------------

    def hash_inicial(self, destino: Path) -> str | None:
        """Hash do destino como ele estava quando este recurso começou."""
        if destino not in self._inicial:
            self._inicial[destino] = hash_do_arquivo(destino)
        return self._inicial[destino]

    def ja_era_do_consumidor(self, destino: Path) -> bool:
        """O arquivo existia antes desta execução e não fomos nós que o criamos."""
        return self.hash_inicial(destino) is not None and destino not in self.criados_antes

    @property
    def arquivos(self) -> list[Path]:
        """O que está no staging agora, em ordem estável."""
        return sorted(
            pendente.origem for pendente in self._pendentes.values() if pendente.publicavel
        )

    # -- escrita ------------------------------------------------------------

    def escrever(self, relativo: str, conteudo: str) -> Path:
        """Grava um arquivo do recurso (manifesto, spec, `_support/`) no staging."""
        return self._gravar(self.dir_recurso, self.destino_recurso, relativo, conteudo)

    def escrever_schema(self, relativo: str, conteudo: str) -> Path:
        """Grava um schema de entrada no staging de schemas."""
        return self._gravar(self.dir_schemas, self.destino_schemas, relativo, conteudo)

    def preservar_schema(self, relativo: str) -> Path:
        """Copia para o staging o schema que já era do consumidor.

        A cópia é necessária mesmo sem intenção de publicar: o staging é o que os
        gates enxergam, e o denominador da cobertura por campo é justamente esse
        arquivo. Sem a cópia, o schema do cliente sumiria do denominador e a
        cobertura subiria porque a régua encolheu.
        """
        origem = confinar(self.dir_schemas, relativo)
        destino = confinar(self.destino_schemas, relativo)
        origem.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destino, origem)
        self._anotar(origem, destino, publicavel=False)
        return origem

    def _gravar(self, raiz: Path, destino_raiz: Path, relativo: str, conteudo: str) -> Path:
        origem = confinar(raiz, relativo)
        destino = confinar(destino_raiz, relativo)
        origem.parent.mkdir(parents=True, exist_ok=True)
        origem.write_text(conteudo, encoding="utf-8", newline="\n")
        self._anotar(origem, destino, publicavel=True)
        return origem

    def _anotar(self, origem: Path, destino: Path, *, publicavel: bool) -> None:
        self._pendentes[destino] = _Pendente(
            origem=origem,
            destino=destino,
            hash_anterior=self.hash_inicial(destino),
            publicavel=publicavel,
        )

    # -- publicação ---------------------------------------------------------

    def publicar(self) -> list[EntradaDoDiario]:
        """Move o staging para o destino final, tudo ou nada.

        A escrita é em duas fases porque a substituição é o único passo que não dá
        para ensaiar: primeiro cada arquivo é copiado para um temporário **ao lado
        do destino** (mesmo volume, senão `os.replace` deixa de ser atômico), e só
        depois vêm as substituições. Se uma delas falhar, as anteriores voltam da
        reserva e os temporários somem — o projeto do consumidor fica byte a byte
        como estava.

        Antes de tudo, o conflito: se algum destino mudou desde o início do
        recurso, ninguém publica nada. Publicar por cima seria apagar a edição de
        outra pessoa com um artefato que foi validado contra outro estado do
        projeto.
        """
        pendentes = [item for item in self._pendentes.values() if item.publicavel]
        conflitos = [
            item.destino
            for item in pendentes
            if hash_do_arquivo(item.destino) != item.hash_anterior
        ]
        if conflitos:
            raise FalhaDePublicacao(
                f"o recurso {self.recurso!r} não foi publicado: "
                f"{len(conflitos)} arquivo(s) de destino mudaram durante a execução "
                "(alguém editou, ou outra ferramenta escreveu). O projeto continua "
                "como estava e os artefatos ficaram no staging:\n"
                + "\n".join(f"  - {caminho}" for caminho in conflitos)
                + f"\n  staging: {self.dir_recurso}",
                arquivos=self.arquivos,
            )

        temporarios: list[tuple[_Pendente, Path]] = []
        substituidos: list[tuple[_Pendente, Path | None]] = []
        try:
            for item in pendentes:
                item.destino.parent.mkdir(parents=True, exist_ok=True)
                temporario = item.destino.with_name(
                    f"{PREFIXO_DE_STAGING}{os.getpid()}.{item.destino.name}"
                )
                shutil.copyfile(item.origem, temporario)
                temporarios.append((item, temporario))
            for ordem, (item, temporario) in enumerate(temporarios):
                reserva = self._reservar(item.destino, ordem)
                temporario.replace(item.destino)
                substituidos.append((item, reserva))
        except OSError as erro:
            self._reverter(substituidos)
            for _item, temporario in temporarios:
                Path(temporario).unlink(missing_ok=True)
            raise FalhaDePublicacao(
                f"a publicação do recurso {self.recurso!r} falhou no meio e foi "
                f"desfeita — o projeto voltou ao estado anterior. Causa: {erro}\n"
                f"  staging: {self.dir_recurso}",
                arquivos=self.arquivos,
            ) from erro

        self.publicada = True
        return self.diario()

    def diario(self) -> list[EntradaDoDiario]:
        """Uma entrada por arquivo tocado, publicado ou preservado."""
        return [
            self._entrada(item)
            for item in sorted(self._pendentes.values(), key=lambda item: item.destino)
        ]

    def _entrada(self, item: _Pendente) -> EntradaDoDiario:
        if not item.publicavel:
            classificacao = Classificacao.PREEXISTENTE
            hash_novo = item.hash_anterior
        else:
            hash_novo = hash_do_arquivo(item.origem)
            nosso = item.hash_anterior is None or item.destino in self.criados_antes
            classificacao = Classificacao.CRIADO if nosso else Classificacao.MODIFICADO
        return EntradaDoDiario(
            destino=item.destino,
            classificacao=classificacao,
            hash_anterior=item.hash_anterior,
            hash_novo=hash_novo,
            recurso=self.recurso,
            execucao=self.execucao,
        )

    def _reservar(self, destino: Path, ordem: int) -> Path | None:
        """Guarda o conteúdo atual do destino; `None` quando não havia arquivo.

        A reserva é numerada pela ordem da publicação, e não espelha a árvore do
        destino: dois arquivos de mesmo nome vindos de raízes diferentes (o
        diretório do recurso e o de schemas) colidiriam numa cópia por caminho
        relativo, e a cópia perdida seria justamente a que o rollback precisa.
        """
        if not destino.is_file():
            return None
        reserva = self.dir_reserva / f"{ordem:03d}-{destino.name}"
        reserva.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destino, reserva)
        return reserva

    def _reverter(self, substituidos: list[tuple[_Pendente, Path | None]]) -> None:
        """Desfaz as substituições já feitas, da última para a primeira."""
        for item, reserva in reversed(substituidos):
            if reserva is None:
                # Não havia arquivo antes: desfazer é sumir, não restaurar vazio.
                item.destino.unlink(missing_ok=True)
            else:
                shutil.copyfile(reserva, item.destino)

    # -- limpeza ------------------------------------------------------------

    def descartar(self) -> None:
        """Remove o staging **dentro do projeto do consumidor**.

        Só ele: o staging de schemas e a reserva ficam no diretório da execução,
        que é nosso e é o que se inspeciona depois. O que não pode sobrar é
        diretório nosso dentro do projeto de quem nos contratou.
        """
        shutil.rmtree(self.dir_recurso, ignore_errors=True)


def criar_area(
    *,
    recurso: str,
    destino_recurso: Path,
    destino_schemas: Path,
    dir_execucao: Path,
    criados_antes: frozenset[Path] = frozenset(),
) -> AreaDeStaging:
    """Monta a área de staging de um recurso a partir do diretório da execução.

    O identificador vem do nome do diretório da execução (`<AAAAMMDD-HHMMSS>-<pid>`),
    que já é único por execução — inventar outro criaria uma segunda numeração para
    a mesma coisa.
    """
    execucao = dir_execucao.name
    return AreaDeStaging(
        recurso=recurso,
        execucao=execucao,
        dir_recurso=destino_recurso.parent / f"{PREFIXO_DE_STAGING}{execucao}-{recurso}",
        destino_recurso=destino_recurso,
        dir_schemas=dir_execucao / "staging" / recurso / "schemas",
        destino_schemas=destino_schemas,
        dir_reserva=dir_execucao / "reserva" / recurso,
        criados_antes=criados_antes,
    )


# ---------------------------------------------------------------------------
# Diário acumulado entre execuções
# ---------------------------------------------------------------------------


class Diario:
    """Persistência do diário de propriedade, fora do projeto do consumidor.

    Mora na raiz do diretório de saída (`.execucoes/`), e não dentro do projeto de
    testes: é registro **nosso** sobre o que fizemos, não artefato do cliente, e
    escrever um arquivo de controle dentro do repositório dele para poder apagar
    arquivos dele é exatamente a inversão que este módulo existe para evitar.

    Diário ilegível ou ausente vira diário vazio, nunca exceção: a única coisa que
    ele autoriza é remoção, e sem ele a resposta certa é não remover nada.
    """

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho

    def carregar(self) -> DiarioDePropriedade:
        try:
            bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
            return DiarioDePropriedade.model_validate(bruto)
        except (OSError, ValueError):
            return DiarioDePropriedade()

    def registrar(
        self, entradas: list[EntradaDoDiario], *, esquecer: set[Path] | None = None
    ) -> DiarioDePropriedade:
        """Grava as novas entradas e poda as que apontam para arquivo inexistente.

        A poda mantém o arquivo finito — sem ela, cada sandbox de `--dry-run` deixa
        um punhado de caminhos que nunca mais casam com nada — e não perde
        informação útil: entrada cujo destino sumiu não autoriza remoção nenhuma.

        Se o destino estiver temporariamente inacessível (unidade de rede fora do
        ar), a entrada cai e o arquivo deixa de ser reconhecido como nosso. É a
        direção conservadora: o pior efeito é parar de remover.
        """
        novos = {entrada.destino for entrada in entradas}
        sumidos = {
            entrada.destino
            for entrada in self.carregar().entradas
            if entrada.destino not in novos and hash_do_arquivo(entrada.destino) is None
        }
        atualizado = self.carregar().substituir(entradas, esquecer=sumidos | (esquecer or set()))
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho.write_text(atualizado.para_json(), encoding="utf-8", newline="\n")
        return atualizado

    def criados(self) -> frozenset[Path]:
        """Destinos que alguma execução nossa criou — e que, portanto, são nossos."""
        return frozenset(
            entrada.destino
            for entrada in self.carregar().entradas
            if entrada.classificacao is Classificacao.CRIADO
        )


def remover_criados(
    entradas: list[EntradaDoDiario], *, sob: Path | None = None
) -> tuple[list[Path], list[Path]]:
    """Apaga o que **nós** criamos e ninguém tocou depois.

    Devolve `(removidos, recusados)`. Recusa em três casos, e cada um deles é um
    arquivo que não é nosso para apagar: classificação diferente de `criado`, hash
    atual diferente do registrado (alguém editou depois de nós) e caminho fora de
    `sob`.

    A checagem de hash é o que separa esta função da que existia antes, que fazia
    `unlink()` na lista inteira: um spec que criamos e o desenvolvedor melhorou à
    mão deixa de ser descartável no instante em que ele o salva.
    """
    removidos: list[Path] = []
    recusados: list[Path] = []
    for entrada in entradas:
        destino = entrada.destino
        atual = hash_do_arquivo(destino)
        fora = sob is not None and not sob_a_raiz(destino, sob)
        if entrada.classificacao is not Classificacao.CRIADO or fora or atual != entrada.hash_novo:
            if atual is not None:
                recusados.append(destino)
            continue
        destino.unlink(missing_ok=True)
        removidos.append(destino)
    return removidos, recusados
