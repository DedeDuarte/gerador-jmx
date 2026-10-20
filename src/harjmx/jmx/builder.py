"""Plano validado + HAR → texto do .jmx e linhas do CSV de massa."""
from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from harjmx.jmx import naming
from harjmx.jmx.hydrate import Sampler, hydrate
from harjmx.jmx.structure import Controller, arrange
from harjmx.models import Entry
from harjmx.plan.schema import Plan
from harjmx.plan.validate import Variavel

TEMPLATES = Path(__file__).parent / "templates"
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


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


@dataclass
class Node:
    xml: str
    children: list["Node"] = field(default_factory=list)

    def render(self, depth: int) -> list[str]:
        pad = "  " * depth
        lines = [pad + ln for ln in self.xml.splitlines() if ln.strip()]
        if not self.children:
            return lines + [pad + "<hashTree/>"]
        lines.append(pad + "<hashTree>")
        for c in self.children:
            lines += c.render(depth + 1)
        return lines + [pad + "</hashTree>"]


@dataclass
class BuildResult:
    jmx: str
    csv_header: list[str]
    csv_row: list[str]
    hosts: dict[str, str]
    global_headers: list[tuple[str, str]]
    cookies: list[dict]


class _Templates:
    def __init__(self):
        env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=False, finalize=xml_value,
                          trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined)
        self._mods = {p.stem.split(".")[0]: env.get_template(p.name).module
                      for p in TEMPLATES.glob("*.xml.j2")}

    def __call__(self, module: str, macro: str, *args) -> str:
        return str(getattr(self._mods[module], macro)(*args))


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


def _set_cookie_names(e: Entry) -> set[str]:
    return {v.split("=", 1)[0].strip() for k, v in e.response.headers if k.lower() == "set-cookie"}


def _request_cookies(e: Entry) -> list[tuple[str, str]]:
    if e.cookies:
        return e.cookies
    raw = e.header("cookie") or ""
    pairs = (p.strip().split("=", 1) for p in raw.split(";") if "=" in p)
    return [(k.strip(), v.strip()) for k, v in pairs]


def _initial_cookies(ordered: list[Entry]) -> list[dict]:
    """Cookies que o navegador já tinha: enviados antes de qualquer Set-Cookie da jornada."""
    set_so_far: set[str] = set()
    out: dict[str, dict] = {}
    for e in ordered:
        for name, value in _request_cookies(e):
            if name not in set_so_far and name not in out:
                out[name] = {"name": name, "value": value, "domain": e.host, "secure": e.scheme == "https"}
        set_so_far |= _set_cookie_names(e)
    return list(out.values())


def _sampler_node(t: _Templates, s: Sampler, hosts: dict[str, str], http: dict) -> Node:
    view = {**s.__dict__, "domain": "${" + hosts[s.host] + "}", "port": s.port or ""}
    children = []
    if s.headers:
        children.append(Node(t("header_manager", "header_manager", "HTTP Header Manager", s.headers)))
    for ex in s.extractors:
        nome = naming.extractor(ex.tipo, ex.variavel)
        if ex.tipo == "json":
            children.append(Node(t("extractors", "json_extractor", nome, ex.variavel, ex.expressao, ex.match)))
        else:
            children.append(Node(t("extractors", "regex_extractor", nome, ex.variavel, ex.expressao,
                                        ex.campo, ex.match)))
    for a in s.assertions:
        nome = naming.assertion(a.tipo, a.esperado)
        if a.tipo == "status":
            xml = t("assertions", "response_assertion", nome, "Assertion.response_code", 8, a.esperado)
        elif a.tipo == "texto":
            xml = t("assertions", "response_assertion", nome, "Assertion.response_data", 16, a.esperado)
        else:
            xml = t("assertions", "json_assertion", nome, a.esperado)
        children.append(Node(xml))
    return Node(t("http_sampler", "http_sampler", view, http), children)


def _tree_nodes(t: _Templates, items: list[Any], hosts: dict[str, str], http: dict) -> list[Node]:
    out = []
    for it in items:
        if isinstance(it, Controller):
            out.append(Node(t("simple_controller", "simple_controller", it.nome),
                            _tree_nodes(t, it.filhos, hosts, http)))
        else:
            out.append(_sampler_node(t, it, hosts, http))
    return out


def build(plan: Plan, variaveis: list[Variavel], by_id: dict[int, Entry], config: dict) -> BuildResult:
    t = _Templates()
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
        tg_children.append(Node(t("config_elements", "csv_data_set", plan.csv_arquivo,
                                  [v.nome for v in csv_vars])))
    if global_headers:
        tg_children.append(Node(t("header_manager", "header_manager", "HTTP Header Manager - Global",
                                  global_headers)))
    tt = config["think_time"]
    if tt["enabled"]:
        tg_children.append(Node(t("thread_group", "think_time", tt["delay_ms"], tt["range_ms"])))
    tg_children += _tree_nodes(t, arrange(plan, samplers), hosts, http)

    plan_children = [
        Node(t("config_elements", "user_defined_variables", udv)),
        Node(t("config_elements", "cookie_manager", cookies)),
        Node(t("config_elements", "cache_manager")),
        Node(t("thread_group", "thread_group", config["thread_group"]), tg_children),
    ]
    if config["listeners"]["view_results_tree"]:
        plan_children.append(Node(t("config_elements", "view_results_tree", False)))
    root = Node(t("test_plan", "test_plan", plan.nome_teste), plan_children)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">',
             "  <hashTree>", *root.render(2), "  </hashTree>", "</jmeterTestPlan>"]
    jmx = "\n".join(lines) + "\n"
    ET.fromstring(jmx.encode("utf-8"))           # garante XML bem formado antes de gravar
    return BuildResult(jmx, [v.nome for v in csv_vars], [v.valor for v in csv_vars],
                       hosts, global_headers, cookies)
