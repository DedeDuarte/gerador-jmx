import json
import os
import unicodedata
from typing import Any

from openai import OpenAI

# Gateway DRYVN (Inmetrics) — OpenAI-compatible
_DRYVN_BASE_URL = "https://api.dryvn.inmetrics.io/v2"

# Máximo de chars para serializar o body de uma response antes de enviar à IA.
# Evita gastar tokens com payloads irrelevantes (ex: datalayer do GTM).
_RESPONSE_TRUNCATE_CHARS = 500

_SYSTEM_PROMPT = """\
Você é um especialista em JMeter e testes de carga. Receberá uma lista de requests HTTP \
capturadas de um navegador (via HAR) e deverá transformá-las em objetos JSON prontos \
para geração de um plano JMeter.

Regras:
1. Substitua valores literais que variam por usuário (CPF, senha, email, nome, IDs \
   gerados dinamicamente) por variáveis JMeter no formato ${nome_variavel}.
2. Params de infraestrutura conhecidos (valores fixos — mantenha literal):
   - "pushSite", "realmId"
   Params de sessão (gerados dinamicamente — devem virar dependência):
   - "_dynSessConf": extrair da response da request que o gerou (registre em "dependencies").
3. Se um valor presente em query/body/path de uma request aparecer na response de outra, \
   registre em "dependencies" com source_request = índice 0-based da request de origem.
4. Valores retornados em responses que serão usados em requests posteriores devem ir em \
   "extractors" da request que os retorna.
5. "name" segue o padrão: "{id}.0 - {ação em português} - {METHOD} {endpoint_curto}"
   Onde endpoint_curto é apenas o último segmento significativo do path.
6. "include": true por padrão. false apenas para requests sem valor de teste \
   (ex: health checks, telemetria interna, analytics).
7. "csv_candidates": campos de body/query que variam por usuário e devem vir de CSV.
8. Preencha o objeto raiz "test_data" com o valor REAL observado no HAR para CADA \
   variável que apareça em "csv_candidates" de qualquer request, ou como variável \
   "órfã" em "variables_used" (sem extractor/dependency que a origine — também acaba \
   indo para o CSV). Formato: {"<variavel>": {"value": "<string>", "sensitive": <bool>}}.
   - "value" DEVE ser o valor literal exatamente como capturado no HAR (o mesmo dado \
     que o usuário usou para reproduzir o fluxo, ex: o email e a senha realmente \
     digitados no login). NÃO substitua por um dado fictício/genérico — o objetivo é \
     reproduzir o fluxo exatamente como foi gravado.
   - "sensitive": true para dados PESSOAIS (login, senha, email, CPF, CNPJ, telefone, \
     endereço, cartão de crédito, token, etc.) — apenas para sinalizar que são dados \
     de teste pessoais, sem alterar o valor. "sensitive": false para dados de negócio \
     (productId, cupom, postalCode, shippingMethod, etc.).
9. "group": rótulo curto (em português, maiúsculas, ex: "LOGIN", "PRODUTOS", "CHECKOUT", \
   "CADASTRO") da etapa lógica à qual a request pertence. Requests consecutivas com o \
   mesmo "group" são agrupadas visualmente em um Controller no JMeter. Use o mesmo rótulo \
   para todas as requests de uma mesma etapa e troque de rótulo apenas quando o fluxo \
   mudar de etapa. Se não houver agrupamento relevante, use null.
10. Analise o fluxo como um todo e preencha o objeto raiz "auth" com o mecanismo de \
   autenticação detectado:
   - "custom_login": autenticação via request de login (usuário/senha) que gera sessão \
     via cookies e/ou token de sessão dinâmico (ex: "_dynSessConf"). Preencha "login_group" \
     com o valor de "group" usado nas requests que fazem o login, para que essas requests \
     sejam executadas apenas uma vez por usuário virtual (mesmo com múltiplos loops).
   - "bearer": as requests enviam um header "Authorization: Bearer <token>" (estático ou \
     variável extraída). Preencha "header_name" (normalmente "Authorization") e \
     "token_variable" (ex: "${accessToken}").
   - "basic": HTTP Basic Auth (usuário/senha). Preencha "username_variable" e \
     "password_variable".
   - "none": quando não há autenticação identificável no fluxo.
11. Se um OBJETIVO for fornecido pelo usuário, use-o para decidir quais requests são \
   relevantes: inclua no array de saída APENAS as requests relacionadas ao objetivo \
   (descarte completamente as demais, não apenas marque include=false). Se nenhum \
   objetivo for fornecido, inclua todas as requests normalmente.
12. Retorne APENAS um JSON com as chaves "requests" (array), "auth" (objeto) e \
   "test_data" (objeto). Nenhum texto extra.\
"""

_SCHEMA_EXAMPLE = {
    "requests": [
        {
            "id": 1,
            "name": "1.0 - Realizar login - POST /ProfileActor/login",
            "description": "Autentica o usuário e inicia a sessão.",
            "include": True,
            "group": "LOGIN",
            "method": "POST",
            "endpoint": "/rest/model/atg/userprofiling/ProfileActor/login",
            "headers": {},
            "parameters": {
                "query": {"_dynSessConf": "${dynSessConf}", "pushSite": "rennerBrasilDesktop"},
                "path": {}
            },
            "body": {"login": "${login}", "password": "${password}"},
            "variables_used": ["dynSessConf", "login", "password"],
            "extractors": [
                {"variable": "token", "type": "json", "path": "$.token"}
            ],
            "dependencies": [
                {"variable": "dynSessConf", "source_request": 0, "location": "query._dynSessConf"}
            ],
            "csv_candidates": ["login", "password"],
            "assertions": [
                {"type": "status_code", "expected": 200}
            ]
        }
    ],
    "auth": {
        "type": "custom_login",
        "login_group": "LOGIN"
    },
    "test_data": {
        "login": {"value": "andre@mail.com", "sensitive": True},
        "password": {"value": "1234", "sensitive": True}
    }
}


