"""ETAPA 1 — Catálogo.

Gera o catalog.md (o que a IA lê) e o catalog.json: estrutura e nomes de chaves de cada
requisição mantida, correlações candidatas e avisos sobre o HAR. Nunca inclui valores.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from harjmx.correlation import Candidato, SemOrigem
from harjmx.har import Entry

_COMMON_HEADERS = {
    "accept", "accept-language", "accept-encoding", "cache-control", "connection", "content-length",
    "content-type", "cookie", "dnt", "host", "origin", "pragma", "referer", "te",
    "upgrade-insecure-requests", "user-agent", "priority", "if-none-match", "if-modified-since",
}


def shape(data: Any, depth: int = 2, limit: int = 160) -> str:
    """Resumo da estrutura: {a, b{c, d}, itens[3]{id, nome}}."""
    def walk(d: Any, level: int) -> str:
        if isinstance(d, dict):
            if level >= depth:
                return "{…}" if d else "{}"
            inner = ", ".join(f"{k}{walk(v, level + 1)}" for k, v in d.items())
            return "{" + inner + "}"
        if isinstance(d, list):
            return f"[{len(d)}]" + (walk(d[0], level) if d and isinstance(d[0], (dict, list)) else "")
        return ""
    text = walk(data, 0)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _body_summary(e: Entry) -> str:
    if not e.body:
        return ""
    if e.body.kind == "json":
        return "json" + shape(e.body.json)
    if e.body.kind == "form":
        return "form{" + ", ".join(dict.fromkeys(k for k, _ in e.body.params)) + "}"
    return f"raw({e.body.mime or '?'}, {len(e.body.text)} chars)"


def _response_summary(e: Entry) -> str:
    r = e.response
    if r.is_json:
        return "json" + shape(r.json)
    if r.text:
        return f"{r.mime or 'text'} ({len(r.text)} chars)"
    return r.mime or "vazio"


def _missing_body(e: Entry) -> bool:
    r = e.response
    return r.text is None and 200 <= r.status < 300 and r.status != 204 and bool(r.mime)


def _short(expr: str, limit: int = 90) -> str:
    return expr if len(expr) <= limit else expr[:30] + "…" + expr[-(limit - 31):]


def _signature(e: Entry) -> tuple:
    return (e.method, e.host, e.path, tuple(sorted({k for k, _ in e.query})), _body_summary(e))


def build(entries: list[Entry], all_entries: list[Entry], excluded: dict[int, str], main_host: str,
          correlations: list[Candidato], sem_origem: list[SemOrigem], har_name: str) -> tuple[str, dict]:
    """`entries` são as mantidas; `all_entries`, o HAR inteiro. Retorna (markdown, dados json)."""
    hosts = list(dict.fromkeys(e.host for e in entries))
    alias = {h: f"h{i}" for i, h in enumerate(hosts, start=1)}
    by_id = {e.id: e for e in all_entries}
    third_party = Counter(by_id[i].host for i, r in excluded.items() if r == "terceiro")
    reasons = Counter(excluded.values())

    origins: dict[int, list[str]] = {}
    uses: dict[int, list[str]] = {}
    for c in correlations:
        origins.setdefault(c.origem.har_id, []).append(c.id)
        for u in c.usos:
            uses.setdefault(u.har_id, []).append(c.id)

    seen: dict[tuple, int] = {}
    rows, items = [], []
    for e in entries:
        sig = _signature(e)
        repeat = seen.get(sig)
        seen.setdefault(sig, e.id)
        custom = [k for k, _ in e.headers if k.lower() not in _COMMON_HEADERS
                  and not k.lower().startswith("sec-")]
        notes = []
        if repeat:
            notes.append(f"repete #{repeat}")
        if 300 <= e.response.status < 400:
            notes.append(f"redirect → {e.response.header('location') or '?'}"[:120])
        if e.id in origins:
            notes.append("origem " + ",".join(origins[e.id]))
        if e.id in uses:
            notes.append("usa " + ",".join(dict.fromkeys(uses[e.id])))
        item = {
            "id": e.id, "method": e.method, "host": e.host, "path": e.path,
            "query": list(dict.fromkeys(k for k, _ in e.query)), "status": e.response.status,
            "req": _body_summary(e), "resp": _response_summary(e), "headers": custom, "notes": notes,
        }
        items.append(item)
        query = ("?" + "&".join(item["query"])) if item["query"] else ""
        line = f"#{e.id} {e.method} {e.response.status} [{alias[e.host]}] {e.path}{query}"
        detail = []
        if item["req"]:
            detail.append(f"req: {item['req']}")
        detail.append(f"resp: {item['resp']}")
        if custom:
            detail.append("hdr: " + ", ".join(custom))
        if repeat:
            rows.append(f"{line}  ({'; '.join(notes)})")
            continue
        rows.append(line)
        rows.append("    " + " | ".join(detail))
        if notes:
            rows.append("    obs: " + "; ".join(notes))

    missing = [e.id for e in entries if _missing_body(e)]
    md = [f"# Catálogo HAR: {har_name}", "",
          f"Host principal: {main_host}",
          f"Mantidas: {len(entries)} de {len(all_entries)}. Excluídas: "
          + (", ".join(f"{r} {n}" for r, n in reasons.most_common()) or "nenhuma"), ""]
    if not any(e.response.mime == "text/html" for e in entries):
        md += ["**ATENÇÃO: nenhum documento HTML da aplicação no HAR.** Valores que vêm da página "
               "(buildId, tokens em `<input hidden>`, cookies iniciais) não têm origem visível. "
               "A gravação provavelmente começou depois de carregar a página.", ""]
    if missing:
        md += [f"**ATENÇÃO: {len(missing)} respostas sem corpo gravado** ("
               + ", ".join(f"#{i}" for i in missing[:15]) + (" …" if len(missing) > 15 else "") + "). "
               "Correlações que saem delas não são detectadas. Peça ao usuário um HAR exportado com "
               "'Save all as HAR with content' ou use um extractor manual com `valor_de`.", ""]
    md += ["## Hosts"]
    md += [f"- {alias[h]} = {h}" for h in hosts]
    if third_party:
        md += ["", "## Hosts excluídos como terceiros",
               "Se algum faz parte da aplicação (API, SSO), rode o prepare de novo com `--host <dominio>`."]
        md += [f"- {h} ({n})" for h, n in third_party.most_common(25)]
    md += ["", "## Requisições", "Formato: #id MÉTODO status [host] path?chaves-da-query", ""]
    md += rows
    md += ["", "## Correlações candidatas",
           "Valor que saiu da resposta de uma requisição e foi enviado em outras depois. "
           "Use pelo id em `correlacoes` do plan.json.", ""]
    if correlations:
        md.append("Expressões longas aparecem truncadas; a completa está em correlations.json.")
        # Itens da mesma lista (mesma origem e mesmo caminho a menos dos índices) viram um bloco.
        groups: dict[tuple, list[Candidato]] = {}
        for c in correlations:
            key = (c.origem.har_id, c.origem.tipo, re.sub(r"\[\d+\]", "[*]", c.origem.expressao)
                   if c.origem.tipo == "json" else c.origem.expressao)
            groups.setdefault(key, []).append(c)
        for (origem_id, tipo, expr), cs in groups.items():
            if len(cs) >= 3:
                md.append(f"- **{len(cs)} itens da mesma lista** ← #{origem_id} {tipo} `{_short(expr)}` "
                          "(considere `aleatorio: true` em um deles):")
                for c in cs:
                    usos = ", ".join(f"#{u.har_id} {u.local}" for u in c.usos[:3])
                    indices = "".join(re.findall(r"\[\d+\]", c.origem.expressao))
                    md.append(f"    - {c.id} `{c.variavel}` índices {indices} → {usos}")
                continue
            for c in cs:
                usos = ", ".join(f"#{u.har_id} {u.local}" for u in c.usos[:6])
                more = f" (+{len(c.usos) - 6})" if len(c.usos) > 6 else ""
                campo = f" em {c.origem.campo}" if c.origem.tipo == "regex" else ""
                if c.origem.match > 1:
                    campo += f" (ocorrência {c.origem.match})"
                md.append(f"- {c.id} `{c.variavel}` ← #{c.origem.har_id} {c.origem.tipo}{campo} "
                          f"`{_short(c.origem.expressao)}` → {usos}{more}")
    else:
        md.append("(nenhuma)")
    if sem_origem:
        md += ["", "## Valores repetidos sem origem no HAR",
               "Valor forte enviado em várias requisições que nenhuma resposta gravada contém. Pode ser "
               "gerado pelo cliente (deviceId, fingerprint) ou vir de uma resposta sem corpo "
               "(veja ATENÇÃO acima). Trate com extractor manual + `valor_de`, ou `parametros`.", ""]
        for o in sem_origem[:20]:
            ids = list(dict.fromkeys(u.har_id for u in o.usos))
            md.append(f"- {o.usos[0].local} em " + ", ".join(f"#{i}" for i in ids[:8])
                      + (f" (+{len(ids) - 8})" if len(ids) > 8 else "")
                      + f" — valor_de: `{o.usos[0].har_id}:{o.usos[0].local}`")
    data = {"har": har_name, "main_host": main_host, "hosts": alias, "entries": items,
            "missing_response_body": missing,
            "excluded": dict(reasons), "third_party_hosts": dict(third_party)}
    return "\n".join(md) + "\n", data
