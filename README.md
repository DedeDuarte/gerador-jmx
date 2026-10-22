# Agente JMeter

Gera scripts JMeter (`.jmx`) a partir de um `.har` e de uma descrição da jornada
("login, ver produto, adicionar à sacola").

## Como rodar
Instala o pacote e liga a skill em qualquer pasta (ajuste o caminho do repo):
```powershell
python -m pip install -e "C:\.code\Agente_JMeter"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\gerar-jmeter" -Target "C:\.code\Agente_JMeter\.claude\skills\gerar-jmeter"
```
Para gerar um script pelo Claude Code ou Codex, veja o **[GUIA.md](docs/GUIA.md)**.

## Como funciona
A **IA** só faz uma coisa: lê o catálogo (requisições agrupadas em ações do usuário, com horário
e página) e diz qual requisição é a principal de cada passo pedido, na ordem pedida:

```json
{"nome_teste": "renner_sacola", "etapas": [{"nome": "Ver Jaquetas", "requisicoes": [50]}]}
```

O **Python** faz todo o resto, sem a IA:
- filtra o HAR e monta o catálogo por ações;
- resolve as correlações contra o plano: a origem de cada valor é a resposta **mais próxima
  antes** do uso;
- inclui sozinho as requisições de que os passos dependem (a origem de um valor que não está no
  plano, e o login) e explica cada uma no relatório;
- manda para o CSV o que o usuário digitou (login, senha, CEP, cupom...);
- põe uma asserção do status em cada requisição, aplica a nomenclatura e monta o XML;
- reporta os riscos: valores que ficaram fixos, ações que acumulam estado, anti-bot sem cookies.

```
HAR ──prepare──> catálogo ──(IA)──> plan.json ──build──> .jmx + massa.csv + relatório
```

Os detalhes, com diagramas e uma execução real passo a passo, estão em
**[docs/ARQUITETURA.md](docs/ARQUITETURA.md)**.

### Comandos
| Comando | O que faz |
|---|---|
| `prepare <har> --out jornadas/<proj> [--host dominio] [--match "POST /login"]` | Filtra o HAR, grava e imprime o catálogo por ações |
| `show jornadas/<proj> <id>... [--redact]` | Mostra requisição e resposta completas (`--redact` mascara senhas e tokens) |
| `match jornadas/<proj> "POST /login"` | Casa endpoints com IDs; mostra também as requisições excluídas pelos filtros |
| `validate jornadas/<proj>` | Confere o `plan.json` e mostra o relatório, sem gerar arquivos |
| `build jornadas/<proj>` | Valida, gera o `.jmx` (mais o `massa.csv`) e mostra o relatório com a árvore |

O formato do `plan.json` está em [src/harjmx/plan.py](src/harjmx/plan.py). Há um exemplo
completo em [.claude/skills/gerar-jmeter/plan.example.json](.claude/skills/gerar-jmeter/plan.example.json).

### O que o JMX gerado tem
- **Nomes e agrupamento** conforme `boas-praticas_jmeter.md`:
  - requisições como `1.0 POST - /api/login - Autenticar Usuário`;
  - Simple Controller `N - Nome` para etapas com vários passos (o passo pedido e o que ele precisa);
  - extractors `JSON Extractor - var`, com default `var_ERR`;
  - `Assertion - Status 200` em cada requisição.
- **Esqueleto** igual ao `renner_sap_uy.jmx`:
  - Thread Group por propriedades (`-JVUs`, `-JRampup`, `-Jduration`);
  - User Defined Variables com os hosts;
  - Cookie Manager e Cache Manager;
  - Header Manager global com os headers comuns e um por sampler só com o que difere;
  - think time.
- **Correlações** automáticas: JSON Extractor, ou Regex em body ou headers.
- **Massa de dados** em CSV (login, senha, CEP, cupom…) com os valores reais do HAR.
- **Cookies anti-bot** que o navegador já tinha, pré-carregados no Cookie Manager.

Os padrões (thread group, think time, headers descartados) ficam em
[config/defaults.yaml](config/defaults.yaml). Os domínios de telemetria descartados ficam em
[config/denylist.txt](config/denylist.txt).

## Estrutura

### Jornadas
A skill cria `jornadas/` na pasta onde o Claude foi aberto (aqui no repo, fora do git: HARs e
scripts têm credenciais e cookies). Aceita outro destino (`/gerar-jmeter <har> "<jornada>" ~/jmeter`):
```
jornadas/
├── hars/                    # HARs mais usados (opcional: o HAR pode vir de qualquer lugar)
└── <proj>/                  # uma pasta por script gerado
    ├── <nome_teste>.jmx     # o que vai para o JMeter
    ├── massa.csv            # precisa ficar ao lado do .jmx
    └── _geracao/            # resíduos da geração: session.json, catalog.md, plan.json
```

### Código
Um arquivo por etapa do pipeline, lidos nesta ordem:
```
src/harjmx/
├── har_reader.py        # ETAPA 1: lê o HAR (Entry com ID fixo), filtra e casa "POST /login" → IDs
├── catalog.py           # ETAPA 1: catálogo por ações do usuário, o que a IA lê (sem valores)
├── plan.py              # ETAPA 2: contrato do plan.json (passos → IDs), validação e relatório
├── correlations.py      # ETAPA 3: correlações, dependências, login e CSV resolvidos contra o plano
├── jmx_builder.py       # ETAPA 3: hidratação (${var}) → nomes → estrutura → XML
├── jmx_templates/       # um .xml por elemento JMeter (edite aqui para mudar o estilo)
├── commands.py          # comandos prepare | show | match | validate | build + pasta do script
└── jsonpath.py          # utilitário de JSONPath usado pelas etapas
```

## Testes
```powershell
python -m pip install -e ".[dev]"   # uma vez: instala o pytest
python -m pytest            # unitários + golden do JMX (< 1 s)
python -m pytest -m e2e     # roda o JMX no JMeter real contra um mock local (~20 s, precisa do JMeter)
```
O teste e2e sobe [tests/mock_server.py](tests/mock_server.py). Esse mock gera CSRF, token e
IDs novos a cada chamada, então o JMX só passa se as correlações estiverem certas. Para
atualizar o golden depois de uma mudança intencional, rode `UPDATE_GOLDEN=1 python -m pytest`.