# Substrings específicas o suficiente para não gerar falso-positivo em
# variáveis de negócio comuns (ex: "userAgent", "productName", "companyId").
# Usadas apenas para SINALIZAR "sensitive": true (dado pessoal) quando a IA
# não classificar — o valor em si nunca é alterado, sempre reflete o dado
# real do HAR.
_SENSITIVE_NAME_HINTS = (
    "senha", "password", "pwd",
    "email", "mail",
    "cpf", "cnpj",
    "usuario", "username", "login",
    "telefone", "phone", "celular",
    "cartao", "cardnumber", "cvv",
    "token", "secret",
    "endereco", "address", "cep", "postalcode",
    "firstname", "lastname", "sobrenome", "primeironome",
)


def _normalize(name: str) -> str:
    """Remove acentos e coloca em minúsculas, para que hints ASCII (ex:
    "endereco") também batam com variáveis acentuadas (ex: "endereço_bairro")."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _looks_sensitive(var_name: str) -> bool:
    """Heurística por nome de variável usada apenas para sinalizar
    "sensitive": true quando a IA não classificar — não afeta o valor."""
    name = _normalize(var_name)
    return any(hint in name for hint in _SENSITIVE_NAME_HINTS)


def _truncate_response(entry: dict) -> dict:
    """Return a copy of the entry with the response body truncated if too large."""
    if "response" not in entry:
        return entry
    serialized = json.dumps(entry["response"], ensure_ascii=False)
    if len(serialized) <= _RESPONSE_TRUNCATE_CHARS:
        return entry
    copy = dict(entry)
    copy["response"] = serialized[:_RESPONSE_TRUNCATE_CHARS] + "...[truncated]"
    return copy


def analyze(entries: list[dict], model: str = "claude4-6-sonnet-caveman", goal: str | None = None) -> dict:
    """
    Send parsed HAR entries to the DRYVN Gateway and return a dict with:
        {"requests": [...], "auth": {...}, "test_data": {...}}

    "test_data" traz o valor REAL capturado do HAR para cada variável que
    acaba indo para o CSV final (csv_candidates + variáveis órfãs), no
    formato {"<variavel>": {"value": str, "sensitive": bool}}. "sensitive"
    apenas sinaliza dados pessoais (login, senha, email, etc.) — o valor não
    é mascarado, sempre reflete o dado usado na captura do fluxo.

    Requires the DRYVN_API_KEY environment variable to be set.
    Default model "claude4-6-sonnet-caveman" resolves to Claude Sonnet 4.6.

    goal: descrição em linguagem natural do que o usuário quer testar (ex: "login e
    validação de perfil"). Quando fornecido, a IA descarta requests não relacionadas
    ao objetivo, mantendo apenas o fluxo relevante.
    """
    api_key = os.environ.get("DRYVN_API_KEY")
    if not api_key:
        raise EnvironmentError("DRYVN_API_KEY environment variable is not set.")

    client = OpenAI(api_key=api_key, base_url=_DRYVN_BASE_URL)

    # Pré-processamento: truncar responses grandes antes de serializar
    preprocessed = [_truncate_response(e) for e in entries]

    # Monta o user prompt com o objetivo (se houver), schema de exemplo e os dados
    goal_section = f"OBJETIVO definido pelo usuário: {goal}\n\n" if goal else ""
    user_prompt = (
        goal_section
        + "Schema de exemplo de um objeto da saída:\n"
        + json.dumps(_SCHEMA_EXAMPLE, ensure_ascii=False, indent=2)
        + "\n\nRequests capturadas do HAR (já filtradas):\n"
        + json.dumps(preprocessed, ensure_ascii=False)
    )

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = response.choices[0].message.content
    data: Any = json.loads(raw)

    # Validação básica do output
    if not isinstance(data, dict) or "requests" not in data:
        raise ValueError(f"IA retornou JSON sem a chave 'requests'. Raw: {raw[:200]}")
    requests = data["requests"]
    if not isinstance(requests, list):
        raise ValueError(f"'requests' não é uma lista. Raw: {raw[:200]}")
    for i, item in enumerate(requests):
        if not isinstance(item, dict):
            raise ValueError(f"Item {i} não é um objeto. Raw: {raw[:200]}")
        for required in ("id", "method", "endpoint"):
            if required not in item:
                raise ValueError(f"Item {i} não tem campo obrigatório '{required}'.")

    auth = data.get("auth")
    if not isinstance(auth, dict):
        auth = {"type": "none"}

    test_data = data.get("test_data")
    if not isinstance(test_data, dict):
        test_data = {}

    # Sanitiza cada entrada: garante {"value": str, "sensitive": bool}. O
    # valor é mantido exatamente como a IA retornou (o dado real do HAR);
    # "sensitive" apenas sinaliza dado pessoal, complementando a classificação
    # da IA com uma heurística por nome de variável quando ela não marcar.
    sanitized_test_data: dict = {}
    for var_name, entry in test_data.items():
        raw_value = entry.get("value", "") if isinstance(entry, dict) else entry
        ai_sensitive = bool(entry.get("sensitive", False)) if isinstance(entry, dict) else False
        sensitive = ai_sensitive or _looks_sensitive(var_name)
        sanitized_test_data[var_name] = {"value": str(raw_value), "sensitive": sensitive}

    return {"requests": requests, "auth": auth, "test_data": sanitized_test_data}
