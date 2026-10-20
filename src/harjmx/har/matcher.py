"""Casa endpoints explícitos do usuário ("POST /login", "GET */products/*") com IDs do catálogo."""
from __future__ import annotations

import re
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from harjmx.models import Entry

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_SPEC = re.compile(r"^\s*(?:(?P<method>[A-Za-z]+|\*)\s+)?(?P<target>\S+)\s*$")


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


def match(entries: list[Entry], spec: str) -> list[int]:
    method, pattern = parse_spec(spec)
    return [e.id for e in entries
            if (method is None or e.method == method) and _path_matches(e.path, pattern)]
