package fixture.controllers;

// Backend de fixture: existe para o --dry-run exercitar as tools de leitura do
// mapeador (ler_arquivo, listar_diretorio, buscar_no_backend) com confinamento de
// caminho real. Não é um backend de verdade.

@RestController
@RequestMapping("/pedidos")
public class PedidoController {

  @GetMapping
  public List<Pedido> listar() {
    return service.listar();
  }

  @PostMapping
  public Pedido criar(@RequestBody @Valid Pedido pedido) {
    return service.criar(pedido);
  }
}
