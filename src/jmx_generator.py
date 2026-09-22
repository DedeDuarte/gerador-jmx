"""
Gera um plano JMeter (.jmx) a partir do JSON produzido por `agente.py --analyze`.

Formato de entrada esperado:
    {
        "host": "www.exemplo.com",
        "protocol": "https",
        "port": 443,          # opcional
        "auth": {             # opcional — mecanismo de autenticação do fluxo
            "type": "custom_login" | "bearer" | "basic" | "none",
            "login_group": "LOGIN",             # custom_login: group das requests de login
            "header_name": "Authorization",     # bearer
            "token_variable": "${accessToken}", # bearer
            "username_variable": "username",    # basic
            "password_variable": "password",    # basic
        },
        "test_data": {        # opcional — valores reais capturados do fluxo, para o CSV
            "login": {"value": "andre@mail.com", "sensitive": True},
            "password": {"value": "1234", "sensitive": True},
        },
        # Formato alternativo aceito no lugar de "test_data" (usado pelo JSON
        # salvo por `agente.py --analyze`, que envolve os valores numa seção
        # de metadados explícita):
        # "test_data_metadata": {"description": "...", "variables": {...}},
        "requests": [ {..., "group": "LOGIN"}, {...} ]
    }

Requests com o mesmo "group" (consecutivas) são agrupadas visualmente sob um
Controller no plano gerado. Quando auth.type == "custom_login", o group igual a
auth.login_group é agrupado sob um Once Only Controller (executa a
autenticação apenas uma vez por usuário virtual, mesmo com múltiplos loops).

Quando há variáveis destinadas ao CSV (csv_candidates / variáveis órfãs), o
`.csv` gerado ao lado do `.jmx` inclui, além do header, uma linha de dados
preenchida com "test_data" — os valores REAIS capturados no fluxo original
(não são mascarados), permitindo reproduzir o fluxo exatamente como lido
com 1 usuário virtual (--threads 1, default).

Usa apenas test elements nativos do JMeter (sem plugins) e apenas a stdlib do
Python (xml.etree.ElementTree) — sem dependências externas.
"""

import csv
import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

_PATH_PARAM_RE = re.compile(r"\{([^{}]+)\}")

_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# --------------------------------------------------------------------------- #
# Helpers de construção de XML
# --------------------------------------------------------------------------- #

def _el(tag: str, attrib: Optional[dict] = None, text: Optional[str] = None) -> ET.Element:
    e = ET.Element(tag, attrib or {})
    if text is not None:
        e.text = text
    return e


def _string_prop(name: str, value: str) -> ET.Element:
    return _el("stringProp", {"name": name}, value)


def _bool_prop(name: str, value: bool) -> ET.Element:
    return _el("boolProp", {"name": name}, "true" if value else "false")


def _int_prop(name: str, value: int) -> ET.Element:
    return _el("intProp", {"name": name}, str(value))


def _long_prop(name: str, value: int) -> ET.Element:
    return _el("longProp", {"name": name}, str(value))


def _hash_tree() -> ET.Element:
    return _el("hashTree")


# --------------------------------------------------------------------------- #
# Resolução de path params e coleta de variáveis
# --------------------------------------------------------------------------- #

def _resolve_endpoint(endpoint: str, path_params: dict) -> str:
    """Substitui {chave} no endpoint pelos valores de parameters.path."""
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        return str(path_params.get(key, match.group(0)))

    return _PATH_PARAM_RE.sub(_sub, endpoint)


def _collect_csv_variables(requests: list[dict]) -> list[str]:
    """
    União de csv_candidates de todas as requests + variables_used que não têm
    origem em extractors, dependencies ou csv_candidates de nenhuma request
    (variáveis "órfãs" também vêm do CSV).
    """
    csv_vars: list[str] = []
    seen = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            csv_vars.append(name)

    extracted_vars = set()
    for req in requests:
        for ex in req.get("extractors", []):
            extracted_vars.add(ex.get("variable"))

    dependency_vars = set()
    for req in requests:
        for dep in req.get("dependencies", []):
            dependency_vars.add(dep.get("variable"))

    for req in requests:
        for cand in req.get("csv_candidates", []):
            _add(cand)

    for req in requests:
        for var in req.get("variables_used", []):
            if var in extracted_vars or var in dependency_vars:
                continue
            _add(var)

    return csv_vars


