"""HAR → JMeter. O Python faz o determinístico; o Claude Code decide a jornada (plan.json).

Pipeline, um módulo por etapa:
  har_reader.py   ETAPA 1  lê o HAR, filtra e casa endpoints
  correlations.py ETAPA 1  detecta valores dinâmicos (resposta X → requisição Y)
  catalog.py      ETAPA 1  escreve o catalog.md que a IA lê
  plan.py         ETAPA 2  contrato do plan.json + validação
  jmx_builder.py  ETAPA 3  hidrata o plano com o HAR e gera o XML (jmx_templates.xml.j2)
  commands.py              comandos prepare | show | match | validate | build
  jsonpath.py              utilitário de JSONPath usado pelas etapas
"""
