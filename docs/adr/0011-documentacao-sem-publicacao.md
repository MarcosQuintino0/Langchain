# ADR 0011 — Documentação com MkDocs, sem publicação

**Status:** Aceita

## Contexto

O projeto não tinha diagrama nenhum, glossário nenhum e nenhuma página sobre o ferramental. Os códigos de saída da CLI não estavam em `.md` algum. Publicar o site tornaria público o conteúdo do repositório, incluindo arquitetura e decisões.

## Decisão

MkDocs Material renderiza `docs/`. A CI roda `mkdocs build --strict` e **descarta** o resultado. Nada é publicado. Quem quiser ler roda `mkdocs serve`.

## Consequências

O `--strict` reprova link quebrado e página fora da navegação, que é o valor imediato do gerador. Em troca, entram ~20 dependências pinadas num extra `[docs]` separado do `dev`.

**Gatilho para rever:** um consumidor externo da documentação, ou mais de 25 páginas, ou necessidade de busca. Sem gatilho escrito, 'por enquanto' vira 'para sempre' — ou vira 'vamos publicar agora' na primeira sexta-feira.