# --------------------------------------------------------------------------- #
# Elementos JMeter
# --------------------------------------------------------------------------- #

def _build_test_plan() -> tuple[ET.Element, ET.Element]:
    tp = _el(
        "TestPlan",
        {
            "guiclass": "TestPlanGui",
            "testclass": "TestPlan",
            "testname": "Test Plan",
            "enabled": "true",
        },
    )
    tp.append(_string_prop("TestPlan.comments", ""))
    tp.append(_bool_prop("TestPlan.functional_mode", False))
    tp.append(_bool_prop("TestPlan.tearDown_on_shutdown", True))
    tp.append(_bool_prop("TestPlan.serialize_threadgroups", False))

    udv = _el(
        "elementProp",
        {
            "name": "TestPlan.user_defined_variables",
            "elementType": "Arguments",
            "guiclass": "ArgumentsPanel",
            "testclass": "Arguments",
            "testname": "User Defined Variables",
            "enabled": "true",
        },
    )
    udv.append(_el("collectionProp", {"name": "Arguments.arguments"}))
    tp.append(udv)
    tp.append(_string_prop("TestPlan.user_define_classpath", ""))
    return tp, _hash_tree()


def _build_thread_group(threads: int, loops: int, ramp_up: int) -> tuple[ET.Element, ET.Element]:
    tg = _el(
        "ThreadGroup",
        {
            "guiclass": "ThreadGroupGui",
            "testclass": "ThreadGroup",
            "testname": "Thread Group",
            "enabled": "true",
        },
    )
    tg.append(_string_prop("ThreadGroup.on_sample_error", "continue"))

    loop_ctrl = _el(
        "elementProp",
        {
            "name": "ThreadGroup.main_controller",
            "elementType": "LoopController",
            "guiclass": "LoopControlPanel",
            "testclass": "LoopController",
            "testname": "Loop Controller",
            "enabled": "true",
        },
    )
    loop_ctrl.append(_bool_prop("LoopController.continue_forever", False))
    loop_ctrl.append(_string_prop("LoopController.loops", str(loops)))
    tg.append(loop_ctrl)

    tg.append(_string_prop("ThreadGroup.num_threads", str(threads)))
    tg.append(_string_prop("ThreadGroup.ramp_time", str(ramp_up)))
    tg.append(_bool_prop("ThreadGroup.scheduler", False))
    tg.append(_string_prop("ThreadGroup.duration", ""))
    tg.append(_string_prop("ThreadGroup.delay", ""))
    tg.append(_bool_prop("ThreadGroup.same_user_on_next_iteration", True))

    return tg, _hash_tree()


def _build_http_defaults(host: str, protocol: str, port: Optional[int]) -> tuple[ET.Element, ET.Element]:
    defaults = _el(
        "ConfigTestElement",
        {
            "guiclass": "HttpDefaultsGui",
            "testclass": "ConfigTestElement",
            "testname": "HTTP Request Defaults",
            "enabled": "true",
        },
    )
    args = _el(
        "elementProp",
        {
            "name": "HTTPsampler.Arguments",
            "elementType": "Arguments",
            "guiclass": "HTTPArgumentsPanel",
            "testclass": "Arguments",
            "testname": "User Defined Variables",
            "enabled": "true",
        },
    )
    args.append(_el("collectionProp", {"name": "Arguments.arguments"}))
    defaults.append(args)
    defaults.append(_string_prop("HTTPSampler.domain", host))
    defaults.append(_string_prop("HTTPSampler.port", str(port) if port else ""))
    defaults.append(_string_prop("HTTPSampler.protocol", protocol))
    defaults.append(_string_prop("HTTPSampler.contentEncoding", ""))
    defaults.append(_string_prop("HTTPSampler.path", ""))
    defaults.append(_bool_prop("HTTPSampler.follow_redirects", True))
    defaults.append(_bool_prop("HTTPSampler.auto_redirects", False))
    defaults.append(_bool_prop("HTTPSampler.use_keepalive", True))
    defaults.append(_bool_prop("HTTPSampler.DO_MULTIPART_POST", False))

    return defaults, _hash_tree()


