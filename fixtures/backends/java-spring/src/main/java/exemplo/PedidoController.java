package exemplo;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/pedidos")
public class PedidoController {

    @GetMapping
    public List<Pedido> listar() { return servico.listar(); }

    @GetMapping("/{id}")
    public Pedido detalhar(@PathVariable Long id) { return servico.buscar(id); }

    @PostMapping
    public Pedido criar(@RequestBody NovoPedido corpo) { return servico.criar(corpo); }

    @PutMapping("/{id}")
    public Pedido atualizar(@PathVariable Long id, @RequestBody Pedido corpo) {
        return servico.atualizar(id, corpo);
    }

    @DeleteMapping("/{id}")
    public void remover(@PathVariable Long id) { servico.remover(id); }
}
