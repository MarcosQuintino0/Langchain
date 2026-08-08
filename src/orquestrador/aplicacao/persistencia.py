"""O que sai para o disco, e de quem é cada arquivo que sai.

Reúne as quatro escritas do pipeline que não são "uma tentativa do loop": a
superfície do projeto (Bloco 0), os schemas de entrada do mapeador, a publicação
no projeto do consumidor e o registro das divergências de schema.

Ela é dona de `divergencias` — a lista que decide entre `APROVADO` e
`REQUER_REVISAO` no fim do recurso. Enquanto morava no `Pipeline`, era o único
campo mutável não-resultado dele, e existia só porque a gravação dos schemas e a
publicação estavam no mesmo objeto.

**O disco do consumidor é tocado uma vez por recurso**, em `publicar`, depois que
os dois gates aprovaram. Tudo o mais escreve no diretório da execução ou no
staging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from orquestrador.analise_estatica.extrator_de_superficie import extrair
from orquestrador.config import Config
from orquestrador.dominio.artefatos import SaidaMapeador
from orquestrador.dominio.propriedade import (
    Classificacao,
    DivergenciaDeSchema,
    EntradaDoDiario,
)
from orquestrador.dominio.recurso import Recurso
from orquestrador.dominio.superficie import SuperficieDoProjeto
from orquestrador.ferramentas.arquivos import sob_a_raiz
from orquestrador.ferramentas.publicacao import AreaDeStaging, Diario, remover_criados
from orquestrador.observabilidade.eventos import TipoDeEvento
from orquestrador.observabilidade.registro import Registro

# Guarda contra schema recursivo, no mesmo espírito do PROFUNDIDADE_MAXIMA de
# `campos/schema.mjs`.
_PROFUNDIDADE_DE_CAMPOS = 4


def nomes_de_campos(esquema: Any, *, profundidade: int = _PROFUNDIDADE_DE_CAMPOS) -> set[str]:
    """Todo nome que aparece sob algum `properties` do schema, até uma profundidade.

    Varredura deliberadamente rasa e tolerante: ela serve para responder "este nome
    não aparece em lugar nenhum do arquivo", não para decidir qual nó é a entidade.
    Quem decide isso é `campos/schema.mjs`, e reimplementar a heurística aqui criaria
    uma segunda fonte de verdade para o denominador da cobertura.
    """
    if profundidade <= 0 or not isinstance(esquema, dict):
        return set()
    # `Any` é a anotação certa na entrada: o argumento é um nó qualquer de um JSON
    # Schema escrito por um LLM, e a função existe justamente para atravessá-lo sem
    # exigir forma. O que ela devolve, porém, é `set[str]` — o `Any` para aqui.
    no = cast(dict[str, Any], esquema)
    nomes: set[str] = set()
    propriedades = no.get("properties")
    if isinstance(propriedades, dict):
        for nome, subesquema in cast(dict[str, Any], propriedades).items():
            nomes.add(str(nome))
            nomes |= nomes_de_campos(subesquema, profundidade=profundidade - 1)
    if (itens := no.get("items")) is not None:
        nomes |= nomes_de_campos(itens, profundidade=profundidade - 1)
    return nomes


class PersistenciaDeArtefatos:
    """As escritas do pipeline, e a memória das divergências do recurso em curso."""

    def __init__(
        self, config: Config, registro: Registro, *, dir_execucao: Path, diario: Diario
    ) -> None:
        self.config = config
        self.registro = registro
        self.dir_execucao = dir_execucao
        self.diario = diario
        # Zerada a cada recurso por `iniciar_recurso`: é o princípio 3 aplicado à
        # própria persistência — nada de um recurso pode decidir o desfecho do
        # seguinte.
        self.divergencias: list[DivergenciaDeSchema] = []

    def iniciar_recurso(self) -> None:
        self.divergencias = []

    def extrair_superficie(self) -> SuperficieDoProjeto:
        """Lê os módulos compartilhados do projeto. Determinístico, zero token.

        Falha aqui é pré-condição do projeto de testes, não do recurso: ela
        interrompe a execução antes de qualquer chamada de modelo, porque o
        executor não tem como adivinhar nomes de export que ninguém contou a ele —
        e o delta do Gate B ("import não resolve") não é acionável.

        Devolve a superfície em vez de guardá-la: ela é do projeto, não do recurso,
        e quem a leva à instrução fixa do executor é o pipeline. Dois donos para o
        mesmo valor é como ele fica desatualizado num deles.
        """
        superficie = extrair(self.config)
        destino = self.dir_execucao / "artefatos" / "superficie-do-projeto.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(superficie.para_json(), encoding="utf-8", newline="\n")

        self.registro.evento(
            TipoDeEvento.SUPERFICIE,
            raiz=superficie.raiz,
            modulos=[
                {
                    "caminho": modulo.caminho,
                    "import_do_recurso": modulo.import_do_recurso,
                    "import_do_support": modulo.import_do_support,
                    "exports": [exportado.nome for exportado in modulo.exports],
                }
                for modulo in superficie.modulos
            ],
            artefato=destino,
        )
        self.registro.ok(
            f"superfície do projeto: {len(superficie.modulos)} módulo(s), "
            f"{superficie.total_de_exports} export(s) em {superficie.raiz}"
        )
        return superficie

    def persistir_schemas(
        self, recurso: Recurso, saida: SaidaMapeador, area: AreaDeStaging
    ) -> list[Path]:
        """Grava os schemas do mapeador sem passar por cima do que é do cliente.

        No desenho da skill o schema de entrada é artefato **pré-existente** do
        projeto consumidor — o AJV valida respostas com ele e ele quebra os testes se
        estiver errado (`scripts/cobertura/campos/schema.mjs`). É dessa independência
        que vem a autoridade dele como denominador: ele não foi escrito por quem vai
        ser medido. Sobrescrever em silêncio quebraria suíte alheia e trocaria uma
        régua independente pela régua do próprio modelo.

        Daí a regra: arquivo que já era do consumidor é copiado para o staging e
        preservado; o resto é escrito à vontade a cada tentativa, senão o loop de
        reparo do Gate A nunca convergiria sobre o schema.

        Quem responde "já era do consumidor?" é a área de staging, que fotografou o
        destino no início do recurso. O conjunto de schemas gravados que esta classe
        mantinha respondia à mesma pergunta com estado próprio — e estado próprio
        para uma pergunta sobre o disco erra na primeira vez que o disco muda por
        fora.
        """
        escritos: list[Path] = []
        preservados: list[Path] = []

        for arquivo in saida.schemas:
            alvo = recurso.caminho_schemas / arquivo.caminho
            if area.ja_era_do_consumidor(alvo):
                preservados.append(alvo)
                if divergencia := self._divergencia(recurso, alvo, arquivo.conteudo):
                    self.divergencias.append(divergencia)
                area.preservar_schema(arquivo.caminho)
                continue
            escritos.append(area.escrever_schema(arquivo.caminho, arquivo.conteudo))

        if preservados:
            self.registro.evento(
                TipoDeEvento.SCHEMAS_PRESERVADOS,
                recurso=recurso.nome,
                arquivos=[str(caminho) for caminho in preservados],
            )
        return escritos

    def _divergencia(
        self, recurso: Recurso, alvo: Path, conteudo_emitido: str
    ) -> DivergenciaDeSchema | None:
        """O que o mapeador achou no backend e o schema preservado não declara.

        Não reprova: a autoridade sobre o arquivo é do Gate A, e o arquivo é do
        consumidor. Mas também não pode passar como aviso e o recurso terminar
        aprovado — campo que existe no backend e não está no schema sai do
        denominador sem deixar rastro, e a cobertura sobe porque a régua encolheu.
        O desfecho é `REQUER_REVISAO`; ver `_rodar_recurso`.
        """
        try:
            existente = nomes_de_campos(json.loads(alvo.read_text(encoding="utf-8")))
            emitido = nomes_de_campos(json.loads(conteudo_emitido))
        except (OSError, ValueError):
            # Schema ilegível é caso do gate, que reprova com a mensagem certa. Aqui
            # só desistimos da comparação.
            return None
        ausentes = sorted(emitido - existente)
        if not ausentes:
            return None
        return DivergenciaDeSchema(recurso=recurso.nome, arquivo=alvo, campos_ausentes=ausentes)

    def publicar(self, recurso: Recurso, area: AreaDeStaging) -> list[EntradaDoDiario]:
        """Leva o staging aprovado para o projeto do consumidor e registra o diário.

        Devolve as entradas em vez de escrever no `ResultadoDoRecurso`: aquele tipo
        é do `pipeline.py`, e recebê-lo aqui faria a persistência importar quem a
        chama. O que o pipeline precisa saber — publicou, e o quê — está no retorno.
        Se `area.publicar()` levantar, nada foi publicado e nada é devolvido, que é
        exatamente o que a versão anterior registrava.
        """
        anteriores = [
            entrada
            for entrada in self.diario.carregar().entradas
            if entrada.recurso == recurso.nome
            and entrada.classificacao is Classificacao.CRIADO
            and sob_a_raiz(entrada.destino, recurso.caminho_testes)
        ]

        entradas = area.publicar()

        publicados = {entrada.destino for entrada in entradas}
        obsoletos = [entrada for entrada in anteriores if entrada.destino not in publicados]
        removidos, recusados = remover_criados(obsoletos, sob=recurso.caminho_testes)

        self.diario.registrar(entradas, esquecer=set(removidos))
        self.registro.evento(
            TipoDeEvento.PUBLICACAO,
            recurso=recurso.nome,
            arquivos=[
                {
                    "destino": str(entrada.destino),
                    "classificacao": entrada.classificacao.value,
                    "hash_anterior": entrada.hash_anterior,
                    "hash_novo": entrada.hash_novo,
                }
                for entrada in entradas
            ],
            obsoletos_removidos=[str(caminho) for caminho in removidos],
            obsoletos_mantidos=[str(caminho) for caminho in recusados],
        )
        criados = sum(1 for e in entradas if e.classificacao is Classificacao.CRIADO)
        modificados = sum(1 for e in entradas if e.classificacao is Classificacao.MODIFICADO)
        self.registro.ok(
            f"{recurso.nome} publicado: {criados} arquivo(s) criado(s), "
            f"{modificados} modificado(s), {len(removidos)} obsoleto(s) removido(s)"
        )
        if recusados:
            # Spec que nasceu conosco e alguém editou depois. Não é nosso para
            # apagar, e o silêncio faria parecer que a limpeza foi completa.
            self.registro.aviso(
                f"{len(recusados)} arquivo(s) que criamos em execução anterior "
                "mudaram desde então e NÃO foram removidos: "
                + ", ".join(str(caminho) for caminho in recusados)
            )
        for divergencia in self.divergencias:
            self.registro.aviso(divergencia.render())
        if self.divergencias:
            destino = self.dir_execucao / "artefatos" / recurso.nome / "divergencias-de-schema.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    [d.model_dump(mode="json") for d in self.divergencias],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self.registro.evento(
                TipoDeEvento.SCHEMAS_DIVERGENTES,
                recurso=recurso.nome,
                artefato=destino,
                divergencias=[d.model_dump(mode="json") for d in self.divergencias],
            )
        return entradas
