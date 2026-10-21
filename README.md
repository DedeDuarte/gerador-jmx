# Agente JMeter

Gera scripts JMeter (`.jmx`) a partir de um `.har` e de uma descrição da jornada
("login, ver produto, adicionar à sacola").

## Como rodar

### 0. Instalação (uma vez)
```powershell
python -m pip install -e ".[dev]"
```

### 1. Direto pelo terminal
Cinco comandos, sempre a partir da raiz do repositório. O exemplo usa um HAR de `jornadas/hars/`.

```powershell
# 1. Filtra o HAR e gera o catálogo das requisições
python -m harjmx prepare jornadas\hars\renner_login.har --out jornadas\scripts\meu_teste

# 2. Leia o catálogo: IDs das requisições, correlações detectadas e avisos
code jornadas\scripts\meu_teste\_geracao\catalog.md

#    Para ver uma requisição inteira (as senhas ficam mascaradas):
python -m harjmx show jornadas\scripts\meu_teste 32 --redact
#    Para achar um endpoint:
python -m harjmx match jornadas\scripts\meu_teste "* *login*"

# 3. Escreva jornadas\scripts\meu_teste\_geracao\plan.json com a jornada
#    (modelo: .claude\skills\gerar-jmeter\plan.example.json)

# 4. Confere o plano contra o HAR. Os erros dizem o que corrigir
python -m harjmx validate jornadas\scripts\meu_teste

# 5. Gera o .jmx (e o massa.csv com login/senha)
python -m harjmx build jornadas\scripts\meu_teste
```
O resultado sai em `jornadas\scripts\meu_teste\<nome_teste>.jmx`, que você pode abrir no JMeter ou rodar
sem interface:
```powershell
C:\apache-jmeter-5.6.3\bin\jmeter.bat -n -t jornadas\scripts\meu_teste\<nome_teste>.jmx -l jornadas\scripts\meu_teste\_geracao\smoke.jtl -JVUs=1 -JRampup=1 -Jduration=30
```

### 2. Pelo Claude Code ou Codex
O agente faz os 5 passos acima sozinho. A parte de julgamento, escrever o `plan.json`,
também fica com ele.

**Claude Code**: abra o repositório e rode a skill:
```
/gerar-jmeter jornadas/hars/teste.har "login, ver produto, adicionar à sacola"
```

**Codex** (ou outro agente): ele não conhece a skill. Peça que siga o mesmo roteiro:
```
Siga as instruções de .claude/skills/gerar-jmeter/SKILL.md com o HAR
jornadas/hars/teste.har e a jornada "login, ver produto, adicionar à sacola".
```

A jornada pode ser texto livre, endpoints explícitos (`POST /login, GET */products/*`) ou os
dois. No fim, o agente informa o caminho do `.jmx`, a árvore gerada, as variáveis e os riscos
que encontrou.

> **Grave o HAR com conteúdo.** No DevTools, use "Preserve log", "Disable cache" e "Save all
> as HAR with content". Sem os corpos das respostas, as correlações que saem delas não são
> detectadas. Mais detalhes em [antigo/docs/HAR_COM_COOKIES.md](antigo/docs/HAR_COM_COOKIES.md).

> ⚠️ O JMX gerado aponta para o host do HAR, muitas vezes **produção**, e usa o login real
> gravado. Para rodar contra outro ambiente, troque a variável `host` em *User Defined
> Variables*.

## Como funciona
O **Python** faz tudo que é determinístico:
- filtrar o HAR;
- detectar correlações;
- aplicar a nomenclatura das boas práticas;
- montar o XML.

A **IA** decide a jornada:
- quais requisições entram e em que ordem;
- o nome de cada etapa;
- que valores viram variáveis.

A IA nunca copia dados do HAR. Ela escreve um `plan.json` que referencia as requisições por
ID, e o Python busca headers, bodies e credenciais reais direto no HAR.

```
HAR ──prepare──> catalog.md ──(IA)──> plan.json ──validate──> build ──> .jmx + massa.csv
```

