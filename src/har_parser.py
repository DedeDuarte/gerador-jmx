import json
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

from src.filters import is_static, is_third_party


def _try_parse_json(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _parse_request_body(post: Optional[dict]) -> Any:
    if not post:
        return None
    raw_text = post.get("text", "") or ""
    if raw_text and all(c >= " " or c in "\t\n\r" for c in raw_text):
        body = _try_parse_json(raw_text)
        if body is not None:
            return body
    if post.get("params"):
        return {p["name"]: p.get("value") for p in post["params"]}
    return None


def _parse_query(query_list: list[dict]) -> dict:
    """Convert HAR queryString array to a flat dict."""
    return {q["name"]: q["value"] for q in query_list}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _site_info_from_pages(pages: list[dict]) -> dict:
    """
    Derive host/protocol/port of the main site from the HAR's first page title
    (the browser records the top-level page URL there).

    Returns {"host": str, "protocol": str, "port": Optional[int]}.
    """
    if not pages:
        return {"host": "", "protocol": "https", "port": None}

    parsed = urlparse(pages[0]["title"])
    return {
        "host": parsed.hostname or "",
        "protocol": parsed.scheme or "https",
        "port": parsed.port,
    }


def get_site_info(path: str) -> dict:
    """Standalone helper: loads the HAR from `path` and derives its site info."""
    data = _load(path)
    return _site_info_from_pages(data["log"].get("pages", []))


def parse(
    path: str,
    include_third_party: bool = False,
    include_static: bool = False,
) -> list[dict]:
    """
    Parse a HAR file into a list of enriched request dicts ready for AI analysis.

    Each entry contains:
      - method, host, endpoint (path only), query (dict), status
      - body (parsed JSON or form params, if present)
      - response (parsed JSON, if browser captured it)

    Default behaviour (no flags):
      - Only requests to the main site (derived from the first page title)
      - Static assets excluded (images, css, js, fonts, etc.)
    """
    entries, _ = parse_with_site_info(
        path, include_third_party=include_third_party, include_static=include_static
    )
    return entries


def parse_with_site_info(
    path: str,
    include_third_party: bool = False,
    include_static: bool = False,
) -> tuple[list[dict], dict]:
    """
    Same as `parse`, but also returns the site info derived from the same HAR
    load (avoids reading/parsing the file twice when both are needed, e.g. in
    the --analyze flow).

    Returns (entries, site_info) where site_info is
    {"host": str, "protocol": str, "port": Optional[int]}.
    """
    data = _load(path)

    pages = data["log"].get("pages", [])
    main_host = urlparse(pages[0]["title"]).netloc if pages else ""
    site_info = _site_info_from_pages(pages)

    result = []
    for e in data["log"].get("entries", []):
        req = e.get("request", {})
        resp = e.get("response", {})
        url = req.get("url", "")
        mime = resp.get("content", {}).get("mimeType", "")

        if not include_third_party and is_third_party(url, main_host):
            continue
        if not include_static and is_static(url, mime):
            continue

        parsed_url = urlparse(url)
        query = _parse_query(req.get("queryString", []))
        body = _parse_request_body(req.get("postData"))

        resp_body: Any = None
        resp_text = resp.get("content", {}).get("text")
        if resp_text:
            resp_body = _try_parse_json(resp_text)

        entry: dict = {
            "method": req.get("method", ""),
            "host": parsed_url.netloc,
            "endpoint": parsed_url.path,
            "status": resp.get("status", 0),
        }
        if query:
            entry["query"] = query
        if body is not None:
            entry["body"] = body
        if resp_body is not None:
            entry["response"] = resp_body

        result.append(entry)

    return result, site_info
