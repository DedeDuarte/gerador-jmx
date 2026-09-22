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


def _extract_cookies(entries: list[dict], main_host: str) -> list[dict]:
    """
    Extract unique cookies from HAR entries for the main domain.
    
    Returns a list of cookie dicts with: name, value, domain, path, secure, expires.
    Deduplicates by (name, domain, path).
    """
    cookies_map: dict[tuple[str, str, str], dict] = {}
    
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        parsed_url = urlparse(url)
        
        # Only extract cookies for requests to the main host
        if parsed_url.netloc != main_host:
            continue
        
        # Get cookies from request.cookies array
        for cookie in req.get("cookies", []):
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", parsed_url.netloc)
            path = cookie.get("path", "/")
            secure = cookie.get("secure", parsed_url.scheme == "https")
            expires = cookie.get("expires")
            
            if not name:
                continue
            
            key = (name, domain, path)
            # Keep the last occurrence (most recent value)
            cookies_map[key] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure,
                "expires": expires,
            }
    
    return list(cookies_map.values())


def _extract_browser_headers(entries: list[dict], main_host: str) -> dict:
    """
    Extract critical browser headers from the first request to the main host.
    
    Returns dict with: User-Agent, sec-ch-ua*, Sec-Fetch-* headers
    important for anti-bot detection.
    """
    browser_headers = {}
    
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        parsed_url = urlparse(url)
        
        # Find first request to main host with headers
        if parsed_url.netloc != main_host:
            continue
        
        headers = req.get("headers", [])
        if not headers:
            continue
        
        # Extract critical headers
        for header in headers:
            name = header.get("name", "")
            value = header.get("value", "")
            name_lower = name.lower()
            
            if name_lower == "user-agent":
                browser_headers["User-Agent"] = value
            elif name_lower == "sec-ch-ua":
                browser_headers["sec-ch-ua"] = value
            elif name_lower == "sec-ch-ua-mobile":
                browser_headers["sec-ch-ua-mobile"] = value
            elif name_lower == "sec-ch-ua-platform":
                browser_headers["sec-ch-ua-platform"] = value
            elif name_lower in ("sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site"):
                # Store original case for Sec-Fetch-* headers
                browser_headers[name] = value
        
        # If we found at least User-Agent, we have enough
        if "User-Agent" in browser_headers:
            break
    
    return browser_headers


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
    {"host": str, "protocol": str, "port": Optional[int],
     "cookies": list[dict], "browser_headers": dict}.
    """
    data = _load(path)

    pages = data["log"].get("pages", [])
    main_host = urlparse(pages[0]["title"]).netloc if pages else ""
    site_info = _site_info_from_pages(pages)

    result = []
    raw_entries = data["log"].get("entries", [])
    
    for e in raw_entries:
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

    # Extract cookies and browser headers from raw HAR entries
    site_info["cookies"] = _extract_cookies(raw_entries, main_host)
    site_info["browser_headers"] = _extract_browser_headers(raw_entries, main_host)

    return result, site_info
