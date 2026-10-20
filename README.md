# Agente JMeter

Gera scripts JMeter (`.jmx`) a partir de um `.har` e de uma descrição da jornada.

O **Python** faz tudo que é determinístico:
- filtrar o HAR;
- detectar correlações;
- aplicar a nomenclatura das boas práticas;
- montar o XML.

O **Claude Code**, pela skill `/gerar-jmeter`, decide a jornada:
- quais requisições entram e em que ordem;
- o nome de cada etapa;
- que valores viram variáveis.

A IA nunca copia dados do HAR. Ela escreve um `plan.json` que referencia as requisições por
ID, e o Python busca headers, bodies e credenciais reais direto no HAR.

```
HAR ──prepare──> catalog.md ──(Claude Code)──> plan.json ──validate──> build ──> .jmx + massa.csv
```

## Instalação
```
python -m pip install -e ".[dev]"
```

## Uso com o Claude Code
Abra o repositório no Claude Code e rode:
```
/gerar-jmeter caminho/do/arquivo.har "login, buscar produto, adicionar à sacola"
```
A jornada pode ser texto livre, endpoints explícitos (`POST /login, GET */products/*`) ou os
dois. O agente segue o roteiro de [.claude/skills/gerar-jmeter/SKILL.md](.claude/skills/gerar-jmeter/SKILL.md)
e entrega `work/<projeto>/<nome>.jmx`.

**Grave o HAR com conteúdo.** Use "Preserve log", "Disable cache" e "Save all as HAR with
content". Sem os corpos das respostas, as correlações que saem delas não são detectadas.
Mais detalhes em [antigo/docs/HAR_COM_COOKIES.md](antigo/docs/HAR_COM_COOKIES.md).

## Uso manual (CLI)
| Comando | O que faz |
|---|---|
| `python -m harjmx prepare <har> --out work/<proj> [--host dominio] [--match "POST /login"]` | Filtra o HAR e gera `catalog.md`, `catalog.json` e `correlations.json` |
| `python -m harjmx show work/<proj> <id>...` | Mostra requisição e resposta completas, **com valores reais** |
| `python -m harjmx match work/<proj> "POST /login"` | Casa endpoints com IDs |
| `python -m harjmx validate work/<proj>` | Confere o `plan.json` contra o HAR |
| `python -m harjmx build work/<proj>` | Valida e gera o `.jmx` (mais o `massa.csv`, se houver variáveis de CSV) |

O formato do `plan.json` está em [src/harjmx/plan/schema.py](src/harjmx/plan/schema.py). Há um
exemplo completo em [.claude/skills/gerar-jmeter/plan.example.json](.claude/skills/gerar-jmeter/plan.example.json).

## O que o JMX gerado tem
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
[config/defaults.toml](config/defaults.toml). Os domínios de telemetria descartados ficam em
[config/denylist.txt](config/denylist.txt).

## Estrutura
```
src/harjmx/
├── cli.py                 # prepare | show | match | validate | build
├── models.py              # Entry, Body, Response
├── workspace.py           # pasta work/<proj> e session.json
├── jsonpath.py            # subconjunto de JSONPath usado pelo JMeter
├── correlation.py         # valor de resposta X reaparece na requisição Y
├── har/
│   ├── loader.py          # HAR → Entry com ID estável
│   ├── filters.py         # estáticos, terceiros, denylist, preflight
│   ├── matcher.py         # "POST /login" → IDs
│   └── catalog.py         # catalog.md (o que a IA lê, sem valores)
├── plan/
│   ├── schema.py          # contrato do plan.json
│   └── validate.py        # erros acionáveis e resolução das variáveis
└── jmx/
    ├── hydrate.py         # plano + HAR → samplers com ${var}
    ├── naming.py          # nomenclatura das boas práticas
    ├── structure.py       # requisição solta / controller / agrupado
    ├── builder.py         # renderiza templates e grava o XML
    └── templates/*.xml.j2 # XML de cada elemento JMeter
```

## Testes
```
python -m pytest            # unitários + golden do JMX
python -m pytest -m e2e     # roda o JMX no JMeter real contra um mock local (precisa do JMeter)
```
O teste e2e sobe [tests/mock_server.py](tests/mock_server.py). Esse mock gera CSRF, token e
IDs novos a cada chamada, então o JMX só passa se as correlações estiverem certas. Para
atualizar o golden depois de uma mudança intencional, rode `UPDATE_GOLDEN=1 python -m pytest`.
