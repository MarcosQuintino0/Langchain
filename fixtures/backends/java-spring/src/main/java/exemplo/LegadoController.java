package exemplo;

import org.springframework.web.bind.annotation.*;

@RestController
public class LegadoController {

    private static final String BASE = "/legado/v1";

    @GetMapping(BASE + "/itens")
    public List<Item> itens() { return servico.itens(); }
}
