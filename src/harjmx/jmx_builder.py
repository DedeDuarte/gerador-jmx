"""ETAPA 3 — Gerar o JMX.

Plano validado + HAR → texto do .jmx e a linha do CSV de massa. Em quatro passos:
  1. Hidratação: cada requisição do plano vira um `Sampler` com os dados reais do HAR e os
     valores dinâmicos trocados por ${variavel}.
  2. Nomes: nomenclatura das boas práticas (C:\\.code\\Caderno\\Boas-praticas_jmeter).
  3. Estrutura: requisição solta, Simple Controller ou Simple Controller agrupado.
  4. XML: cada elemento é renderizado pelo seu .xml em jmx_templates/.

`build(plan, variaveis, by_id, config)` → `BuildResult`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from harjmx.har_reader import Entry
from harjmx.plan import Assertion, Extractor, Plan, Variavel


@dataclass
class BuildResult:
    jmx: str
    csv_header: list[str]
    csv_row: list[str]
    hosts: dict[str, str]
    global_headers: list[tuple[str, str]]
    cookies: list[dict]


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build(plan: Plan, variaveis: list[Variavel], by_id: dict[int, Entry], config: dict) -> BuildResult:
    http, hdr_cfg = config["http"], config["headers"]
    samplers = hydrate(plan, variaveis, by_id, set(hdr_cfg["drop"]), http["follow_redirects"])
    ordered_samplers = [samplers[r.har_id] for r in plan.ordered()]
    ordered_entries = [by_id[r.har_id] for r in plan.ordered()]

    hosts = _host_vars(ordered_samplers)
    global_headers = _split_headers(ordered_samplers)
    if any(e.header("accept-encoding") for e in ordered_entries):
        global_headers.append(("Accept-Encoding", hdr_cfg["accept_encoding"]))
    cookies = _initial_cookies(ordered_entries) if config["cookies"]["pre_popular"] else []

    udv = [(name, host) for host, name in hosts.items()]
    udv += [(v.nome, v.valor) for v in variaveis if v.tipo == "global"]
    csv_vars = [v for v in variaveis if v.tipo == "csv"]

    tg_children = []
    if csv_vars:
        tg_children.append(Node(render("csv_data_set", arquivo=plan.csv_arquivo, variaveis=[v.nome for v in csv_vars])))
    if global_headers:
        tg_children.append(Node(render("header_manager", nome="HTTP Header Manager - Global", headers=global_headers)))
    tt = config["think_time"]
    if tt["enabled"]:
        tg_children.append(Node(render("think_time", delay_ms=tt["delay_ms"], range_ms=tt["range_ms"])))
    tg_children += _tree_nodes(arrange(plan, samplers), hosts, http)

    plan_children = [
        Node(render("user_defined_variables", variaveis=udv)),
        Node(render("cookie_manager", cookies=cookies)),
        Node(render("cache_manager")),
        Node(render("thread_group", tg=config["thread_group"]), tg_children),
    ]
    if config["listeners"]["view_results_tree"]:
        plan_children.append(Node(render("view_results_tree", enabled=False)))
    root = Node(render("test_plan", nome=plan.nome_teste), plan_children)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">',
             "  <hashTree>", *root.render(2), "  </hashTree>", "</jmeterTestPlan>"]
    jmx = "\n".join(lines) + "\n"
    ET.fromstring(jmx.encode("utf-8"))           # garante XML bem formado antes de gravar
    return BuildResult(jmx, [v.nome for v in csv_vars], [v.valor for v in csv_vars],
                       hosts, global_headers, cookies)


# --- 1. Hidratação ------------------------------------------------------------------------

@dataclass
class Sampler:
    har_id: int
    nome: str
    method: str
    protocol: str
    host: str
    port: int | None
    path: str
    args: list[tuple[str, str]] = field(default_factory=list)   # query (sem body) ou form
    raw_body: str | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)
    extractors: list[Extractor] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    follow_redirects: bool = True


def hydrate(plan: Plan, variaveis: list[Variavel], by_id: dict[int, Entry],
            drop_headers: set[str], follow_redirects: bool) -> dict[int, Sampler]:
    ordered = plan.ordered()
    pos = {r.har_id: i for i, r in enumerate(ordered)}
    out: dict[int, Sampler] = {}
    for r in ordered:
        e = by_id[r.har_id]
        sub = Substituter([v for v in variaveis if v.em_escopo(r.har_id, pos)])
        path, args, raw = sub(e.path), [], None
        if e.body is None and e.method in ("GET", "HEAD"):
            args = [(k, sub(v)) for k, v in e.query]
        else:
            # Nos demais métodos o JMeter mandaria os parâmetros no corpo: a query fica no path.
            if e.raw_query:
                path += "?" + sub(e.raw_query)
            if e.body is None:
                pass
            elif e.body.kind == "form":
                args = [(k, sub(v)) for k, v in e.body.params]
            else:
                raw = sub(e.body.text)
        extractors = [v.extractor for v in variaveis if v.origem_har_id == r.har_id and v.extractor]
        follow = r.seguir_redirect
        if follow is None:
            # Extrair de headers de um 3xx exige parar no redirect.
            is_redirect = 300 <= e.response.status < 400
            follow = not (is_redirect and any(x.campo == "headers" for x in extractors)) and follow_redirects
        out[r.har_id] = Sampler(
            har_id=e.id,
            nome=request_name(r.etapa, r.passo, e.method, r.endpoint_nome or endpoint_name(path),
                              r.descricao),
            method=e.method, protocol=e.scheme, host=e.host, port=e.port, path=path,
            args=args, raw_body=raw,
            headers=[(k, sub(v)) for k, v in e.headers if k.lower() not in drop_headers],
            extractors=extractors, assertions=list(r.assertions), follow_redirects=follow,
        )
    return out


class Substituter:
    """Troca os valores reais por ${nome} numa passada só (valores mais longos primeiro)."""

    def __init__(self, variaveis: list[Variavel]):
        self.by_text: dict[str, str] = {}
        for v in sorted(variaveis, key=lambda v: len(v.valor), reverse=True):
            for variant in (v.valor, quote(v.valor, safe=""), quote(v.valor, safe="/:")):
                self.by_text.setdefault(variant, v.nome)
        alts = sorted(self.by_text, key=len, reverse=True)
        self.regex = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, alts)) + r")(?![A-Za-z0-9])")
                      if alts else None)

    def __call__(self, text: str) -> str:
        if not self.regex or not text:
            return text
        return self.regex.sub(lambda m: "${" + self.by_text[m.group(0)] + "}", text)


def _host_vars(samplers: list[Sampler]) -> dict[str, str]:
    """Host mais usado vira ${host}; os outros, ${host_<primeiro rótulo>}."""
    counts = Counter(s.host for s in samplers)
    names: dict[str, str] = {}
    for i, (host, _) in enumerate(counts.most_common()):
        base = "host" if i == 0 else "host_" + re.sub(r"\W", "_", host.split(".")[0])
        name, n = base, 2
        while name in names.values():
            name, n = f"{base}{n}", n + 1
        names[host] = name
    return names


def _split_headers(samplers: list[Sampler]) -> list[tuple[str, str]]:
    """Headers idênticos em todos os samplers vão para o Header Manager global."""
    if len(samplers) < 2:
        return []
    common = set((k.lower(), v) for k, v in samplers[0].headers)
    for s in samplers[1:]:
        common &= set((k.lower(), v) for k, v in s.headers)
    global_headers = [(k, v) for k, v in samplers[0].headers if (k.lower(), v) in common]
    for s in samplers:
        s.headers = [(k, v) for k, v in s.headers if (k.lower(), v) not in common]
    return global_headers


def _initial_cookies(ordered: list[Entry]) -> list[dict]:
    """Cookies que o navegador já tinha: enviados antes de qualquer Set-Cookie da jornada."""
    set_so_far: set[str] = set()
    out: dict[str, dict] = {}
    for e in ordered:
        for name, value in _request_cookies(e):
            if name not in set_so_far and name not in out:
                out[name] = {"name": name, "value": value, "domain": e.host, "secure": e.scheme == "https"}
        set_so_far |= {v.split("=", 1)[0].strip() for k, v in e.response.headers
                       if k.lower() == "set-cookie"}
    return list(out.values())


def _request_cookies(e: Entry) -> list[tuple[str, str]]:
    if e.cookies:
        return e.cookies
    raw = e.header("cookie") or ""
    pairs = (p.strip().split("=", 1) for p in raw.split(";") if "=" in p)
    return [(k.strip(), v.strip()) for k, v in pairs]


# --- 2. Nomes (boas práticas) -------------------------------------------------------------

_VAR = re.compile(r"^\$\{([^}]+)\}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def request_name(etapa: int, passo: int, method: str, endpoint: str, descricao: str) -> str:
    """`1.0 POST - /api/login - Autenticar Usuário`"""
    return f"{etapa}.{passo} {method.upper()} - {endpoint} - {descricao.strip()}"


def endpoint_name(path: str) -> str:
    """Path sem domínio e sem query; ${var} vira {var} e IDs soltos viram {id}."""
    segs = []
    for seg in path.split("?", 1)[0].split("/"):
        m = _VAR.match(seg)
        if m:
            seg = "{" + m[1] + "}"
        elif "${" in seg:
            seg = re.sub(r"\$\{([^}]+)\}", r"{\1}", seg)
        elif _UUID.match(seg) or (seg.isdigit() and len(seg) >= 3):
            seg = "{id}"
        segs.append(seg)
    return "/".join(segs) or "/"


def controller_name(numero: int, nome: str) -> str:
    return f"{numero} - {nome.strip()}"


def group_name(de: int, ate: int, nome: str) -> str:
    return f"{de}-{ate} - {nome.strip()}"


def extractor_name(tipo: str, variavel: str) -> str:
    return f"{'JSON' if tipo == 'json' else 'Regex'} Extractor - {variavel}"


def assertion_name(tipo: str, esperado: str) -> str:
    label = {"status": "Status", "texto": "Texto", "json_existe": "JSON"}[tipo]
    return f"Assertion - {label} {esperado}"[:120]


# --- 3. Estrutura -------------------------------------------------------------------------
# etapa com um só passo (x.0) → requisição solta
# etapa com vários passos     → Simple Controller "N - Nome"
# etapas dentro de um grupo   → Simple Controller agrupado "de-ate - Nome" envolvendo as etapas

@dataclass
class Controller:
    nome: str
    filhos: list[Any] = field(default_factory=list)   # Controller | sampler


def arrange(plan: Plan, samplers: dict[int, Any]) -> list[Any]:
    """`samplers`: {har_id: sampler}. Retorna a lista de nós de topo, na ordem do plano."""
    por_etapa: dict[int, list] = {}
    for r in plan.ordered():
        por_etapa.setdefault(r.etapa, []).append(samplers[r.har_id])
    grupo_de = {n: g for g in plan.grupos for n in range(g.de, g.ate + 1)}

    top: list[Any] = []
    abertos: dict[int, Controller] = {}
    for etapa in sorted(plan.etapas, key=lambda e: e.numero):
        reqs = por_etapa.get(etapa.numero, [])
        nos = reqs if len(reqs) == 1 else [Controller(controller_name(etapa.numero, etapa.nome), reqs)]
        g = grupo_de.get(etapa.numero)
        if g is None:
            top += nos
            continue
        if g.de not in abertos:
            abertos[g.de] = Controller(group_name(g.de, g.ate, g.nome))
            top.append(abertos[g.de])
        abertos[g.de].filhos += nos
    return top


# --- 4. XML -------------------------------------------------------------------------------

_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
                "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}
_XML_ESCAPE_RE = re.compile("[&<>\"'\n\r\t]")


def xml_value(value: Any) -> str:
    """Escapa tudo que sai num {{ }}. Quebras de linha viram entidade, então indentar é seguro."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return _XML_ESCAPE_RE.sub(lambda m: _XML_ESCAPES[m.group(0)], str(value))


_TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "jmx_templates"), autoescape=False,
                         finalize=xml_value, trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined)


def render(element: str, **fields: Any) -> str:
    """XML de um elemento JMeter: jmx_templates/<element>.xml preenchido com `fields`.

    Todo {{ valor }} passa por xml_value(). Para mudar o estilo do JMX gerado, edite o .xml e
    rode `UPDATE_GOLDEN=1 python -m pytest`.
    """
    return _TEMPLATES.get_template(f"{element}.xml").render(**fields)


@dataclass
class Node:
    """Um elemento JMeter e seus filhos. Todo elemento é seguido de um <hashTree>."""
    xml: str
    children: list["Node"] = field(default_factory=list)

    def render(self, depth: int) -> list[str]:
        pad = "  " * depth
        lines = [pad + ln for ln in str(self.xml).splitlines() if ln.strip()]
        if not self.children:
            return lines + [pad + "<hashTree/>"]
        lines.append(pad + "<hashTree>")
        for c in self.children:
            lines += c.render(depth + 1)
        return lines + [pad + "</hashTree>"]


def _tree_nodes(items: list[Any], hosts: dict[str, str], http: dict) -> list[Node]:
    out = []
    for it in items:
        if isinstance(it, Controller):
            out.append(Node(render("simple_controller", nome=it.nome), _tree_nodes(it.filhos, hosts, http)))
        else:
            out.append(_sampler_node(it, hosts, http))
    return out