def _build_csv_config(csv_vars: list[str], csv_filename: str) -> tuple[ET.Element, ET.Element]:
    csv_config = _el(
        "CSVDataSet",
        {
            "guiclass": "TestBeanGUI",
            "testclass": "CSVDataSet",
            "testname": "CSV Data Set Config",
            "enabled": "true",
        },
    )
    csv_config.append(_string_prop("filename", csv_filename))
    csv_config.append(_string_prop("fileEncoding", "UTF-8"))
    csv_config.append(_string_prop("variableNames", ",".join(csv_vars)))
    csv_config.append(_string_prop("delimiter", ","))
    csv_config.append(_bool_prop("quotedData", False))
    csv_config.append(_bool_prop("recycle", True))
    csv_config.append(_bool_prop("stopThread", False))
    # Cada thread (usuário virtual) sorteia 1 linha e mantém para toda sua vida.
    csv_config.append(_string_prop("shareMode", "shareMode.thread"))
    # O .csv gerado sempre tem uma linha de header (nomes das variáveis)
    # seguida da linha de dados reais — sem ignoreFirstLine=true, o JMeter
    # usaria o default (false) e leria o HEADER como se fosse dado real
    # (ex: login="login", password="password"), quebrando o fluxo.
    csv_config.append(_bool_prop("ignoreFirstLine", True))

    return csv_config, _hash_tree()


def _build_cookie_manager(cookies: list[dict] = None) -> tuple[ET.Element, ET.Element]:
    """
    Build CookieManager with optional initial cookies from HAR file.
    
    Args:
        cookies: List of cookie dicts with name, value, domain, path, secure, expires
    """
    cm = _el(
        "CookieManager",
        {
            "guiclass": "CookiePanel",
            "testclass": "CookieManager",
            "testname": "HTTP Cookie Manager",
            "enabled": "true",
        },
    )
    
    cookies_coll = _el("collectionProp", {"name": "CookieManager.cookies"})
    
    # Add initial cookies if provided
    if cookies:
        for cookie in cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "")
            path = cookie.get("path", "/")
            secure = cookie.get("secure", False)
            expires = cookie.get("expires", 0)
            
            if not name:
                continue
            
            cookie_el = _el(
                "elementProp",
                {"name": name, "elementType": "Cookie", "testname": name},
            )
            cookie_el.append(_string_prop("Cookie.value", value))
            cookie_el.append(_string_prop("Cookie.domain", domain))
            cookie_el.append(_string_prop("Cookie.path", path))
            cookie_el.append(_bool_prop("Cookie.secure", secure))
            
            # Convert expires to long timestamp if present
            # If expires is None or 0, use 0 (session cookie)
            expires_val = 0
            if expires and isinstance(expires, (int, float)):
                expires_val = int(expires * 1000)  # Convert to milliseconds
            elif expires and isinstance(expires, str):
                # Try to parse ISO date string if needed
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    expires_val = int(dt.timestamp() * 1000)
                except:
                    expires_val = 0
            
            cookie_el.append(_long_prop("Cookie.expires", expires_val))
            
            cookies_coll.append(cookie_el)
    
    cm.append(cookies_coll)
    cm.append(_bool_prop("CookieManager.clearEachIteration", False))
    cm.append(_bool_prop("CookieManager.controlledByThreadGroup", False))
    return cm, _hash_tree()