### Comandos
| Comando | O que faz |
|---|---|
| `prepare <har> --out jornadas/scripts/<proj> [--host dominio] [--match "POST /login"]` | Filtra o HAR e gera `catalog.md` e `correlations.json` |
| `show jornadas/scripts/<proj> <id>... [--redact]` | Mostra requisição e resposta completas (`--redact` mascara senhas e tokens) |
| `match jornadas/scripts/<proj> "POST /login"` | Casa endpoints com IDs; mostra também as requisições excluídas pelos filtros |
| `validate jornadas/scripts/<proj>` | Confere o `plan.json` contra o HAR |
| `build jornadas/scripts/<proj>` | Valida e gera o `.jmx` (mais o `massa.csv`, se houver variáveis de CSV) |

O formato do `plan.json` está em [src/harjmx/plan.py](src/harjmx/plan.py). Há um exemplo
completo em [.claude/skills/gerar-jmeter/plan.example.json](.claude/skills/gerar-jmeter/plan.example.json).

### O que o JMX gerado tem
- **Nomes e agrupamento** conforme `boas-praticas_jmeter.md`:
  - requisições como `1.0 POST - /api/login - Autenticar Usuário`;
  - Simple Controller `N - Nome` para etapas com vários passos;
  - Simple Controller agrupado `3-4 - Nome` para juntar etapas;
  - extractors `JSON Extractor - var`, com default `var_ERR`.
- **Esqueleto** igual ao `renner_sap_uy.jmx`:
  - Thread Group por propriedades (`-JVUs`, `-JRampup`, `-Jduration`);
  - User Defined Variables com os hosts;
  - Cookie Manager e Cache Manager;
  - Header Manager global com os headers comuns e um por sampler só com o que difere;
  - think time.
- **Correlações** detectadas automaticamente: JSON Extractor, ou Regex em body ou headers.
  Opcionalmente, item aleatório de uma lista (`"aleatorio": true`).
- **Massa de dados** em CSV (login, senha…) com os valores reais do HAR.
- **Cookies anti-bot** que o navegador já tinha, pré-carregados no Cookie Manager.

Os padrões (thread group, think time, headers descartados) ficam em
[config/defaults.yaml](config/defaults.yaml). Os domínios de telemetria descartados ficam em
[config/denylist.txt](config/denylist.txt).

## Estrutura

### Jornadas
Entradas e saídas ficam em `jornadas/`, fora do git (HARs e scripts têm credenciais e cookies):
```
jornadas/
├── hars/                        # HARs mais usados (opcional: o HAR pode vir de qualquer lugar)
└── scripts/
    └── <proj>/                  # uma pasta por script gerado
        ├── <nome_teste>.jmx     # o que vai para o JMeter
        ├── massa.csv            # precisa ficar ao lado do .jmx
        └── _geracao/            # resíduos da geração: session.json, catalog.md, correlations.json, plan.json
```

### Código
Um arquivo por etapa do pipeline, lidos nesta ordem:
```
src/harjmx/
├── har_reader.py        # ETAPA 1: lê o HAR (Entry com ID fixo), filtra e casa "POST /login" → IDs
├── correlations.py      # ETAPA 1: valor da resposta X reaparece na requisição Y
├── catalog.py           # ETAPA 1: catalog.md, o que a IA lê (sem valores)
├── plan.py              # ETAPA 2: contrato do plan.json + validação com erros acionáveis
├── jmx_builder.py       # ETAPA 3: hidratação (${var}) → nomes → estrutura → XML
├── jmx_templates.xml.j2 # XML de cada elemento JMeter (edite aqui para mudar o estilo)
├── commands.py          # comandos prepare | show | match | validate | build + pasta do script
└── jsonpath.py          # utilitário de JSONPath usado pelas etapas
```

## Testes
```powershell
python -m pytest            # unitários + golden do JMX (< 1 s)
python -m pytest -m e2e     # roda o JMX no JMeter real contra um mock local (~20 s, precisa do JMeter)
```
O teste e2e sobe [tests/mock_server.py](tests/mock_server.py). Esse mock gera CSRF, token e
IDs novos a cada chamada, então o JMX só passa se as correlações estiverem certas. Para
atualizar o golden depois de uma mudança intencional, rode `UPDATE_GOLDEN=1 python -m pytest`.
