---
name: gerar-jmeter
description: Gera um script JMeter (.jmx) a partir de um arquivo .har e da descrição da jornada (texto livre e/ou lista de endpoints). Use quando o usuário pedir para criar, gerar ou montar um JMeter/jornada/script de carga a partir de um HAR.
argument-hint: <caminho do .har> <jornada: "login, buscar produto, checkout" e/ou "POST /login, GET /cart"> [destino: ./saida, ~/jmeter ou C:\jmeter]
---

# Gerar JMeter a partir de um HAR

Você é o único passo com julgamento num pipeline determinístico. O Python filtra o HAR,
detecta correlações e gera o XML. **Você decide a jornada**: quais requisições entram, em
que ordem, como se chamam e quais valores viram variáveis. Você escreve isso em um
`plan.json`, que referencia as requisições pelo ID do catálogo.

Rode os comandos na pasta atual (onde o usuário abriu o Claude): `python -m harjmx ...`
funciona em qualquer pasta. Se falhar por falta do pacote, peça ao usuário para instalar o
Agente_JMeter: `python -m pip install -e <pasta do Agente_JMeter>`.

## Entradas
- **HAR**: caminho do arquivo. Se vier só o nome, procure em `jornadas/hars/` da pasta atual. Se não vier, pergunte.
- **Jornada**: texto livre ("login, buscar produto, adicionar à sacola") e/ou endpoints
  explícitos ("POST /login", "GET */products/*"). Se não vier nenhum dos dois, pergunte.
- **Destino** (opcional): último argumento, depois da jornada entre aspas. É a pasta onde o
  script é criado. Pode ser relativo à pasta atual (`./saida`, `../testes`), absoluto
  (`C:\jmeter`) ou começar com `~` (`~/jmeter`). Se não vier, use `jornadas` (na pasta atual)
  sem perguntar. Só pergunte se não der para separar o que é jornada e o que é destino.
- **Projeto**: nome em snake_case, derivado do nome do HAR. Nunca `hars` (é a pasta dos HARs).
- **Pasta do script** (`<pasta>` no resto deste roteiro): `<destino>/<proj>`, ex:
  `jornadas/renner_login` ou `~/jmeter/renner_login`. O `.jmx` e o `massa.csv` ficam na
  raiz dela; catálogo, correlações, plano e o resto da geração ficam em `<pasta>/_geracao/`.
  Se `<pasta>` já tiver um script, pergunte se pode sobrescrever ou use outro nome.

## Passo a passo

### 1. Preparar
```
python -m harjmx prepare "<har>" --out "<pasta>" [--match "POST /login" ...]
```
Passe cada endpoint explícito do usuário em um `--match`. O catálogo vai listar os IDs que
cada um casou.

### 2. Ler o catálogo
Leia `<pasta>/_geracao/catalog.md`. Ele mostra estrutura e nomes de chaves, sem valores.
- **Hosts excluídos como terceiros**: se a jornada depende de uma API ou SSO em outro
  domínio (ex: `sso.empresa.com`, `api.empresa.com.br`), rode o prepare de novo com
  `--host <dominio>`.
- **obs**:
  - `origem Cn`: a resposta fornece um valor dinâmico.
  - `usa Cn`: a requisição depende de um valor dinâmico.
  - `repete #n`: a mesma chamada já apareceu antes.
  - `redirect →`: a resposta é um redirect.
- **ATENÇÃO: respostas sem corpo gravado**: o HAR foi exportado sem conteúdo dessas
  respostas, e correlações que saem delas não aparecem. Avise o usuário. Se a jornada
  depende delas, use extractor manual com `valor_de` (ver passo 4) ou peça um HAR novo.
- **Valores repetidos sem origem no HAR**: valores dinâmicos sem resposta de origem
  gravada. Exemplo típico: um token de sessão cuja resposta ficou sem corpo. Decida caso
  a caso:
  - origem numa resposta sem corpo: extractor manual com `valor_de`;
  - dado do usuário (e-mail, CEP): `parametros`;
  - gerado pelo navegador (deviceId, fingerprint) e aceito como constante: ignore.

### 3. Escolher as requisições
- Mapeie cada passo da jornada do usuário para os IDs. Siga a ordem do HAR, a menos que o
  usuário peça outra.
- **Inclua** a requisição de origem de toda correlação que uma requisição escolhida usa.
  Exemplo: `#9 usa C1` e `C1 ← #2` significa que `#2` precisa entrar.
- **Descarte**:
  - telemetria e analytics, mesmo no domínio principal (GTM, `/collect`, `/log`, `/metrics`, `datalayer`);
  - polling repetido sem relação com a jornada;
  - chamadas que o usuário não pediu e que nada usa.
- **Redirects**: o sampler segue o redirect, então não inclua a requisição de destino
  separadamente. Exceção: o destino tem dados que você precisa extrair.
- Na dúvida sobre o que uma requisição faz, veja os detalhes com
  `python -m harjmx show "<pasta>" <id> [<id>...] --redact`.
  - O `--redact` mascara senhas, tokens, cookies e login/e-mail, e mostra só o tamanho.
  - Tire o `--redact` apenas se precisar do valor real, e não repita esses valores na conversa.
- Para procurar um endpoint, use `python -m harjmx match "<pasta>" "* *cart*"`. O `*` no
  método casa qualquer método. A busca inclui as requisições excluídas pelos filtros e mostra
  o motivo da exclusão.
- Se um passo pedido pelo usuário não existe no HAR, diga isso e peça uma gravação nova.
  **Nunca invente requisições.**
