# Guia rápido

## 1. Instalar (uma vez)
```powershell
python -m pip install -e "C:\.code\Agente_JMeter"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\gerar-jmeter" -Target "C:\.code\Agente_JMeter\.claude\skills\gerar-jmeter"
```
Depois disso a skill funciona em qualquer pasta, no Claude Code do terminal, do VS Code e na aba Code do app.
Não funciona no chat normal nem no Cowork.

## 2. Gerar o script
**Claude Code**
```
/gerar-jmeter jornadas/hars/teste.har "login, ver produto, adicionar à sacola"
```

**Codex**
```
Siga C:\.code\Agente_JMeter\.claude\skills\gerar-jmeter\SKILL.md com o HAR jornadas/hars/teste.har
e a jornada "login, ver produto, adicionar à sacola".
```

A jornada pode ser texto livre (`"login, ver produto"`), endpoints (`"POST /login, GET */products/*"`) ou os dois.

Para salvar em outro lugar, passe a pasta no fim (relativa, absoluta ou com `~`):
```
/gerar-jmeter jornadas/hars/teste.har "login, ver produto" ~/jmeter
```

Para ajustar, peça na mesma conversa: *"tira a requisição do datalayer"*, *"renomeia a etapa 2 para Sacola"*.

## 3. Resultado
Em `jornadas/<proj>/` da pasta onde você abriu o Claude (ou `<pasta informada>/<proj>/`):
```
<proj>/
├── <nome>.jmx      # abra no JMeter
├── massa.csv       # login/senha
└── _geracao/       # arquivos internos da geração
```

## 4. Cuidados
- **HAR com conteúdo:** no DevTools, use "Preserve log", "Disable cache" e "Save all as HAR with content".
- **Ambiente:** o `.jmx` aponta para o host do HAR, muitas vezes **produção**. Troque `host` em *User Defined Variables*.
- **Não edite o `.jmx` à mão:** peça o ajuste ao agente.
