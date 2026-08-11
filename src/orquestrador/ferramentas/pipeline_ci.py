"""Escreve a pipeline de CI da suíte gerada — quando, e só quando, cabe.

Três regras, e todas são sobre não mexer no que é do cliente:

1. **Sem CI no projeto, nada é gerado.** Quem não tem integração contínua não
   pediu uma, e depositar um `.yml` num repositório que nunca rodou nada é
   palpite disfarçado de entrega.
2. **Arquivo existente nunca é sobrescrito.** Nem o nosso de uma execução
   anterior: o dono pode tê-lo ajustado, e a versão dele vale mais que a nossa.
3. **Arquivo de configuração alheio nunca é editado.** Onde a plataforma não
   descobre arquivo novo sozinha, a sugestão fica no diretório da execução e o
   relatório diz a linha exata a acrescentar. Editar o `.gitlab-ci.yml` de quem
   nos contratou seria a mesma classe de erro que sobrescrever um spec.

O template é constante de módulo, e não arquivo em `assets/`, pelo mesmo motivo
do `config.toml` de `cli/init.py`: o valor dele está nos comentários, e conteúdo
comentado sobrevive melhor ao lado do código que o escreve do que num arquivo que
alguém esquece de empacotar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orquestrador.analise_estatica.ci_do_projeto import CiDoProjeto, Geracao, Plataforma

# `{{ secrets.* }}` é sintaxe do GitHub e fica literal: por isso a substituição é
# por marcador nomeado, e não por `str.format`, que engasgaria nas chaves duplas.
MARCADOR_DE_SPECS = "__SPECS__"

MODELO_GITHUB = f"""# Testes de API — gerado pelo orquestrador.
#
# Este arquivo é um ponto de partida deliberadamente simples: ele roda a suíte
# gerada a cada push e a cada pull request, e falha o build quando um teste falha.
# Ajuste à vontade — nós não o sobrescrevemos depois de criado.
#
# ANTES DE VALER, declare os segredos em Settings > Secrets and variables:
#   CYPRESS_API_URL   endereço da API de teste
# O Cypress lê sozinho toda variável prefixada com CYPRESS_.
name: Testes de API

on: [push, pull_request]

jobs:
  testes-de-api:
    runs-on: ubuntu-latest
    # Teto de tempo: suíte de API que passa de 20 minutos tem defeito de espera,
    # não de tamanho — e job pendurado consome minuto de quem paga.
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
      - run: npm ci
      - run: npx cypress run --spec "{MARCADOR_DE_SPECS}"
        env:
          CYPRESS_apiUrl: ${{{{ secrets.CYPRESS_API_URL }}}}
      - if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: cypress-evidencias
          path: |
            cypress/screenshots
            cypress/videos
          retention-days: 7
"""


@dataclass(frozen=True)
class PipelineGerada:
    """O que aconteceu com uma plataforma detectada."""

    plataforma: Plataforma
    escrita: Path | None = None
    inclusao: str = ""
    motivo: str = ""

    def render(self) -> str:
        if self.escrita is None:
            return f"{self.plataforma.nome}: {self.motivo}"
        linha = f"{self.plataforma.nome}: {self.escrita}"
        if self.inclusao:
            linha += f"\n    acrescente ao arquivo de CI do projeto:\n      {self.inclusao}"
        return linha


def gerar(
    ci: CiDoProjeto,
    *,
    specs: list[str],
    dir_execucao: Path,
) -> list[PipelineGerada]:
    """Uma entrada por plataforma detectada, tenha sido escrita ou não.

    `specs` são os padrões de caminho das suítes publicadas, relativos à raiz do
    projeto de testes. Lista vazia não gera nada: pipeline que não aponta para
    teste nenhum passa em verde sem rodar coisa alguma, que é o pior resultado
    possível — verde mentindo.
    """
    if not ci.tem_ci or ci.raiz is None or not specs:
        return []

    conteudo = MODELO_GITHUB.replace(MARCADOR_DE_SPECS, ",".join(sorted(specs)))
    resultados: list[PipelineGerada] = []
    for plataforma in ci.plataformas:
        if plataforma.geracao is Geracao.NAO_GERA:
            resultados.append(PipelineGerada(plataforma=plataforma, motivo=plataforma.motivo))
            continue

        if plataforma.geracao is Geracao.NO_PROJETO:
            alvo = ci.raiz / plataforma.destino
            if alvo.exists():
                resultados.append(
                    PipelineGerada(
                        plataforma=plataforma,
                        motivo=f"{alvo} já existe e não foi tocado",
                    )
                )
                continue
        else:
            # Sugestão: fica na NOSSA execução. O projeto do cliente não é tocado
            # porque a plataforma exigiria editar o arquivo de configuração dele.
            alvo = dir_execucao / plataforma.destino

        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8", newline="\n")
        resultados.append(
            PipelineGerada(
                plataforma=plataforma,
                escrita=alvo,
                inclusao=plataforma.inclusao,
            )
        )
    return resultados


def escritas_no_projeto(resultados: list[PipelineGerada]) -> list[Path]:
    """As que caíram dentro do projeto do consumidor — as que o diário registra."""
    return [
        resultado.escrita
        for resultado in resultados
        if resultado.escrita is not None and resultado.plataforma.geracao is Geracao.NO_PROJETO
    ]
