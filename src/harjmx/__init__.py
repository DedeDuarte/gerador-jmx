"""HAR → JMeter. O Claude Code só diz qual requisição é cada passo (plan.json); o Python faz o resto.

Pipeline, um módulo por etapa:
  har_reader.py   ETAPA 1  lê o HAR, filtra e casa endpoints
  catalog.py      ETAPA 1  catálogo por ações do usuário, que a IA lê
  plan.py         ETAPA 2  contrato do plan.json + validação + relatório
  correlations.py ETAPA 3  correlações, dependências, login e CSV resolvidos contra o plano
  jmx_builder.py  ETAPA 3  hidrata a sequência com o HAR e gera o XML (jmx_templates/)
  commands.py              comandos prepare | show | match | validate | build
  jsonpath.py              utilitário de JSONPath usado pelas etapas
"""