def _build_auth_manager(auth: dict) -> tuple[ET.Element, ET.Element]:
    """HTTP Authorization Manager nativo do JMeter, para Basic Auth."""
    username = auth.get("username_variable", "username")
    password = auth.get("password_variable", "password")
    am = _el(
        "AuthManager",
        {
            "guiclass": "AuthPanel",
            "testclass": "AuthManager",
            "testname": "HTTP Authorization Manager",
            "enabled": "true",
        },
    )
    coll = _el("collectionProp", {"name": "AuthManager.auth_list"})
    auth_el = _el("elementProp", {"name": "", "elementType": "Authorization"})
    auth_el.append(_string_prop("Authorization.url", ""))
    auth_el.append(_string_prop("Authorization.username", f"${{{username}}}"))
    auth_el.append(_string_prop("Authorization.password", f"${{{password}}}"))
    auth_el.append(_string_prop("Authorization.domain", ""))
    auth_el.append(_string_prop("Authorization.realm", ""))
    coll.append(auth_el)
    am.append(coll)
    am.append(_bool_prop("AuthManager.clearEachIteration", False))
    return am, _hash_tree()


def _build_generic_controller(name: str) -> ET.Element:
    return _el(
        "GenericController",
        {
            "guiclass": "LogicControllerGui",
            "testclass": "GenericController",
            "testname": name,
            "enabled": "true",
        },
    )


def _build_once_only_controller(name: str) -> ET.Element:
    """
    Once Only Controller: executa as requests filhas apenas na primeira
    iteração de cada usuário virtual (thread), mesmo com múltiplos loops.
    Ideal para envolver o fluxo de autenticação customizada (login).
    """
    return _el(
        "OnceOnlyController",
        {
            "guiclass": "OnceOnlyControllerGui",
            "testclass": "OnceOnlyController",
            "testname": name,
            "enabled": "true",
        },
    )


def _build_bearer_header_manager(auth: dict) -> tuple[ET.Element, ET.Element]:
    """HeaderManager global com o header Authorization: Bearer <token>."""
    header_name = auth.get("header_name", "Authorization")
    token_variable = auth.get("token_variable", "${accessToken}")
    value = token_variable if token_variable.strip().lower().startswith(("bearer", "${")) else f"${{{token_variable}}}"
    if not value.strip().lower().startswith("bearer") and "${" in value:
        value = f"Bearer {value}"
    return _build_header_manager({header_name: value}, testname="Authorization Header Manager")


def _build_header_manager(headers: dict, testname: str = "HTTP Header Manager") -> tuple[ET.Element, ET.Element]:
    hm = _el(
        "HeaderManager",
        {
            "guiclass": "HeaderPanel",
            "testclass": "HeaderManager",
            "testname": testname,
            "enabled": "true",
        },
    )
    coll = _el("collectionProp", {"name": "HeaderManager.headers"})
    for k, v in headers.items():
        header_el = _el("elementProp", {"name": k, "elementType": "Header"})
        header_el.append(_string_prop("Header.name", k))
        header_el.append(_string_prop("Header.value", str(v)))
        coll.append(header_el)
    hm.append(coll)
    return hm, _hash_tree()


