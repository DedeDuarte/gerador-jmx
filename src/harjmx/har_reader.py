"""ETAPA 1 — Leitura do HAR.

Converte cada entry do HAR em `Entry` (ID estável = posição 1-based no arquivo), decide quais
ficam no catálogo (`split`) e casa endpoints informados pelo usuário (`match`).
Todo o resto do pacote só conhece `Entry`, nunca o JSON cru do HAR.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from harjmx import jsonpath

# --- Modelo -------------------------------------------------------------------------------

BodyKind = Literal["json", "form", "raw"]


@dataclass
class Body:
    kind: BodyKind
    mime: str
    text: str
    json: Any = None                                     # kind == "json"
    params: list[tuple[str, str]] = field(default_factory=list)  # kind == "form"


@dataclass
class Response:
    status: int
    mime: str
    headers: list[tuple[str, str]]
    text: str | None
    json: Any = None

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)

    @property
    def is_json(self) -> bool:
        return self.json is not None


@dataclass
class Entry:
    id: int                                  # posição 1-based no HAR, nunca muda
    method: str
    url: str
    scheme: str
    host: str
    port: int | None
    path: str
    query: list[tuple[str, str]]             # decodificado, ordem e duplicatas preservadas
    raw_query: str                           # como estava na URL
    headers: list[tuple[str, str]]
    cookies: list[tuple[str, str]]
    body: Body | None
    response: Response
    started: float | None = None             # startedDateTime em segundos (epoch)

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)


# --- Leituras de uma Entry ----------------------------------------------------------------

def path_segments(e: Entry) -> list[str]:
    return [s for s in e.path.split("/") if s]


def field_value(e: Entry, local: str) -> str | None:
    """Lê o valor real de um local (query.k, form.k, header.k, body.<jsonpath>, path.n) da requisição."""
    kind, _, key = local.partition(".")
    if kind == "path" and key.isdigit():
        segs = path_segments(e)
        return unquote(segs[int(key)]) if int(key) < len(segs) else None
    if kind == "query":
        return next((v for k, v in e.query if k == key), None)
    if kind == "header":
        return e.header(key)
    if kind == "form" and e.body and e.body.kind == "form":
        return next((v for k, v in e.body.params if k == key), None)
    if kind == "body" and e.body and e.body.kind == "json" and key:
        found = jsonpath.find(e.body.json, key)
        return jsonpath.as_text(found[0]) if found else None
    return None


def request_texts(e: Entry) -> list[str]:
    """Todos os trechos de texto que uma requisição envia (para checar uso de variáveis)."""
    out = [e.path, unquote(e.path), e.raw_query, *(v for _, v in e.query),
           *(v for k, v in e.headers if k.lower() != "cookie")]
    if e.body:
        out.append(e.body.text)
        out += [v for _, v in e.body.params]
    return out


# --- Carregar o arquivo -------------------------------------------------------------------

_TEXT_MIME_HINTS = ("json", "text", "xml", "javascript", "html", "x-www-form-urlencoded")


def load(path: str | Path) -> tuple[list[Entry], str]:
    """Retorna (todas as entries, host da página principal)."""
    with open(path, encoding="utf-8-sig") as f:
        log = json.load(f)["log"]
    entries = [_to_entry(i, raw) for i, raw in enumerate(log.get("entries", []), start=1)]
    return entries, _main_host(log.get("pages", []), entries)


def _try_json(text: str | None) -> Any:
    if not text:
        return None
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _pairs(items: list[dict] | None) -> list[tuple[str, str]]:
    return [(i.get("name", ""), i.get("value", "") or "") for i in items or [] if i.get("name")]


def _parse_body(post: dict | None) -> Body | None:
    if not post:
        return None
    mime = (post.get("mimeType") or "").lower()
    text = post.get("text") or ""
    if "x-www-form-urlencoded" in mime:
        params = _pairs(post.get("params")) or parse_qsl(text, keep_blank_values=True)
        return Body("form", mime, text, params=list(params))
    parsed = _try_json(text)
    if parsed is not None:
        return Body("json", mime, text, json=parsed)
    if not text and not post.get("params"):
        return None
    return Body("raw", mime, text)


def _response_text(content: dict) -> str | None:
    text = content.get("text")
    if text is None:
        return None
    if content.get("encoding") == "base64":
        mime = (content.get("mimeType") or "").lower()
        if not any(h in mime for h in _TEXT_MIME_HINTS):
            return None
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return None
    return text


def _to_entry(idx: int, raw: dict) -> Entry:
    req, resp = raw.get("request", {}), raw.get("response", {})
    url = req.get("url", "")
    parts = urlsplit(url)
    content = resp.get("content", {}) or {}
    resp_text = _response_text(content)
    return Entry(
        id=idx,
        method=(req.get("method") or "GET").upper(),
        url=url,
        scheme=parts.scheme or "https",
        host=parts.hostname or "",
        port=parts.port,
        path=parts.path or "/",
        query=parse_qsl(parts.query, keep_blank_values=True),
        raw_query=parts.query,
        headers=[(k, v) for k, v in _pairs(req.get("headers")) if not k.startswith(":")],
        cookies=_pairs(req.get("cookies")),
        body=_parse_body(req.get("postData")),
        response=Response(
            status=int(resp.get("status") or 0),
            mime=(content.get("mimeType") or "").split(";")[0].strip().lower(),
            headers=[(k, v) for k, v in _pairs(resp.get("headers")) if not k.startswith(":")],
            text=resp_text,
            json=_try_json(resp_text),
        ),
        started=_timestamp(raw.get("startedDateTime")),
    )


def _timestamp(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp() if value else None
    except ValueError:
        return None


def _main_host(pages: list[dict], entries: list[Entry]) -> str:
    """Host da primeira página; se o HAR não tiver pages, o host do primeiro documento HTML."""
    for page in pages:
        host = urlsplit(page.get("title", "")).hostname
        if host:
            return host
    for e in entries:
        if e.response.mime == "text/html" and e.host:
            return e.host
    return entries[0].host if entries else ""


# --- Filtros: o que vai para o catálogo ---------------------------------------------------

STATIC_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico", ".bmp", ".tiff",
    ".css", ".js", ".mjs", ".map",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".webm", ".ogg", ".wav", ".m3u8", ".ts",
    ".pdf", ".zip", ".gz",
}

STATIC_MIME_PREFIXES = (
    "text/css", "text/javascript", "application/javascript", "application/x-javascript",
    "image/", "font/", "audio/", "video/", "application/font", "application/vnd.ms-fontobject",
)

# Segundo nível genérico: em "lojasrenner.com.br" o domínio base tem 3 rótulos.
_GENERIC_SLD = {"com", "net", "org", "gov", "edu", "co", "ac", "mil"}


@dataclass
class FilterConfig:
    main_host: str
    extra_domains: list[str] = field(default_factory=list)  # --host
    denylist: list[str] = field(default_factory=list)
    include_static: bool = False

    @property
    def allowed_domains(self) -> list[str]:
        return [base_domain(self.main_host), *self.extra_domains]


def split(entries: list[Entry], cfg: FilterConfig) -> tuple[list[Entry], dict[int, str]]:
    """Retorna (entries mantidas, {id: motivo} das excluídas)."""
    kept, excluded = [], {}
    for e in entries:
        reason = exclusion_reason(e, cfg)
        if reason:
            excluded[e.id] = reason
        else:
            kept.append(e)
    return kept, excluded


def exclusion_reason(e: Entry, cfg: FilterConfig) -> str | None:
    """None quando a entry fica; senão o motivo: nao-http | preflight | denylist | terceiro | estatico."""
    if e.scheme not in ("http", "https") or not e.host:
        return "nao-http"
    if e.method == "OPTIONS":
        return "preflight"
    if any(_matches_domain(e.host, d) for d in cfg.denylist):
        return "denylist"
    if not any(_matches_domain(e.host, d) for d in cfg.allowed_domains):
        return "terceiro"
    if not cfg.include_static and is_static(e):
        return "estatico"
    return None


def base_domain(host: str) -> str:
    labels = host.lower().strip(".").split(".")
    if len(labels) < 2 or all(p.isdigit() for p in labels):     # localhost, IPs
        return host.lower()
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _GENERIC_SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_static(e: Entry) -> bool:
    segment = e.path.lower().rsplit("/", 1)[-1]
    ext = "." + segment.rsplit(".", 1)[-1] if "." in segment else ""
    if ext in STATIC_EXTENSIONS:
        return True
    return e.response.mime.startswith(STATIC_MIME_PREFIXES)


def load_denylist(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _matches_domain(host: str, domain: str) -> bool:
    host, domain = host.lower(), domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


# --- Matcher: "POST /login" → IDs ---------------------------------------------------------

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_SPEC = re.compile(r"^\s*(?:(?P<method>[A-Za-z]+|\*)\s+)?(?P<target>\S+)\s*$")


def match(entries: list[Entry], spec: str) -> list[int]:
    """IDs das entries que casam com 'MÉTODO /caminho' (método '*' ou omitido = qualquer)."""
    method, pattern = parse_spec(spec)
    return [e.id for e in entries
            if (method is None or e.method == method) and _path_matches(e.path, pattern)]


def parse_spec(spec: str) -> tuple[str | None, str]:
    m = _SPEC.match(spec)
    if not m:
        raise ValueError(f"endpoint inválido: {spec!r} (use 'MÉTODO /caminho')")
    method = (m["method"] or "").upper() or None
    if method == "*":
        method = None
    elif method and method not in _METHODS:
        raise ValueError(f"método desconhecido em {spec!r}")
    target = m["target"]
    if "://" in target:
        target = urlsplit(target).path or "/"
    return method, target.split("?", 1)[0]


def _path_matches(path: str, pattern: str) -> bool:
    if any(c in pattern for c in "*?["):
        return fnmatchcase(path, pattern) or fnmatchcase(path, "*" + pattern)
    # Sem curinga: o caminho do HAR termina com o informado (o usuário costuma omitir prefixos).
    return path == pattern or (pattern.startswith("/") and path.endswith(pattern.rstrip("/")))
