"""O diff grafo × manifesto: o que existe no backend contra o que o gabarito planeja.

Este é o teste do item que fecha o defeito de origem do projeto — o modelo amostra
em vez de enumerar, e ninguém percebe porque o gabarito é escrito pelo mesmo modelo
que depois vai satisfazê-lo. Daí a insistência nos casos negativos: um extrator que
só é exercitado com a fixture que ele acerta prova exatamente nada.

Três desfechos são verificados, e nenhum deles é opcional:

* gabarito que cobre o controlador inteiro **aprova**;
* gabarito que cobre parte dele **reprova** com `QAORQ-002`;
* backend que nenhum adaptador sabe ler **não termina como aprovado** — vira
  `ERRO_DA_FERRAMENTA`, com o diagnóstico na mão de quem opera.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orquestrador.analise_estatica.extrator_de_endpoints import (
    MATRIZ_DE_SUPORTE,
    chave_de_endpoint,
    extrair,
)
from orquestrador.analise_estatica.rotas_java_spring import extrair_controladores
from orquestrador.contratos import (
    Inventario,
    Manifesto,
    Recurso,
    RotaDinamica,
    VereditoDeGate,
)
from orquestrador.excecoes import GrafoNaoPreparado
from orquestrador.gates.gate_a import diff_grafo_manifesto

# ---------------------------------------------------------------------------
# Fontes de fixture
# ---------------------------------------------------------------------------

PRODUTOS = """
package com.exemplo.api;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/products")
public class ProductController {
    public ProductController(ProductService service) { this.service = service; }

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasAuthority('PERM_PRODUCT_WRITE')")
    public ResponseEntity<ProductDtos.Response> create(@Valid @RequestBody ProductDtos.Create req) {
        return null;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public PageResponse<ProductDtos.Response> list(@RequestParam(required = false) String search) {
        return null;
    }

    @GetMapping(value = "/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ProductDtos.Response> get(@PathVariable UUID id) { return null; }

    @PatchMapping(value = "/{id}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ProductDtos.Response> patch(@PathVariable UUID id) { return null; }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable UUID id) { return null; }

    private ProductDtos.Response response(ProductService.ProductView view) { return null; }
}
"""

CINCO_DE_PRODUTOS = (
    "POST /api/v1/products",
    "GET /api/v1/products",
    "GET /api/v1/products/{id}",
    "PATCH /api/v1/products/{id}",
    "DELETE /api/v1/products/{id}",
)


def manifesto_de(*endpoints: str, recurso: str = "products") -> Manifesto:
    """Gabarito mínimo que o contrato aceita: só os endpoints importam aqui."""
    return Manifesto.model_validate(
        {"recurso": recurso, "endpoints": [{"endpoint": item} for item in endpoints]}
    )


def recurso_de(nome: str = "products") -> Recurso:
    return Recurso(nome=nome, caminho_testes=Path("cypress/e2e/apis") / nome)


@pytest.fixture
def backend_spring(tmp_path: Path) -> tuple[Path, Path]:
    """Backend Java + `graph.json` no formato do Graphify real."""
    backend = tmp_path / "backend"
    fonte = backend / "src/main/java/com/exemplo/api/ProductController.java"
    fonte.parent.mkdir(parents=True)
    fonte.write_text(PRODUTOS, encoding="utf-8")

    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "label": "ProductController",
                        "source_file": "src/main/java/com/exemplo/api/ProductController.java",
                        "source_location": "L8",
                    },
                    {"label": "pom.xml", "source_file": "pom.xml"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return graph, backend


# ---------------------------------------------------------------------------
# O parser de anotação Spring
# ---------------------------------------------------------------------------


def test_o_prefixo_da_classe_entra_em_toda_rota():
    controladores = extrair_controladores(PRODUTOS)
    assert [c.classe for c in controladores] == ["ProductController"]
    assert sorted(f"{r.metodo} {r.rota}" for r in controladores[0].rotas) == sorted(
        CINCO_DE_PRODUTOS
    )


def test_metodo_sem_anotacao_de_rota_nao_vira_endpoint():
    """`response()` é privado e sem anotação: não pode aparecer como endpoint."""
    rotas = extrair_controladores(PRODUTOS)[0].rotas
    assert all(".response" not in rota.handler for rota in rotas)


def test_classe_sem_anotacao_de_controlador_e_ignorada():
    fonte = """
    @Service
    public class ProductService {
        @GetMapping("/interno") public String naoEhRota() { return ""; }
    }
    """
    assert extrair_controladores(fonte) == []


def test_request_mapping_com_method_resolve_o_verbo():
    fonte = """
    @RestController
    @RequestMapping("/base")
    class C {
        @RequestMapping(value = "/x", method = RequestMethod.PUT)
        void put() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert [(r.metodo, r.rota) for r in controlador.rotas] == [("PUT", "/base/x")]


def test_request_mapping_sem_method_vira_incerteza_e_nao_endpoint():
    """Sem `method=` a anotação casa com todos os verbos: não há um endpoint a nomear."""
    fonte = """
    @RestController
    @RequestMapping("/base")
    class C {
        @RequestMapping("/x")
        void qualquerVerbo() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert controlador.rotas == ()
    assert len(controlador.nao_resolvidas) == 1
    assert "method=" in controlador.nao_resolvidas[0].motivo


def test_varias_rotas_na_mesma_anotacao_viram_varios_endpoints():
    fonte = """
    @RestController
    @RequestMapping("/base")
    class C {
        @GetMapping({"/a", "/b"})
        void duas() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert sorted(r.rota for r in controlador.rotas) == ["/base/a", "/base/b"]


@pytest.mark.parametrize(
    "expressao",
    ["ROTAS.PRODUTOS", '"/api/" + VERSAO', '"${app.rota.produtos}"'],
)
def test_rota_que_nao_e_literal_vira_incerteza_registrada(expressao: str):
    """Chutar produziria endpoint inexistente; ignorar produziria silêncio."""
    fonte = f"""
    @RestController
    @RequestMapping("/base")
    class C {{
        @GetMapping({expressao})
        void dinamica() {{}}
    }}
    """
    (controlador,) = extrair_controladores(fonte)
    assert controlador.rotas == ()
    assert len(controlador.nao_resolvidas) == 1


def test_prefixo_irresoluvel_da_classe_derruba_todas_as_rotas_dela():
    fonte = """
    @RestController
    @RequestMapping(Rotas.BASE)
    class C {
        @GetMapping("/x") void x() {}
        @GetMapping("/y") void y() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert controlador.rotas == ()
    assert len(controlador.nao_resolvidas) == 1
    assert "nenhuma rota desta classe" in controlador.nao_resolvidas[0].motivo


def test_anotacao_em_comentario_ou_string_nao_conta():
    fonte = """
    @RestController
    @RequestMapping("/base")
    class C {
        // @GetMapping("/comentada")
        /* @PostMapping("/em-bloco") */
        String exemplo = "@DeleteMapping(\\"/em-string\\")";

        @GetMapping("/real") void real() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert [r.rota for r in controlador.rotas] == ["/base/real"]


def test_restricao_de_regex_no_segmento_sai_da_rota():
    fonte = """
    @RestController
    @RequestMapping("/base/")
    class C {
        @GetMapping("/{id:[0-9]+}") void get() {}
    }
    """
    (controlador,) = extrair_controladores(fonte)
    assert [r.rota for r in controlador.rotas] == ["/base/{id}"]


# ---------------------------------------------------------------------------
# O extrator: grafo + matriz de suporte
# ---------------------------------------------------------------------------


def test_extrator_le_os_dois_formatos_de_no_do_grafo(tmp_path: Path):
    """O Graphify real usa `source_file`; a fixture do dry-run usa `file`."""
    backend = tmp_path / "backend"
    (backend / "src").mkdir(parents=True)
    (backend / "src/C.java").write_text(
        '@RestController @RequestMapping("/x") class C { @GetMapping void l() {} }',
        encoding="utf-8",
    )
    for indice, chave in enumerate(("source_file", "file")):
        graph = tmp_path / f"graph-{indice}.json"
        graph.write_text(json.dumps({"nodes": [{chave: "src/C.java"}]}), encoding="utf-8")
        lido = extrair(graph=graph, backend=backend)
        assert [endpoint.canonico for endpoint in lido.endpoints] == ["GET /x"]


def test_extensao_fora_da_matriz_e_contada_e_nomeada(backend_spring: tuple[Path, Path]):
    graph, backend = backend_spring
    lido = extrair(graph=graph, backend=backend)
    assert lido.extensoes_ignoradas == ((".xml", 1),)
    assert ".xml" in lido.resumo_do_ignorado()


def test_controlador_de_fonte_de_teste_nao_entra_no_denominador(tmp_path: Path):
    """`src/test/java` é layout do Maven: o que sobe ali só existe dentro do MockMvc."""
    backend = tmp_path / "backend"
    for raiz in ("src/main/java", "src/test/java"):
        (backend / raiz).mkdir(parents=True)
        (backend / raiz / "C.java").write_text(
            f'@RestController @RequestMapping("/{raiz.split("/")[1]}") '
            "class C { @GetMapping void l() {} }",
            encoding="utf-8",
        )
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps({"nodes": [{"file": "src/main/java/C.java"}, {"file": "src/test/java/C.java"}]}),
        encoding="utf-8",
    )
    lido = extrair(graph=graph, backend=backend)
    assert [endpoint.canonico for endpoint in lido.endpoints] == ["GET /main"]
    assert lido.arquivos_de_teste == 1


def test_grafo_ausente_ou_ilegivel_nao_produz_denominador_vazio(tmp_path: Path):
    """Falhar alto, nunca devolver "nenhum endpoint" — que o gate leria como backend vazio."""
    with pytest.raises(GrafoNaoPreparado):
        extrair(graph=tmp_path / "nao-existe.json", backend=tmp_path)

    sem_nodes = tmp_path / "graph.json"
    sem_nodes.write_text(json.dumps({"grafo": []}), encoding="utf-8")
    with pytest.raises(GrafoNaoPreparado):
        extrair(graph=sem_nodes, backend=tmp_path)


def test_a_matriz_de_suporte_e_declarada_e_hoje_tem_uma_linha():
    """Se um adaptador entrar, este teste é o lembrete de exercitá-lo com fixture real."""
    assert [(a.linguagem, a.extensoes) for a in MATRIZ_DE_SUPORTE] == [("Java", (".java",))]


def test_a_chave_de_comparacao_ignora_o_nome_da_variavel_de_caminho():
    assert chave_de_endpoint("GET /a/{id}") == chave_de_endpoint("GET /a/{productId}")
    assert chave_de_endpoint("GET /a/{id}") != chave_de_endpoint("GET /a/id")


# ---------------------------------------------------------------------------
# O diff
# ---------------------------------------------------------------------------


def test_gabarito_que_cobre_o_controlador_inteiro_aprova(backend_spring: tuple[Path, Path]):
    graph, backend = backend_spring
    resultado = diff_grafo_manifesto(
        graph=graph,
        backend=backend,
        inventario=None,
        manifesto=manifesto_de(*CINCO_DE_PRODUTOS),
        recurso=recurso_de(),
    )
    assert resultado.aprovado
    assert resultado.violacoes == []
    # Aprovar não é o mesmo que ter olhado tudo: o que ficou fora da matriz é dito
    # junto com a aprovação, senão "aprovado" passa a significar "completo".
    assert any("fora da matriz" in aviso.mensagem for aviso in resultado.avisos)


def test_endpoint_no_backend_e_fora_do_gabarito_reprova_com_qaorq_002(
    backend_spring: tuple[Path, Path],
):
    """O defeito de origem, na forma exata em que ele aparece: três de cinco."""
    resultado = diff_grafo_manifesto(
        graph=backend_spring[0],
        backend=backend_spring[1],
        inventario=None,
        manifesto=manifesto_de(*CINCO_DE_PRODUTOS[:3]),
        recurso=recurso_de(),
    )
    assert resultado.veredito is VereditoDeGate.REPROVADO
    assert resultado.codigos == ["QAORQ-002", "QAORQ-002"]
    faltando = " ".join(violacao.mensagem for violacao in resultado.violacoes)
    assert "PATCH /api/v1/products/{id}" in faltando
    assert "DELETE /api/v1/products/{id}" in faltando


def test_rota_inventada_no_gabarito_reprova_com_qaorq_003(backend_spring: tuple[Path, Path]):
    resultado = diff_grafo_manifesto(
        graph=backend_spring[0],
        backend=backend_spring[1],
        inventario=None,
        manifesto=manifesto_de(*CINCO_DE_PRODUTOS, "POST /api/v1/products/{id}/publicar"),
        recurso=recurso_de(),
    )
    assert resultado.veredito is VereditoDeGate.REPROVADO
    assert resultado.codigos == ["QAORQ-003"]
    assert "/publicar" in resultado.violacoes[0].mensagem


def test_classe_que_o_gabarito_nao_toca_nao_gera_cobranca(tmp_path: Path):
    """Escopo por controlador: o recurso `products` não responde pelos endpoints de outro."""
    backend = tmp_path / "backend"
    pasta = backend / "src/main/java"
    pasta.mkdir(parents=True)
    (pasta / "ProductController.java").write_text(PRODUTOS, encoding="utf-8")
    (pasta / "OrderController.java").write_text(
        '@RestController @RequestMapping("/api/v1/orders") '
        "class OrderController { @GetMapping void list() {} @PostMapping void create() {} }",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {"file": "src/main/java/ProductController.java"},
                    {"file": "src/main/java/OrderController.java"},
                ]
            }
        ),
        encoding="utf-8",
    )

    resultado = diff_grafo_manifesto(
        graph=graph,
        backend=backend,
        inventario=None,
        manifesto=manifesto_de(*CINCO_DE_PRODUTOS),
        recurso=recurso_de(),
    )
    assert resultado.aprovado


def test_linguagem_fora_da_matriz_nao_termina_como_aprovado(tmp_path: Path):
    """A regra que não pode ser quebrada: sem denominador, nunca aprovação silenciosa."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "app.py").write_text(
        '@app.get("/api/v1/products")\ndef listar(): ...\n', encoding="utf-8"
    )
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"nodes": [{"file": "app.py"}]}), encoding="utf-8")

    resultado = diff_grafo_manifesto(
        graph=graph,
        backend=backend,
        inventario=None,
        manifesto=manifesto_de("GET /api/v1/products"),
        recurso=recurso_de(),
    )
    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
    assert not resultado.aprovado
    assert "matriz de suporte" in resultado.motivo
    assert ".py" in resultado.motivo


def test_grafo_ausente_interrompe_em_vez_de_aprovar(tmp_path: Path):
    resultado = diff_grafo_manifesto(
        graph=tmp_path / "nao-existe.json",
        backend=tmp_path,
        inventario=None,
        manifesto=manifesto_de("GET /api/v1/products"),
        recurso=recurso_de(),
    )
    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA


def test_rota_dinamica_do_inventario_vira_aviso_e_nunca_violacao(
    backend_spring: tuple[Path, Path],
):
    """Incerteza declarada não some e não reprova: as duas coisas seriam mentira."""
    inventario = Inventario(
        recurso="products",
        endpoints=[
            {  # pyright: ignore[reportArgumentType]
                "metodo": "GET",
                "rota": "/api/v1/products",
                "handler": "ProductController.list",
                "arquivo": "src/main/java/com/exemplo/api/ProductController.java",
            }
        ],
        rotas_dinamicas_nao_resolvidas=[
            RotaDinamica(
                expressao="ROTAS.EXTRA",
                arquivo="src/main/java/com/exemplo/api/ProductController.java",
                linha=42,
                motivo="rota montada por constante",
            )
        ],
    )
    resultado = diff_grafo_manifesto(
        graph=backend_spring[0],
        backend=backend_spring[1],
        inventario=inventario,
        manifesto=manifesto_de(*CINCO_DE_PRODUTOS),
        recurso=recurso_de(),
    )
    assert resultado.aprovado
    assert {aviso.codigo for aviso in resultado.avisos} == {"QAORQ-001"}
    assert any("ROTAS.EXTRA" in aviso.mensagem for aviso in resultado.avisos)


def test_incerteza_do_extrator_aparece_no_aviso_sem_reprovar(tmp_path: Path):
    backend = tmp_path / "backend"
    pasta = backend / "src"
    pasta.mkdir(parents=True)
    (pasta / "C.java").write_text(
        '@RestController @RequestMapping("/api/v1/products") class C {'
        "  @GetMapping void list() {}"
        "  @PostMapping(Rotas.CRIAR) void criar() {}"
        "}",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"nodes": [{"file": "src/C.java"}]}), encoding="utf-8")

    resultado = diff_grafo_manifesto(
        graph=graph,
        backend=backend,
        inventario=None,
        manifesto=manifesto_de("GET /api/v1/products"),
        recurso=recurso_de(),
    )
    assert resultado.aprovado
    assert [aviso.codigo for aviso in resultado.avisos] == ["QAORQ-001"]
    assert "Rotas.CRIAR" in resultado.avisos[0].mensagem


def test_nome_diferente_da_variavel_de_caminho_avisa_mas_nao_reprova(
    backend_spring: tuple[Path, Path],
):
    resultado = diff_grafo_manifesto(
        graph=backend_spring[0],
        backend=backend_spring[1],
        inventario=None,
        manifesto=manifesto_de(
            "POST /api/v1/products",
            "GET /api/v1/products",
            "GET /api/v1/products/{productId}",
            "PATCH /api/v1/products/{productId}",
            "DELETE /api/v1/products/{productId}",
        ),
        recurso=recurso_de(),
    )
    assert resultado.aprovado
    assert {aviso.codigo for aviso in resultado.avisos} == {"QAORQ-001"}
    assert any("{productId}" in aviso.mensagem for aviso in resultado.avisos)


def test_sem_manifesto_nao_ha_veredito(backend_spring: tuple[Path, Path]):
    resultado = diff_grafo_manifesto(
        graph=backend_spring[0],
        backend=backend_spring[1],
        inventario=None,
        manifesto=None,
        recurso=recurso_de(),
    )
    assert resultado.veredito is VereditoDeGate.ERRO_DA_FERRAMENTA