def _build_sampler(request: dict) -> ET.Element:
    method = request.get("method", "GET").upper()
    endpoint = _resolve_endpoint(
        request.get("endpoint", ""),
        request.get("parameters", {}).get("path", {}) or {},
    )
    query = request.get("parameters", {}).get("query", {}) or {}
    body = request.get("body")
    enabled = bool(request.get("include", True))
    name = request.get("name") or endpoint

    sampler = _el(
        "HTTPSamplerProxy",
        {
            "guiclass": "HttpTestSampleGui",
            "testclass": "HTTPSamplerProxy",
            "testname": name,
            "enabled": "true" if enabled else "false",
        },
    )

    args = _el(
        "elementProp",
        {
            "name": "HTTPsampler.Arguments",
            "elementType": "Arguments",
            "guiclass": "HTTPArgumentsPanel",
            "testclass": "Arguments",
            "testname": "User Defined Variables",
            "enabled": "true",
        },
    )
    coll = _el("collectionProp", {"name": "Arguments.arguments"})

    has_body = body not in (None, {}) and method in _BODY_METHODS
    if has_body:
        body_str = json.dumps(body, ensure_ascii=False)
        arg_el = _el("elementProp", {"name": "", "elementType": "HTTPArgument"})
        arg_el.append(_bool_prop("HTTPArgument.always_encode", False))
        arg_el.append(_string_prop("Argument.value", body_str))
        arg_el.append(_string_prop("Argument.metadata", "="))
        coll.append(arg_el)
    else:
        for k, v in query.items():
            arg_el = _el("elementProp", {"name": k, "elementType": "HTTPArgument"})
            arg_el.append(_bool_prop("HTTPArgument.always_encode", False))
            arg_el.append(_string_prop("Argument.name", k))
            arg_el.append(_string_prop("Argument.value", str(v)))
            arg_el.append(_string_prop("Argument.metadata", "="))
            arg_el.append(_bool_prop("HTTPArgument.use_equals", True))
            coll.append(arg_el)

    args.append(coll)
    sampler.append(args)

    sampler.append(_string_prop("HTTPSampler.domain", ""))
    sampler.append(_string_prop("HTTPSampler.port", ""))
    sampler.append(_string_prop("HTTPSampler.protocol", ""))
    sampler.append(_string_prop("HTTPSampler.contentEncoding", ""))
    sampler.append(_string_prop("HTTPSampler.path", endpoint))
    sampler.append(_string_prop("HTTPSampler.method", method))
    sampler.append(_bool_prop("HTTPSampler.follow_redirects", True))
    sampler.append(_bool_prop("HTTPSampler.auto_redirects", False))
    sampler.append(_bool_prop("HTTPSampler.use_keepalive", True))
    sampler.append(_bool_prop("HTTPSampler.DO_MULTIPART_POST", False))
    sampler.append(_bool_prop("HTTPSampler.postBodyRaw", has_body))

    # Se GET/DELETE sem body, mas houver query, ainda usamos os query args acima
    # (quando has_body=False já cobrimos "query" no bloco acima).
    if has_body and query:
        # Requests com body ainda podem ter query string — anexa como parâmetros
        # extras via HTTPSampler.path (query string manual), já que o corpo já
        # ocupa o único argument slot usado para postBodyRaw.
        # Valores são URL-encoded (preservando "${...}" como literal) para evitar
        # query strings malformadas quando um valor contém &, = ou espaço.
        qs = "&".join(
            f"{quote(str(k), safe='${}')}={quote(str(v), safe='${}')}"
            for k, v in query.items()
        )
        current_path = sampler.find("stringProp[@name='HTTPSampler.path']")
        if current_path is not None:
            current_path.text = f"{endpoint}?{qs}"

    return sampler


def _build_headers_for_request(request: dict, has_body: bool) -> dict:
    headers = dict(request.get("headers", {}) or {})
    if has_body:
        has_content_type = any(k.lower() == "content-type" for k in headers)
        if not has_content_type:
            headers["Content-Type"] = "application/json"
    return headers


def _build_json_extractor(extractor: dict) -> ET.Element:
    variable = extractor.get("variable", "")
    path = extractor.get("path", "")
    ext = _el(
        "JSONPostProcessor",
        {
            "guiclass": "JSONPostProcessorGui",
            "testclass": "JSONPostProcessor",
            "testname": f"JSON Extractor - {variable}",
            "enabled": "true",
        },
    )
    ext.append(_string_prop("JSONPostProcessor.referenceNames", variable))
    ext.append(_string_prop("JSONPostProcessor.jsonPathExprs", path))
    ext.append(_string_prop("JSONPostProcessor.match_numbers", "0"))
    ext.append(_string_prop("JSONPostProcessor.defaultValues", ""))
    return ext


def _build_status_code_assertion(expected: int) -> ET.Element:
    ra = _el(
        "ResponseAssertion",
        {
            "guiclass": "AssertionGui",
            "testclass": "ResponseAssertion",
            "testname": "Status Code Assertion",
            "enabled": "true",
        },
    )
    coll = _el("collectionProp", {"name": "Asserion.test_strings"})
    coll.append(_string_prop("49586", str(expected)))
    ra.append(coll)
    ra.append(_string_prop("Assertion.custom_message", ""))
    ra.append(_string_prop("Assertion.test_field", "Assertion.response_code"))
    ra.append(_bool_prop("Assertion.assume_success", False))
    ra.append(_int_prop("Assertion.test_type", 8))  # 8 = Equals
    return ra


