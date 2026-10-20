"""Subconjunto de JSONPath suficiente para o JMeter: $.a.b, $['a-b'], [0], [*] e $..chave."""
from __future__ import annotations

import re
from typing import Any, Iterator

_SIMPLE_KEY = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_TOKEN = re.compile(r"""\.\.(?P<deep>[A-Za-z0-9_$-]+)
                      |\.(?P<key>[A-Za-z0-9_$-]+)
                      |\[(?P<idx>\d+|\*)\]
                      |\[['"](?P<qkey>[^'"]+)['"]\]""", re.X)


def format_key(key: str) -> str:
    return f".{key}" if _SIMPLE_KEY.match(key) else f"['{key}']"


def flatten(data: Any, prefix: str = "$") -> Iterator[tuple[str, Any]]:
    """Gera (jsonpath, valor) para cada folha escalar."""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from flatten(v, prefix + format_key(str(k)))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, data


def _tokens(path: str) -> list[tuple[str, str]]:
    path = path.strip()
    if path.startswith("$"):
        path = path[1:]
    elif not path.startswith("."):
        path = "." + path                     # o JMeter aceita "access_token" e ".access_token"
    out, pos = [], 0
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"JSONPath não suportado: {path!r}")
        kind = m.lastgroup
        out.append((kind, m[kind]))
        pos = m.end()
    return out


def _deep(node: Any, key: str) -> Iterator[Any]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _deep(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _deep(v, key)


def find(data: Any, path: str) -> list[Any]:
    """Todos os valores que o caminho seleciona (ordem do documento). ValueError se o caminho não for suportado."""
    nodes = [data]
    for kind, val in _tokens(path):
        nxt: list[Any] = []
        for n in nodes:
            if kind in ("key", "qkey") and isinstance(n, dict) and val in n:
                nxt.append(n[val])
            elif kind == "idx" and isinstance(n, list):
                nxt.extend(n if val == "*" else n[int(val):int(val) + 1])
            elif kind == "idx" and val == "*" and isinstance(n, dict):
                nxt.extend(n.values())
            elif kind == "deep":
                nxt.extend(_deep(n, val))
        nodes = nxt
    return nodes


def as_text(value: Any) -> str:
    """Como o JMeter converte o valor extraído em string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
