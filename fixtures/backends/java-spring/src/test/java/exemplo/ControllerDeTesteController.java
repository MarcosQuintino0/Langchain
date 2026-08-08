package exemplo;

import org.springframework.web.bind.annotation.*;

@RestController
public class ControllerDeTesteController {

    @GetMapping("/interno/ping")
    public String ping() { return "pong"; }
}