def _groovy_path_access(path: str) -> str:
    """
    Converte um JSONPath simples "$.a.b.c" ou "$.a[0].b" em uma expressão de 
    acesso Groovy segura sobre um Map/List já parseado, usando ?. para não
    quebrar em caminhos ausentes.
    
    Exemplos:
      $.result[0].purchasable -> json?.get('result')?.getAt(0)?.get('purchasable')
      $.order.commerceItems[0].productId -> json?.get('order')?.get('commerceItems')?.getAt(0)?.get('productId')
    """
    parts = path.replace("$.", "").replace("$", "").split(".")
    parts = [p for p in parts if p]
    expr = "json"
    for p in parts:
        # Detecta array index: "result[0]" -> "result" + [0]
        if "[" in p and "]" in p:
            field = p[:p.index("[")]
            index = p[p.index("[")+1:p.index("]")]
            expr += f"?.get('{field}')?.getAt({index})"
        else:
            expr += f"?.get('{p}')"
    return expr


def _build_jsr223_assertion(request_assertions: list[dict]) -> Optional[ET.Element]:
    json_assertions = [
        a for a in request_assertions if a.get("type") in ("json_field", "json_field_not_null")
    ]
    if not json_assertions:
        return None

    lines = [
        "import groovy.json.JsonSlurper",
        "def json = new JsonSlurper().parseText(prev.getResponseDataAsString())",
        "def failures = []",
    ]
    for a in json_assertions:
        path = a.get("path", "")
        access = _groovy_path_access(path)
        if a["type"] == "json_field":
            expected = a.get("expected")
            expected_groovy = json.dumps(expected)
            lines.append(f"if ({access} != {expected_groovy}) {{")
            lines.append(
                f"    failures.add('Campo {path} esperado={expected_groovy} obtido=' + {access})"
            )
            lines.append("}")
        else:  # json_field_not_null
            lines.append(f"if ({access} == null) {{")
            lines.append(f"    failures.add('Campo {path} esperado != null, obtido null')")
            lines.append("}")

    lines.append("if (!failures.isEmpty()) {")
    lines.append("    AssertionResult.setFailure(true)")
    lines.append('    AssertionResult.setFailureMessage(failures.join("; "))')
    lines.append("}")

    script = "\n".join(lines)

    jsr = _el(
        "JSR223Assertion",
        {
            "guiclass": "TestBeanGUI",
            "testclass": "JSR223Assertion",
            "testname": "JSON Field Assertions (Groovy)",
            "enabled": "true",
        },
    )
    jsr.append(_string_prop("cacheKey", "true"))
    jsr.append(_string_prop("filename", ""))
    jsr.append(_string_prop("script", script))
    jsr.append(_string_prop("scriptLanguage", "groovy"))
    jsr.append(_string_prop("parameters", ""))
    return jsr


def _build_listener_view_results_tree() -> tuple[ET.Element, ET.Element]:
    listener = _el(
        "ResultCollector",
        {
            "guiclass": "ViewResultsFullVisualizer",
            "testclass": "ResultCollector",
            "testname": "View Results Tree",
            "enabled": "true",
        },
    )
    listener.append(_bool_prop("ResultCollector.error_logging", False))
    obj_prop = _el("objProp")
    obj_prop.append(_el("name", text="saveConfig"))
    value = _el(
        "value",
        {"class": "SampleSaveConfiguration"},
    )
    # Todos os campos são booleanos, exceto "assertionsResultsToSave", que é um
    # campo legado numérico (0 = nenhuma asserção específica salva por índice).
    bool_tags = (
        "time", "latency", "timestamp", "success", "label", "code", "message",
        "threadName", "dataType", "encoding", "assertions", "subresults",
        "responseData", "samplerData", "xml", "fieldNames", "responseHeaders",
        "requestHeaders", "responseDataOnError", "saveAssertionResultsFailureMessage",
        "bytes", "sentBytes", "url", "threadCounts", "idleTime", "connectTime",
    )
    for tag in bool_tags:
        value.append(_el(tag, text="true"))
    value.append(_el("assertionsResultsToSave", text="0"))
    obj_prop.append(value)
    listener.append(obj_prop)
    listener.append(_string_prop("filename", ""))
    return listener, _hash_tree()


