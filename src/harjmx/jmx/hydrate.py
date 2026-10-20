"""Plano + HAR → samplers com os dados reais e os valores dinâmicos trocados por ${variavel}."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

from harjmx.jmx import naming
from harjmx.models import Entry
from harjmx.plan.schema import Assertion, Extractor, Plan
from harjmx.plan.validate import Variavel


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


def request_texts(e: Entry) -> list[str]:
    """Todos os trechos de texto que uma requisição envia (para checar uso de variáveis)."""
    out = [e.path, unquote(e.path), e.raw_query, *(v for _, v in e.query),
           *(v for k, v in e.headers if k.lower() != "cookie")]
    if e.body:
        out.append(e.body.text)
        out += [v for _, v in e.body.params]
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
            nome=naming.request(r.etapa, r.passo, e.method, r.endpoint_nome or naming.endpoint(path),
                                r.descricao),
            method=e.method, protocol=e.scheme, host=e.host, port=e.port, path=path,
            args=args, raw_body=raw,
            headers=[(k, sub(v)) for k, v in e.headers if k.lower() not in drop_headers],
            extractors=extractors, assertions=list(r.assertions), follow_redirects=follow,
        )
    return out