- **Confira a origem de cada correlação.** O detector aponta a *primeira* resposta que contém
  o valor, que pode ser de outra tela. Exemplo: o mesmo selo de produto aparece numa vitrine
  anterior. Se a origem não faz parte da jornada, não inclua a correlação; prefira um
  extractor manual numa requisição que já está no plano.

### 4. Escrever `<pasta>/_geracao/plan.json`
Use [plan.example.json](${CLAUDE_SKILL_DIR}/plan.example.json) como modelo. As regras de nome e agrupamento
estão em [boas-praticas.md](${CLAUDE_SKILL_DIR}/boas-praticas.md) e o Python as aplica sozinho. Você só fornece
as peças:

| Campo | O que colocar |
|---|---|
| `nome_teste` | snake_case, vira o nome do arquivo `.jmx` |
| `etapas` | `numero` sequencial a partir de 1 e `nome` como substantivo curto (`Login`, `Sacola`) |
| `requisicoes[].etapa/passo` | `x.0`, `x.1`… Um passo novo dentro da mesma etapa; uma etapa nova para um contexto novo |
| `requisicoes[].descricao` | verbo no infinitivo, sem abreviação: `Autenticar Usuário`, `Buscar Perfil` |
| `requisicoes[].endpoint_nome` | **opcional**. O padrão é o path real, sem query string, com `${var}` virando `{var}` e IDs numéricos virando `{id}`. Omita na maioria dos casos. Preencha só para encurtar prefixos longos ou nomear IDs (`/api/products/{idProduto}`) |
| `grupos` | quando houver muitas etapas, junte as relacionadas: `{"de": 5, "ate": 8, "nome": "Listar Produtos"}` |
| `correlacoes` | todo `Cn` cuja origem e algum uso estejam no plano. Renomeie com `variavel` em camelCase significativo. Quando o valor é **um item escolhido de uma lista** (produto de uma vitrine, `records[23]`, `ocorrência 6`), use `"aleatorio": true`: cada usuário virtual escolhe um item diferente (índices viram `[*]`, match 0). Se o próximo passo depende de atributos do **mesmo** item (sku do produto escolhido), não use aleatório nos dois de forma independente: mantenha posicional ou extraia os dois do mesmo item |
| `parametros` | dados de entrada do usuário (login, senha, e-mail, CPF, CEP, produto escolhido): `destino: "csv"`, que é o padrão. Configuração fixa do ambiente: `destino: "global"` (User Defined Variables). Informe `har_id` + `local` no formato dos usos do catálogo (`query.k`, `form.k`, `header.k`, `body.$.campo`, `path.n`). O `har_id` pode ser qualquer requisição do catálogo, mesmo fora do plano |
| `requisicoes[].extractors` | só para o que o catálogo não detectou: `{"variavel", "tipo": "json"\|"regex", "expressao", "campo": "body"\|"headers", "match": 1}`. O valor extraído é trocado automaticamente nas requisições seguintes. Se a resposta não foi gravada, informe `"valor_de": "<har_id>:<local>"` de uma requisição que usa o valor (o catálogo sugere esse texto na seção "sem origem"). Essa requisição não precisa estar no plano. Nesse caso a expressão não é verificada: escolha a mais genérica possível e, no relatório final, marque o extractor como **palpite a confirmar** no primeiro smoke test |
| `requisicoes[].assertions` | `{"tipo": "status", "esperado": "200"}` nas requisições-chave (login, checkout, pagamento). `json_existe` para campos críticos |
| `seguir_redirect` | só quando precisar parar no 3xx |

Não copie headers, bodies nem valores para o plano. O build busca tudo no HAR.

### 5. Validar até passar
```
python -m harjmx validate "<pasta>"
```
Corrija cada `ERRO` no plan.json e rode de novo. As mensagens dizem o que mudar. Leia os
`AVISO`s:
- variável não usada: você incluiu uma correlação desnecessária;
- ordem diferente do HAR: confirme se é intencional.

### 6. Gerar
```
python -m harjmx build "<pasta>"
```
Gera `<pasta>/<nome_teste>.jmx` e, se houver parâmetros de CSV, o `massa.csv`. **Nunca
edite o .jmx à mão**: mude o plano e rode o build de novo.

### 7. Reportar ao usuário
- Caminhos do `.jmx` e do `.csv`.
- A árvore resultante: controllers e nomes das requisições.
- Correlações aplicadas (`variavel ← origem`) e as variáveis do CSV.
- Riscos que você notou, por exemplo:
  - captcha (`g-recaptcha-response`) ou device fingerprint fixo;
  - tokens com expiração gravados como constante;
  - cookies anti-bot pré-carregados, que expiram (ver [har-com-cookies.md](${CLAUDE_SKILL_DIR}/har-com-cookies.md));
  - avisos do validate que ficaram;
  - limitações conhecidas do build:
    - o build troca o valor onde ele aparece, inclusive dentro de listas (`skuList=a,b,${sku}`);
      listas montadas a partir de um array da resposta precisam de ajuste manual;
    - o slug do produto em headers `Referer` continua literal.
- Se a pasta atual for um repositório git e `jornadas/` não estiver no `.gitignore`, avise: o
  `.jmx` e o `massa.csv` têm credenciais. Sugira adicionar `jornadas/` ao `.gitignore`.
- Como rodar um smoke test, contra um ambiente onde isso seja permitido:
  `jmeter -n -t "<pasta>/<nome>.jmx" -l "<pasta>/_geracao/smoke.jtl" -JVUs=1 -JRampup=1 -Jduration=30`
