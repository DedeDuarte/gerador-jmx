---
name: gerar-jmeter
description: Gera um script JMeter (.jmx) a partir de um arquivo .har e da descrição da jornada (texto livre e/ou lista de endpoints). Use quando o usuário pedir para criar, gerar ou montar um JMeter/jornada/script de carga a partir de um HAR.
argument-hint: <caminho do .har> <jornada: "login, buscar produto, checkout" e/ou "POST /login, GET /cart"> [destino: ./saida, ~/jmeter ou C:\jmeter]
model: sonnet
allowed-tools: Bash(python -m harjmx *) PowerShell(python -m harjmx *) Write
---

# Gerar JMeter a partir de um HAR

Seu único trabalho é **mapear cada passo pedido para o ID da requisição principal** no
catálogo. O Python faz todo o resto sozinho: correlações, requisições de que os passos dependem
(inclusive o login), massa de CSV, asserções, nomes e o XML. São 3 chamadas: `prepare`, escrever
o plano, `build`. Não investigue além disso.

Os comandos funcionam em qualquer pasta; rode na pasta atual. Se `python -m harjmx` não existir,
peça ao usuário para instalar o Agente_JMeter (`python -m pip install -e <pasta do Agente_JMeter>`).

## Entradas
- **HAR**: caminho do arquivo. Se vier só o nome, procure em `jornadas/hars/` da pasta atual. Se não vier, pergunte.
- **Jornada**: texto livre ("ver jaquetas, adicionar à sacola") e/ou endpoints ("POST /login",
  "GET */products/*"). Se não vier, pergunte.
- **Destino** (opcional): último argumento, depois da jornada entre aspas. Relativo à pasta atual
  (`./saida`), absoluto (`C:\jmeter`) ou com `~` (`~/jmeter`). Se não vier, use `jornadas` sem
  perguntar. Só pergunte se não der para separar jornada e destino.
- **Projeto**: decida **antes** do prepare. snake_case do nome do HAR; se o nome for genérico
  (`teste`, `gravacao`), derive da jornada (`jaqueta_camiseta_checkout`). Nunca `hars`. Não rode o
  prepare em pasta provisória nem apague pastas.
- **`<pasta>`** = `<destino>/<proj>`.

## 1. Preparar
```
python -m harjmx prepare "<har>" --out "<pasta>" [--match "POST /login" ...]
```
A saída **é o catálogo**: requisições agrupadas em ações do usuário
(`### A12 +60.1s — página /p/... → /c/camisetas`), com horário, página e, quando a ação leva a outra
página, o destino (`→`). Leia a saída inteira (sem `head`): ela termina em `Fim do catálogo: N ações`.
Não abra o arquivo de novo. Passe cada endpoint explícito do usuário em `--match`.
- Se o prepare disser que a pasta **já tem um script**, pergunte ao usuário se pode sobrescrever:
  com o ok, rode de novo com `--force`; senão, use outro nome.
- Se um passo depende de API/SSO de outro domínio (em "Terceiros excluídos"), rode de novo com `--host <dominio>`.
- Os `ATENÇÃO` do topo não interrompem o fluxo: gere o script e repasse o aviso no fim.

## 2. Escrever `<pasta>/_geracao/plan.json` (com a ferramenta Write)
```json
{"nome_teste": "renner_sacola_checkout",
 "etapas": [{"nome": "Ver Jaquetas", "requisicoes": [50]},
            {"nome": "Adicionar Jaqueta à Sacola", "requisicoes": [186]}]}
```
Só esses campos. Regras:
- **Uma etapa por passo pedido, exatamente na ordem pedida.** Nome = o passo do usuário, verbo no
  infinitivo, sem abreviação ("Ver Preço Total", "Adicionar Cupom"). Passo repetido: mesmo nome, com o
  contexto entre parênteses quando ele mudou ("Ver Preço Total (com Cupom)").
- **Uma requisição por etapa**, a principal. O `★` do catálogo marca a provável principal de cada
  ação: comece por ela e confirme com o passo.
  - passo de ação ("adicionar", "aplicar", "cadastrar"): o POST/PUT/DELETE que faz a ação
    (`addItemToOrder`, `claimCoupon`, `newAddress`);
  - passo de navegação ("ver", "abrir"): a requisição que traz o conteúdo da tela (listagem, dados
    do produto, itens da sacola). A navegação costuma estar na ação **anterior** à página, a que tem
    `→ <página>` no cabeçalho;
  - "ir para X" disparado por um POST (ex.: mover para o checkout): o POST;
  - tela montada por vários fragmentos (JSP/HTML): o fragmento com o conteúdo do passo (os itens,
    o total). Se o passo pede duas coisas da mesma tela (itens **e** total), até 2 IDs na etapa.
  Se o mesmo endpoint aparece mais de uma vez (`repete #38`), escolha a ocorrência do momento certo,
  não um prefetch anterior.
- **Não inclua** chamadas acessórias da tela (contadores, frete, parcelamento, estoque,
  recomendações, telemetria) nem passos não pedidos. O Python adiciona sozinho o que for
  necessário (origem de valores dinâmicos, login) e mostra no relatório.
- Se um passo pedido não existe no HAR, diga isso e peça uma gravação nova. **Nunca invente.**

## 3. Gerar
```
python -m harjmx build "<pasta>"
```
Valida, gera o `.jmx` e o `massa.csv` e imprime o relatório. Se der `ERRO`, corrija o plano e rode
de novo. Só use `python -m harjmx show "<pasta>" <id> --redact` ou
`python -m harjmx match "<pasta>" "* *cart*"` se precisar de algo que o catálogo não mostra.
**Nunca edite o .jmx à mão**: mude o plano e rode o build.

## 4. Reportar
Repasse o relatório do build (árvore, automáticas e por quê, correlações, CSV, riscos, caminhos). Acrescente:
- se a pasta atual for um repositório git e `jornadas/` não estiver no `.gitignore`, avise que o `.jmx`
  e o `massa.csv` têm credenciais e sugira ignorar a pasta;
- se houver anti-bot ou cookies, aponte [har-com-cookies.md](${CLAUDE_SKILL_DIR}/har-com-cookies.md);
- smoke test, num ambiente onde isso seja permitido:
  `jmeter -n -t "<pasta>/<nome>.jmx" -l "<pasta>/_geracao/smoke.jtl" -JVUs=1 -JRampup=1 -Jduration=30`