def _build_listener_summary_report() -> tuple[ET.Element, ET.Element]:
    listener = _el(
        "ResultCollector",
        {
            "guiclass": "SummaryReport",
            "testclass": "ResultCollector",
            "testname": "Summary Report",
            "enabled": "true",
        },
    )
    listener.append(_bool_prop("ResultCollector.error_logging", False))
    listener.append(_string_prop("filename", ""))
    return listener, _hash_tree()


def _build_listener_aggregate_report() -> tuple[ET.Element, ET.Element]:
    listener = _el(
        "ResultCollector",
        {
            "guiclass": "StatVisualizer",
            "testclass": "ResultCollector",
            "testname": "Aggregate Report",
            "enabled": "true",
        },
    )
    listener.append(_bool_prop("ResultCollector.error_logging", False))
    listener.append(_string_prop("filename", ""))
    return listener, _hash_tree()


def _group_requests(requests: list[dict]) -> list[tuple[Optional[str], list[dict]]]:
    """
    Agrupa requests consecutivas que compartilham o mesmo campo "group" em
    blocos (group_name, [requests]). Requests sem "group" (None) formam seus
    próprios blocos avulsos (group_name=None), ficando direto na Thread Group.
    """
    groups: list[tuple[Optional[str], list[dict]]] = []
    for request in requests:
        group_name = request.get("group") or None
        if groups and groups[-1][0] == group_name and group_name is not None:
            groups[-1][1].append(request)
        else:
            groups.append((group_name, [request]))
    return groups


def _append_request(target_tree: ET.Element, request: dict) -> None:
    """Constrói o sampler de uma request e todos os seus filhos (headers,
    extractors, assertions) dentro de `target_tree`."""
    method = request.get("method", "GET").upper()
    has_body = request.get("body") not in (None, {}) and method in _BODY_METHODS

    sampler = _build_sampler(request)
    target_tree.append(sampler)
    sampler_tree = _hash_tree()
    target_tree.append(sampler_tree)

    headers = _build_headers_for_request(request, has_body)
    if headers:
        hm, hm_tree = _build_header_manager(headers)
        sampler_tree.append(hm)
        sampler_tree.append(hm_tree)

    for extractor in request.get("extractors", []):
        if extractor.get("type") == "json":
            ext = _build_json_extractor(extractor)
            sampler_tree.append(ext)
            sampler_tree.append(_hash_tree())

    assertions = request.get("assertions", [])
    for assertion in assertions:
        if assertion.get("type") == "status_code":
            ra = _build_status_code_assertion(assertion.get("expected", 200))
            sampler_tree.append(ra)
            sampler_tree.append(_hash_tree())

    jsr223 = _build_jsr223_assertion(assertions)
    if jsr223 is not None:
        sampler_tree.append(jsr223)
        sampler_tree.append(_hash_tree())


# --------------------------------------------------------------------------- #
# Função principal
# --------------------------------------------------------------------------- #

