<!--
A tabela "Cenários e oráculo mínimo por categoria" do planejador. Vive num
arquivo próprio porque NÃO entra na instrução fixa: o código seleciona as linhas
das categorias do grupo da chamada e as envia na ENTRADA, na seção "Oráculo das
categorias desta chamada". Enviar as 12 linhas em toda chamada custava ~1k
tokens × 16 chamadas por recurso e somava restrições irrelevantes ao pedido — o
gatilho medido das espirais e loops.

Esta tabela é CÓPIA da que vive em `executor.md` — a fonte canônica é a skill
(references/catalogo-de-testes.md), e os dois arquivos precisam andar juntos
quando ela mudar. Duplicação conhecida e deliberada: o planejador decide OS
CASOS a partir dela; o executor comprova cada caso com o oráculo dela.

O formato importa para o código: `planejador._oraculo_do_grupo` seleciona as
linhas pelo prefixo "| `CAT-". As duas primeiras linhas (cabeçalho e separador)
sempre acompanham. Não acrescente prosa entre as linhas da tabela.
-->

| Cat | Cenários obrigatórios | Como comprovar |
| --- | --- | --- |
| `CAT-01` | comportamento válido principal com massa controlada; cada resultado de sucesso materialmente diferente; escrita comprova criação, alteração, transição ou exclusão; leitura comprova o recurso, a coleção ou o cálculo | status exato, headers, estrutura, tipos, valores e invariantes; correlacionar resposta com entrada e reconsultar o estado após mutação |
| `CAT-02` | remover cada campo obrigatório individualmente; enviar `null` separadamente quando o transporte representa nulidade real; omitir opcional e confirmar o default; `null` em opcional com regra própria | resultado exato e, em escrita rejeitada, prova de que o estado não mudou. Ausência e `null` não são equivalentes |
| `CAT-03` | tipo incompatível por campo; formato inválido (UUID, data, e-mail, código) quando confirmado; valor fora do enum; estrutura incompatível em objeto e array; representação que o parser trata diferente | erro funcional confirmado, localização do campo quando contratada, não vazamento e ausência de alteração indevida |
| `CAT-04` | valor na fronteira válida; valor imediatamente fora; inferior e superior separados; quantidade vazia, unitária e máxima quando diferirem. **Probe de magnitude agressiva por campo mesmo sem limite declarado** | comportamento em cada lado da fronteira e estado preservado nas rejeições. No probe: resposta controlada, sem `5xx`, sem vazar erro de banco ou framework, sem persistência parcial |
| `CAT-05` | body ausente, `null`, escalar, vazio, malformado, array ou objeto incompatível; campo desconhecido quando o contrato é fechado; `Content-Type` ausente ou incompatível; query ou path que o parser não interpreta; método incorreto quando relevante | erro confirmado, sem detalhe interno e sem persistência parcial |
| `CAT-06` | identificador seguramente ausente do recurso alvo; identificador estrangeiro ausente por relacionamento com comportamento próprio; recurso existente mas invisível, quando a política oculta | status e código confirmados, não vazamento e, em mutação, ausência de criação ou alteração parcial |
| `CAT-07` | cada transição permitida e cada proibida; exclusão de recurso referenciado por dependente; duplicidade e unicidade; combinações com resultados distintos; preservação de imutáveis, derivados e cálculos; ordem de efeitos quando falha parcial corromperia o estado | resposta e estado anterior/posterior; confirmar transição, preservação, cálculo ou ausência de efeito |
| `CAT-08` | sem credencial; credencial inválida; expirada quando determinístico; autenticado sem a permissão; titularidade e tenant com identidades e massas distintas; cada perfil com comportamento diferente; ler e alterar recurso alheio; ausência de efeito após escrita não autorizada | identidade negativa **diferencial** (válida e igual à autorizada no que é irrelevante, sem a permissão-alvo), validada por um controle positivo independente; releitura com identidade autorizada após bloqueio |
| `CAT-09` | tentar sobrescrever cada campo sensível aceito pelo desserializador; elevar privilégio; trocar tenant ou titularidade; definir estado, identidade, auditoria ou valor calculado | resposta **e releitura** provando que o valor controlado não foi aceito nem persistido |
| `CAT-10` | coleção vazia e não vazia; forma dos itens, envelope e metadados; defaults de página, tamanho e ordenação; limites válidos e inválidos; primeira, última e além da última página; cursor válido, inválido e final; consistência entre páginas; cada filtro; ordenação nos dois sentidos; isolamento por usuário ou tenant | itens, total, página/cursor, ordem e correspondência real dos filtros — nunca só que o array existe. Ordenação exige ao menos dois valores distintos que invertam a sequência; coleção unitária ou valores empatados não fecham o alvo |
| `CAT-11` | repetir sequencialmente a mesma operação; repetir com a mesma chave de idempotência; repetir com chave diferente quando o comportamento muda; confirmar que não surgiu efeito duplicado; concorrência só com mecanismo seguro e determinístico | comparar resposta e estado após cada tentativa e contar efeitos por chave determinística |
| `CAT-12` | upload: arquivo sintético válido, ausente, vazio, tipo inválido, nome ou metadados inválidos, conteúdo inválido, tamanho na fronteira e acima, estado incompatível. download: existente, inexistente, existente mas invisível, headers e nome, isolamento | upload: resposta **e** metadados/arquivo persistidos. download: bytes ou conteúdo real, não apenas status e headers. Nas rejeições, ausência de efeito |
