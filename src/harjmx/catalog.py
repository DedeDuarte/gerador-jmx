"""ETAPA 1 — Catálogo.

Gera o catalog.md (o que a IA lê para escolher as requisições de cada passo) e o catalog.json.
As requisições mantidas aparecem agrupadas em **ações** do usuário: um bloco novo começa numa
pausa de mais de `PAUSA_ACAO` segundos ou quando muda a página (Referer). Repetições e
telemetria são colapsadas e a provável requisição principal de cada ação ganha um ★.
Nunca inclui valores, só estrutura e nomes de chaves.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from harjmx.har_reader import Entry

PAUSA_ACAO = 1.5
_TELEMETRY = re.compile(r"collect|datalayer|analytics|track|beacon|pixel|metric|/log(ging)?\b|gtm|telemetry"
                        r"|clarity|hotjar|newrelic|sentry|facebook|conversion|criteo|tiktok|pinterest", re.I)
_ACTION_VERB = re.compile(r"add|claim|apply|new|create|move|login|logout|save|remove|delete|update|insert"
                          r"|cadastr|adicion|remov|confirm|checkout|pay|submit", re.I)
_ANTIBOT = re.compile(r"shieldsquare|radware|akamai|_abck|bm_sz|cf-ray|cf-mitigated|incap|perimeterx|x-px"
                      r"|datadome|kasada|imperva", re.I)


def shape(data: Any, depth: int = 2, limit: int = 70) -> str:
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


def _signature(e: Entry) -> tuple:
    return (e.method, e.host, e.path, tuple(sorted({k for k, _ in e.query})), _body_summary(e))


def is_document(e: Entry) -> bool:
    """Navegação de página (não XHR/fetch). Sem headers Sec-Fetch, vale o HTML."""
    dest, mode = e.header("sec-fetch-dest"), e.header("sec-fetch-mode")
    if dest or mode:
        return dest == "document" or mode == "navigate"
    return e.response.mime == "text/html"


def _page(e: Entry) -> str:
    if is_document(e):
        return e.path
    ref = e.header("referer")
    return (urlsplit(ref).path or "/") if ref else "?"


def actions(entries: list[Entry]) -> list[list[Entry]]:
    """Agrupa as entries em ações do usuário (pausa ou troca de página)."""
    out: list[list[Entry]] = []
    last_t, last_page = None, None
    for e in entries:
        page = _page(e)
        gap = e.started is not None and last_t is not None and e.started - last_t > PAUSA_ACAO
        if not out or gap or page != last_page:
            out.append([])
        out[-1].append(e)
        last_t = e.started if e.started is not None else last_t
        last_page = page
    return out


def alertas(entries: list[Entry]) -> list[str]:
    """Problemas do HAR que afetam o script, para o catálogo e o relatório do build."""
    out = []
    if not any(is_document(e) for e in entries):
        out.append("nenhum documento HTML no HAR (gravado com filtro Fetch/XHR?): as páginas não entram no "
                   "teste e valores que vêm do HTML (buildId, tokens escondidos) ficam fixos")
    missing = [e.id for e in entries if _missing_body(e)]
    if missing:
        out.append(f"{len(missing)} respostas sem corpo gravado (" + ", ".join(f"#{i}" for i in missing[:10])
                   + (" …" if len(missing) > 10 else "") + "): valores que saem delas ficam fixos; "
                   "grave com 'Save all as HAR with content'")
    antibot = any(_ANTIBOT.search(f"{k}: {v}") for e in entries for k, v in e.response.headers)
    sem_cookies = not any(e.cookies or e.header("cookie") for e in entries)
    if antibot and sem_cookies:
        out.append("o site tem anti-bot e o HAR veio sem cookies: o script é gerado, mas deve tomar bloqueio "
                   "(302/403) ao rodar; para corrigir, grave o HAR de novo com cookies (har-com-cookies.md)")
    return out


def principal(acao: list[Entry], destino: str | None) -> int | None:
    """Provável requisição principal da ação (só uma dica para a IA): o 1º POST/PUT/DELETE com verbo
    de ação no path; senão, a que traz a página de destino; senão, a de maior resposta 2xx."""
    cands = [e for e in acao if not _TELEMETRY.search(e.path) and 200 <= e.response.status < 300]
    acao_ids = [e.id for e in cands if e.method != "GET" and _ACTION_VERB.search(e.path)]
    if acao_ids:
        return acao_ids[0]
    alvo = destino.rstrip("/") if destino and destino not in ("/", "?") else None
    nav = [e.id for e in cands if alvo and alvo in e.path]
    if nav:
        return nav[-1]
    if not cands:
        return None
    return max(cands, key=lambda e: (len(e.response.text or ""), -e.id)).id


def build(entries: list[Entry], all_entries: list[Entry], excluded: dict[int, str], main_host: str,
          har_name: str) -> tuple[str, dict]:
    """`entries` são as mantidas; `all_entries`, o HAR inteiro. Retorna (markdown, dados json)."""
    hosts = list(dict.fromkeys(e.host for e in entries))
    alias = {h: f"h{i}" for i, h in enumerate(hosts, start=1)}
    by_id = {e.id: e for e in all_entries}
    third_party = Counter(by_id[i].host for i, r in excluded.items() if r == "terceiro")
    reasons = Counter(excluded.values())
    t0 = next((e.started for e in entries if e.started is not None), None)

    first_seen: dict[tuple, int] = {}
    md = [f"# Catálogo: {har_name}", "",
          f"Host principal: {main_host}. Mantidas {len(entries)} de {len(all_entries)} (excluídas: "
          + (", ".join(f"{r} {n}" for r, n in reasons.most_common()) or "nenhuma") + ")."]
    md += [f"**ATENÇÃO:** {a}." for a in alertas(entries)]
    md += ["Hosts: " + ", ".join(f"{alias[h]} = {h}" for h in hosts)]
    if third_party:
        md.append("Terceiros excluídos (se for da aplicação, rode o prepare com `--host <dominio>`): "
                  + ", ".join(f"{h} ({n})" for h, n in third_party.most_common(10)))
    md += ["", "## Ações",
           f"Cada bloco é uma ação do usuário (pausa > {PAUSA_ACAO} s ou troca de página); `→` indica a "
           "página para onde a ação leva. Linha: #id MÉTODO status [host] path?chaves → resposta | "
           "req: corpo enviado. ★ = provável principal da ação (dica).", ""]

    items = []
    acoes = actions(entries)
    paginas = [_page(a[0]) for a in acoes]
    for n, acao in enumerate(acoes, start=1):
        t = f" +{acao[0].started - t0:.1f}s" if t0 is not None and acao[0].started is not None else ""
        proxima = paginas[n] if n < len(acoes) and paginas[n] not in (paginas[n - 1], "?") else None
        md.append(f"### A{n}{t} — página {paginas[n - 1]}" + (f" → {proxima}" if proxima else ""))
        estrela = principal(acao, proxima)
        telemetria = []
        na_acao: dict[tuple, list[int]] = {}
        for e in acao:
            na_acao.setdefault(_signature(e), []).append(e.id)
        for e in acao:
            sig = _signature(e)
            ids = na_acao[sig]
            if ids[0] != e.id:
                continue                         # repetição na mesma ação: colapsa na 1ª
            anterior = first_seen.setdefault(sig, e.id)
            items.append({"id": e.id, "acao": n, "method": e.method, "host": e.host, "path": e.path,
                          "status": e.response.status, "repete": ids[1:]})
            if _TELEMETRY.search(e.path):
                telemetria.append(e.id)
                continue
            query = "?" + "&".join(dict.fromkeys(k for k, _ in e.query)) if e.query else ""
            host = f" [{alias[e.host]}]" if alias[e.host] != "h1" else ""
            line = ("★ " if e.id == estrela else "") + f"#{e.id} {e.method} {e.response.status}{host} {e.path}{query}"
            if anterior != e.id:
                line += f" (repete #{anterior})"
            elif 300 <= e.response.status < 400:
                line += f" → redirect {e.response.header('location') or '?'}"[:80]
            else:
                line += f" → {_response_summary(e)}"
            if e.method != "GET" and e.body:
                line += f" | req: {_body_summary(e)}"
            if len(ids) > 1:
                line += f" (×{len(ids)}: " + ", ".join(f"#{i}" for i in ids[1:6]) + (" …" if len(ids) > 6 else "") + ")"
            md.append(line)
        if telemetria:
            md.append("telemetria: " + ", ".join(f"#{i}" for i in telemetria))
        md.append("")
    md.append(f"Fim do catálogo: {len(acoes)} ações, {len(entries)} requisições.")

    data = {"har": har_name, "main_host": main_host, "hosts": alias, "entries": items,
            "alertas": alertas(entries), "excluded": dict(reasons), "third_party_hosts": dict(third_party)}
    return "\n".join(md), data