def generate(
    data: dict,
    output_path: str,
    threads: int = 1,
    loops: int = 1,
    ramp_up: int = 1,
) -> None:
    """
    Gera um arquivo .jmx a partir do JSON de entrada (formato
    {"host", "protocol", "port", "auth", "test_data", "requests": [...]}) e
    grava em `output_path`.

    Se houver variáveis a serem lidas de CSV (csv_candidates ou variables_used
    órfãs), gera também um `.csv` ao lado do `.jmx` com o header e uma linha de
    dados preenchida a partir de "test_data" — os valores reais capturados no
    fluxo original (ver módulo `ai_analyzer`). Se uma variável do CSV não
    tiver entrada em "test_data", a coluna é deixada em branco na linha de
    dados.
    """
    host = data.get("host", "")
    protocol = data.get("protocol", "https")
    port = data.get("port")
    requests: list[dict] = data.get("requests", [])
    # "test_data" pode vir solto (uso direto do módulo) ou dentro de
    # "test_data_metadata.variables" (formato salvo por agente.py --analyze).
    test_data: dict = data.get("test_data") or (data.get("test_data_metadata") or {}).get("variables") or {}
    # Cookies e browser headers extraídos do HAR para bypass de WAF/anti-bot
    cookies: list[dict] = data.get("cookies", [])
    browser_headers: dict = data.get("browser_headers", {})

    jmeter_test_plan = _el(
        "jmeterTestPlan",
        {"version": "1.2", "properties": "5.0", "jmeter": "5.6.3"},
    )
    root_tree = _hash_tree()
    jmeter_test_plan.append(root_tree)

    tp, tp_tree = _build_test_plan()
    root_tree.append(tp)
    root_tree.append(tp_tree)

    tg, tg_tree = _build_thread_group(threads, loops, ramp_up)
    tp_tree.append(tg)
    tp_tree.append(tg_tree)

    defaults, defaults_tree = _build_http_defaults(host, protocol, port)
    tg_tree.append(defaults)
    tg_tree.append(defaults_tree)

    cookie_manager, cookie_tree = _build_cookie_manager(cookies)
    tg_tree.append(cookie_manager)
    tg_tree.append(cookie_tree)
    
    # Add global browser headers if present (for anti-bot bypass)
    if browser_headers:
        browser_hm, browser_hm_tree = _build_header_manager(
            browser_headers,
            testname="Browser Headers (Anti-Bot)"
        )
        tg_tree.append(browser_hm)
        tg_tree.append(browser_hm_tree)

    auth: dict = data.get("auth") or {"type": "none"}
    auth_type = auth.get("type", "none")

    if auth_type == "basic":
        am, am_tree = _build_auth_manager(auth)
        tg_tree.append(am)
        tg_tree.append(am_tree)
    elif auth_type == "bearer":
        hm, hm_tree = _build_bearer_header_manager(auth)
        tg_tree.append(hm)
        tg_tree.append(hm_tree)

    csv_vars = _collect_csv_variables(requests)
    csv_path = None
    if csv_vars:
        base = os.path.splitext(output_path)[0]
        csv_path = f"{base}_data.csv"
        csv_filename_in_jmx = os.path.basename(csv_path)
        csv_config, csv_tree = _build_csv_config(csv_vars, csv_filename_in_jmx)
        tg_tree.append(csv_config)
        tg_tree.append(csv_tree)

    login_group = auth.get("login_group") if auth_type == "custom_login" else None

    for group_name, group_requests in _group_requests(requests):
        if group_name is None:
            target_tree = tg_tree
        else:
            if group_name == login_group:
                controller = _build_once_only_controller(group_name)
            else:
                controller = _build_generic_controller(group_name)
            controller_tree = _hash_tree()
            tg_tree.append(controller)
            tg_tree.append(controller_tree)
            target_tree = controller_tree

        for request in group_requests:
            _append_request(target_tree, request)

    for build_listener in (
        _build_listener_view_results_tree,
        _build_listener_summary_report,
        _build_listener_aggregate_report,
    ):
        listener, listener_tree = build_listener()
        tp_tree.append(listener)
        tp_tree.append(listener_tree)

    tree = ET.ElementTree(jmeter_test_plan)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)

    if csv_vars and csv_path:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(csv_vars)
            # Linha de dados: reproduz o fluxo lido com 1 usuário, usando os
            # valores REAIS de "test_data" (capturados do .har, sem
            # mascaramento). Coluna fica vazia se a variável não tiver
            # entrada em "test_data".
            data_row = [test_data.get(var, {}).get("value", "") for var in csv_vars]
            writer.writerow(data_row)
