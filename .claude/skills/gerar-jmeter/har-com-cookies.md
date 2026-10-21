# Como Gravar HAR com Cookies para Bypass de WAF/Anti-Bot

## Problema

Sites com proteção WAF/anti-bot (Radware, Cloudflare, Akamai) exigem:
- **Cookies de sessão** válidos (`_abck`, `bm_sz`, `ak_bmsc`, etc.)
- **Headers de navegador** realistas (User-Agent, sec-ch-ua*, Sec-Fetch-*)
- **Fingerprint consistente** do navegador

Se o HAR for gravado sem cookies, o JMeter receberá redirects HTTP 302 da proteção anti-bot.

## Solução: Gravar HAR com Cookies no Chrome

### Passo 1: Abrir Chrome DevTools

1. Abra o **Google Chrome** (ou Edge/Brave baseados em Chromium)
2. Pressione **F12** ou **Ctrl+Shift+I** para abrir DevTools
3. Navegue para a aba **Network**

### Passo 2: Configurar Captura de HAR

**ANTES de navegar para o site**, configure:

1. ✅ **Preserve log** - Mantém os logs entre navegações
2. ✅ **Disable cache** - Força requisições frescas
3. ⚠️ **NÃO use modo anônimo** - O modo anônimo pode não gravar cookies corretamente

### Passo 3: Navegar pelo Fluxo

1. **Limpe os cookies do site** (opcional, para captura limpa):
   - `chrome://settings/cookies` → Buscar o site → Remover
   
2. **Navegue pelo fluxo completo**:
   - Login → Produtos → Carrinho → Checkout → etc.
   
3. **Aguarde 5-10 segundos entre ações**:
   - Cookies anti-bot podem ser settados via JavaScript assíncrono
   - Dê tempo para o WAF validar a sessão

### Passo 4: Exportar HAR com Cookies

1. **Clique direito** na grid da aba Network
2. Selecione **"Save all as HAR with content"**
3. Salve em `jornadas/hars/nome_do_fluxo.har`

### Passo 5: Verificar se o HAR Contém Cookies

Abra o HAR em um editor de texto e procure:

```json
"request": {
  "method": "GET",
  "url": "https://www.site.com/api/...",
  "cookies": [
    {
      "name": "_abck",
      "value": "ABC123...",
      "domain": ".site.com"
    }
  ]
}
```

**✅ BOM**: `"cookies": [...]` com itens  
**❌ RUIM**: `"cookies": []` vazio

## Passo 6: Gerar o JMeter com o agente

```
/gerar-jmeter fluxo_com_cookies.har "login, ver produto"
```

O build:
1. ✅ Pré-carrega no `HTTP Cookie Manager` os cookies que o navegador já tinha antes da jornada
   (`cookies.pre_popular` em `config/defaults.yaml`). Os recebidos via Set-Cookie durante a
   jornada ficam com o Cookie Manager.
2. ✅ Põe os headers iguais em todas as requisições (User-Agent, sec-ch-ua*, Sec-Fetch-*) no
   `HTTP Header Manager - Global`.

## Verificação do JMX Gerado

Abra o `.jmx` em `jornadas/<proj>/` e procure:

### 1. CookieManager com Cookies Iniciais

```xml
<CookieManager testname="HTTP Cookie Manager">
  <collectionProp name="CookieManager.cookies">
    <elementProp name="_abck" elementType="Cookie">
      <stringProp name="Cookie.value">ABC123...</stringProp>
      <stringProp name="Cookie.domain">.site.com</stringProp>
    </elementProp>
  </collectionProp>
</CookieManager>
```

### 2. HeaderManager com Headers de Navegador

```xml
<HeaderManager testname="HTTP Header Manager - Global">
  <collectionProp name="HeaderManager.headers">
    <elementProp name="" elementType="Header">
      <stringProp name="Header.name">User-Agent</stringProp>
      <stringProp name="Header.value">Mozilla/5.0 ...</stringProp>
    </elementProp>
    <elementProp name="" elementType="Header">
      <stringProp name="Header.name">sec-ch-ua</stringProp>
      <stringProp name="Header.value">"Chromium";v="152", ...</stringProp>
    </elementProp>
  </collectionProp>
</HeaderManager>
```

## Limitações e Considerações

### ⚠️ Validade dos Cookies

**Cookies anti-bot geralmente expiram em 15-60 minutos.**

**Soluções:**
1. **Re-gravar o HAR** antes de cada execução de teste
2. **Implementar login dinâmico** - Fazer o JMeter executar login real para obter cookies novos
3. **Usar múltiplos HARs/contas** - Rotação de sessões

### ⚠️ Vinculação de IP

Alguns WAFs vinculam cookies ao IP de origem.

**Solução:**
- Execute o JMeter na mesma máquina/rede onde gravou o HAR
- Ou use ambiente de homologação sem WAF estrito

### ⚠️ Dados Sensíveis

**HAR files contêm:**
- Cookies de sessão (podem dar acesso à conta)
- Credenciais (login, senha, CPF)
- Dados pessoais (endereço, telefone)

**Boas práticas:**
1. ❌ **NÃO commitar HAR, JMX e CSV no Git** - No repo Agente_JMeter já estão no `.gitignore`; em outros repos, ignore `jornadas/`
2. ✅ Use **dados de teste fictícios** quando possível
3. ✅ Grave HAR em **ambiente de homologação** se disponível
4. ✅ **Delete HAR files** após gerar o JMX

## Troubleshooting

### Problema: Ainda recebo HTTP 302 mesmo com cookies no HAR

**Possíveis causas:**
1. Cookies expiraram → Re-grave o HAR
2. IP diferente → Execute JMeter no mesmo IP/rede
3. WAF detectou comportamento de bot → Adicione `Constant Timer` maior entre requests

### Problema: HAR gravado não tem cookies

**Soluções:**
1. Feche todas as abas do site antes de gravar
2. Abra DevTools ANTES de navegar
3. Marque "Preserve log" ANTES de navegar
4. Aguarde 10-15 segundos após cada ação importante (login, etc.)
5. Verifique se não está usando modo anônimo/incognito

## Exemplo Completo

```bash
# 1. Gravar HAR com cookies no Chrome (manual)
# 2. Verificar se HAR tem cookies
grep '"cookies": \[' jornadas/hars/meu_fluxo.har

# 3. Gerar o JMeter (no Claude Code)
/gerar-jmeter jornadas/hars/meu_fluxo.har "login, ver produto"

# 4. Executar teste
jmeter -n -t jornadas/meu_fluxo/meu_fluxo.jmx -l jornadas/meu_fluxo/_geracao/smoke.jtl
```