def _sampler_node(s: Sampler, hosts: dict[str, str], http: dict) -> Node:
    view = {**s.__dict__, "domain": "${" + hosts[s.host] + "}", "port": s.port or ""}
    children = []
    if s.headers:
        children.append(Node(render("header_manager", nome="HTTP Header Manager", headers=s.headers)))
    for ex in s.extractors:
        nome = extractor_name(ex.tipo, ex.variavel)
        if ex.tipo == "json":
            children.append(Node(render("json_extractor", nome=nome, variavel=ex.variavel, expressao=ex.expressao,
                                        match=ex.match)))
        else:
            children.append(Node(render("regex_extractor", nome=nome, variavel=ex.variavel, expressao=ex.expressao,
                                        campo=ex.campo, match=ex.match)))
    for a in s.assertions:
        nome = assertion_name(a.tipo, a.esperado)
        if a.tipo == "status":
            xml = render("response_assertion", nome=nome, campo="Assertion.response_code", tipo=8,
                         esperado=a.esperado)
        elif a.tipo == "texto":
            xml = render("response_assertion", nome=nome, campo="Assertion.response_data", tipo=16,
                         esperado=a.esperado)
        else:
            xml = render("json_assertion", nome=nome, jsonpath=a.esperado)
        children.append(Node(xml))
    return Node(render("http_sampler", s=view, http=http), children)
