# Boas Práticas com JMeter
Guia de referência para padronização de testes de performance com JMeter. Aborda a organização estrutural dos planos de teste, separação de requisições por assunto ou funcionalidade e convenções de nomenclatura, com o objetivo de garantir consistência, manutenibilidade e clareza nos projetos de teste.

## 1. Requisição HTTP
Um nome bem definido permite identificar de imediato o que uma requisição faz, sem precisar abri-la. Isso reduz o tempo de leitura do plano de teste e facilita a manutenção por outras pessoas do time.

---

### Padrão de Nomenclatura:
`<etapa.passo> <MÉTODO> - <endpoint> - <descrição curta>`

| Campo           | Objetivo | Exemplo | Observações |
| :-------------- | :------- | :------ | :---------- |
| Etapa.Passo     | Indica a etapa ao qual a requisição pertence e sua ordem dentro dela | `1.0`, `1.1`, `2.0` | Use número inteiro para uma nova etapa e decimal para os passos dentro da mesma etapa |
| MÉTODO          | Indica o verbo HTTP utilizado na requisição | `GET`, `POST`, `PUT`, `DELETE` | Sempre em letras maiúsculas |
| Endpoint      | Identifica o recurso acessado na API | `/api/login`, `/api/cart/{idProduto}` | Remova o domínio e mantenha apenas o caminho a partir da `/`. Variáveis dinâmicas devem ser indicadas com `{...}` |
| Descrição curta | Descreve de forma clara o que a requisição faz | `Autenticar Usuário`, `Buscar Perfil` | Use verbos no infinitivo e evite abreviações |

---

### Exemplos
`1.0 POST - /api/login - Abrir Página Login`  
`1.1 POST - /api/login - Autenticar Usuário`  
`1.2 GET - /api/profile - Buscar Perfil`  
`2.0 GET - /api/product/{idProduto} - Ver Produto`  
`2.1 POST - /api/cart - Adicionar ao Carrinho`  

### Detalhes

- __Query parameters__ não devem aparecer no nome da requisição. Configure-os nos parâmetros da HTTP Request.

  | | |
  |:--|:--|
  | Requisição real | `GET /api/products?page=2&category=books` |
  | Nome correto | `1.0 GET - /api/products - Listar Produtos` |

- __Path variables__ (valores dinâmicos que fazem parte do endpoint) devem aparecer no nome, usando `{...}`.

  | | |
  |:--|:--|
  | Requisição real | `GET /api/products/42` |
  | Nome correto | `1.0 GET - /api/products/{idProduto} - Ver Produto` |

<br>

---

<br>

## 2. JSON Extractor
Quando uma requisição precisa extrair um valor da resposta via JSON Extractor, o nome do elemento deve deixar claro qual variável está sendo criada, facilitando a leitura do fluxo por qualquer pessoa do time.

---

### Padrão de Nomenclatura:
`JSON Extractor - <variável>`

| Campo    | Objetivo | Exemplo | Observações |
| :------- | :------- | :------ | :---------- |
| Variável | Identifica o valor que está sendo extraído e armazenado | `tokenLogin`, `idProduto` | Use camelCase e um nome que reflita o conteúdo da variável |

---

### Exemplos
`JSON Extractor - tokenLogin`  
`JSON Extractor - idProduto`  
`JSON Extractor - idPedido`  

---

### Detalhes
- __Default Value:__ `<variável>_ERR`
  - Sempre defina um valor de erro padrão
  - Se a extração falhar, a variável receberá esse valor, facilitando a identificação do problema na análise do teste

<br>

---

<br>

## 3. Estrutura de Testes Pequenos
Testes com poucas requisições não precisam de Simple Controllers. Basta listar as requisições soltas, seguindo o padrão de nomenclatura definido em [1. Requisição HTTP](#1-requisição-http). Cada etapa tem apenas um passo (`x.0`), então não há necessidade de agrupamento.

### Quando usar
- Fluxo simples com poucas requisições
- Cada etapa tem apenas um passo (`x.0`)
- Não há contextos distintos que justifiquem agrupamento

### Estrutura Visual
```
Plano de Teste
└── Grupo de Usuários
    ├── 1.0 MÉTODO - /endpoint - Descrição
    ├── 2.0 MÉTODO - /endpoint - Descrição
    ├── 3.0 MÉTODO - /endpoint - Descrição
    └── ...
```

<br>

---

<br>

## 4. Estrutura de Testes Grandes
Testes com muitas requisições ou etapas com múltiplos passos devem usar Simple Controllers para organizar e agrupar o conteúdo. Isso facilita a navegação no plano e deixa claro o que cada bloco de requisições representa.

### Quando usar
- Etapas com mais de um passo (`x.0`, `x.1`, `x.2`...)
- Muitas requisições que se beneficiam de agrupamento visual
- Contextos distintos que merecem separação clara

<br>

### 4.1 Simple Controller
Use o Simple Controller para agrupar os passos de uma mesma etapa. Requisições que possuem apenas um passo (`x.0`) podem ficar soltas, fora de qualquer controller.

---

### Padrão de Nomenclatura:

__Simple Controller__: Agrupa os passos de uma etapa (`x.0`, `x.1`, `x.2`...):  
`<etapa> - <nome da etapa>`

__Simple Controller agrupado__: Junta controllers de várias etapas em um único grupo:  
`<etapa inicial>-<etapa final> - <nome do grupo>`

| Campo          | Objetivo | Exemplo | Observações |
| :------------- | :------- | :------ | :---------- |
| Etapa          | Indica qual etapa ou intervalo de etapas o controller representa | `3`, `5-8` | Use intervalo apenas quando agrupar múltiplos controllers |
| Nome da etapa  | Identifica o contexto das requisições dentro de um Simple Controller simples | `Login`, `Sacola` | Use substantivos curtos e claros |
| Nome do grupo  | Identifica o contexto geral das etapas dentro de um Simple Controller agrupado | `Listar Produtos`, `Pagamento` | Use substantivos curtos e claros |

---

### Exemplos

Simple Controller de uma etapa:  
`3 - Login`  
`5 - Sacola`  

Simple Controller agrupado:  
`5-8 - Listar Produtos`  
`13-17 - Pagamento`  

---

### Estrutura Visual
```text
Plano de Teste
└── Grupo de Usuários
    │
    ├── 1.0 MÉTODO - /endpoint - Descrição              <- Requisição solta
    │
    ├── 2 - Nome da Etapa                               <- Simple Controller
    │   ├── 2.0 MÉTODO - /endpoint - Descrição
    │   └── 2.1 MÉTODO - /endpoint - Descrição
    │
    ├── 3-4 - Nome do Grupo                             <- Simple Controller Agrupado
    │   │
    │   ├── 3 - Nome da Etapa                           <- Simple Controller
    │   │   ├── 3.0 MÉTODO - /endpoint - Descrição
    │   │   └── 3.1 MÉTODO - /endpoint - Descrição
    │   │
    │   └── 4 - Nome da Etapa                           <- Simple Controller
    │       ├── 4.0 MÉTODO - /endpoint - Descrição
    │       └── 4.1 MÉTODO - /endpoint - Descrição
    │
    └── 5.0 MÉTODO - /endpoint - Descrição              <- Requisição solta
```