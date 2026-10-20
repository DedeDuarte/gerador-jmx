"""Lê o HAR e converte cada entry em `Entry`, com ID estável (posição 1-based no arquivo)."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from harjmx.models import Body, Entry, Response

_TEXT_MIME_HINTS = ("json", "text", "xml", "javascript", "html", "x-www-form-urlencoded")


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
    )


def load(path: str | Path) -> tuple[list[Entry], str]:
    """Retorna (todas as entries, host da página principal)."""
    with open(path, encoding="utf-8-sig") as f:
        log = json.load(f)["log"]
    entries = [_to_entry(i, raw) for i, raw in enumerate(log.get("entries", []), start=1)]
    return entries, _main_host(log.get("pages", []), entries)


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
