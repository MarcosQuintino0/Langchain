package exemplo;

import org.springframework.web.bind.annotation.*;

@RestController
public class RelatorioController {

    @RequestMapping(value = "/relatorios/mensal", method = RequestMethod.GET)
    public Relatorio mensal() { return servico.mensal(); }

    @RequestMapping(value = "/relatorios", method = RequestMethod.POST)
    public Relatorio gerar(@RequestBody Filtro filtro) { return servico.gerar(filtro); }
}
