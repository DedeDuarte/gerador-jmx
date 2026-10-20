# Agente JMeter

Gera planos JMeter (`.jmx`) a partir de um `.har` capturado no navegador, usando IA
para inferir variáveis, dependências entre requests, extractors e assertions.

## Estrutura do projeto

```
.
├── agente.py            # CLI de entrada
├── requirements.txt
├── .env                 # DRYVN_API_KEY (não versionado)
├── src/                 # código-fonte
│   ├── har_parser.py     # parse do .har -> lista de requests
│   ├── filters.py         # filtros de terceiros / assets estáticos
│   ├── ai_analyzer.py     # chamada à IA (DRYVN Gateway) -> JSON JMeter-ready
│   └── jmx_generator.py   # JSON -> .jmx (samplers, controllers, auth, etc.)
├── test/                 # fixtures de entrada (.har de exemplo)
└── reports/              # saídas geradas (.json, .jmx, .csv)
```

## Uso

O `.har` de exemplo fica em `test/renner_login.har` (ajuste o caminho para o seu
próprio arquivo capturado do navegador).

```cmd
python agente.py test/renner_login.har --analyze -g "login do usuario e validacao de que o perfil retorna corretamente"
```

Por padrão o JSON é salvo em `reports/resultado.json` (`-o/--output` sobrescreve o
caminho).

Gerar o JSON **e** o plano JMeter (.jmx) em um único comando (`--jmx` encadeia a
geração do .jmx logo após salvar o JSON, usando o mesmo nome base com extensão
`.jmx` — ex: `reports/resultado.json` -> `reports/resultado.jmx`):

```cmd
python agente.py test/renner_login.har --analyze -g "login do usuario e validacao de que o perfil retorna corretamente" --jmx
```

- `--jmx-output <path>` sobrescreve o caminho do `.jmx` gerado (default: mesmo nome
  de `-o/--output` com extensão `.jmx`).
- `--threads`, `--loops`, `--ramp-up` são opcionais (default: 1/1/1).

Gerar o plano JMeter (.jmx) separadamente, a partir de um resultado.json já existente:

```cmd
python agente.py --to-jmx reports/resultado.json -o reports/plano.jmx --threads 5 --loops 3 --ramp-up 10
```

- Se houver `csv_candidates` (ex: login/password) ou variáveis sem origem definida,
  um `.csv` é gerado ao lado do `.jmx` (ex: `plano_data.csv`) com o header **e uma
  linha de dados** já preenchida a partir de `test_data_metadata` (ver seção
  abaixo) com os valores **reais capturados do `.har`**, pronta para reproduzir
  o fluxo exatamente como foi gravado com **1 usuário virtual** (`--threads 1`,
  default).

## Dados de teste (valores reais do fluxo capturado)

Ao rodar `--analyze`, a IA identifica toda variável que precisa vir de CSV
(`csv_candidates` + variáveis órfãs em `variables_used`) e preenche, no JSON de
saída, a seção `test_data_metadata` com o **valor real observado no `.har`**
para cada uma — ex: se o login foi feito com `andre@mail.com` / `1234`, esses
são exatamente os valores salvos:

```json
"test_data_metadata": {
  "description": "Valores de teste para variaveis do CSV, capturados do .har...",
  "variables": {
    "login": { "value": "andre@mail.com", "sensitive": true },
    "password": { "value": "1234", "sensitive": true },
    "deviceId": { "value": "a1b2c3d4-...", "sensitive": false }
  }
}
```

- **`value`**: sempre o dado real usado na captura do fluxo (não é mascarado
  nem substituído por um placeholder). O objetivo é reproduzir o teste
  exatamente como foi gravado no navegador.
- **`sensitive`**: apenas sinaliza que a variável é um dado pessoal (login,
  senha, email, CPF, CNPJ, telefone, endereço, cartão, token, etc.) — não
  altera o valor. Complementa a classificação da IA com uma heurística de
  nomes (`_looks_sensitive` em `src/ai_analyzer.py`) para o caso de ela não
  marcar corretamente.

Esses mesmos valores são repassados por `src/jmx_generator.py` para a **linha
de dados do `.csv` final**, na mesma ordem das colunas do header — permitindo
rodar o `.jmx` gerado imediatamente com **1 usuário** (`--threads 1`) e
reproduzir exatamente o fluxo lido do `.har`, sem precisar editar o `.csv`
manualmente antes do primeiro teste.

## Autenticação (auth controller)

A IA analisa o fluxo do `.har` e preenche um bloco `"auth"` no JSON de saída,
detectando automaticamente o mecanismo usado:

- **`custom_login`**: login via formulário/body (ex: CPF + senha) que gera sessão
  por cookies e/ou token dinâmico. As requests do grupo (`"group"`) indicado em
  `auth.login_group` são agrupadas sob um **Once Only Controller**, executando a
  autenticação apenas uma vez por usuário virtual mesmo com múltiplos loops. Um
  **HTTP Cookie Manager** é sempre incluído na Thread Group para manter a sessão.
- **`bearer`**: adiciona um **HTTP Header Manager** global com
  `Authorization: Bearer ${token}`.
- **`basic`**: adiciona um **HTTP Authorization Manager** nativo do JMeter.
- **`none`**: nenhum mecanismo de autenticação identificado.

Requests com o mesmo campo `"group"` (ex: `"LOGIN"`, `"PRODUTOS"`, `"CHECKOUT"`)
são agrupadas visualmente sob um Controller no `.jmx` gerado, replicando a
organização por etapas do plano de referência da Renner.