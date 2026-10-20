"""Nomes conforme as boas práticas (C:\\.code\\Caderno\\Boas-praticas_jmeter\\boas-praticas_jmeter.md)."""
from __future__ import annotations

import re

_VAR = re.compile(r"^\$\{([^}]+)\}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def endpoint(path: str) -> str:
    """Path sem domínio e sem query; ${var} vira {var} e IDs soltos viram {id}."""
    segs = []
    for seg in path.split("?", 1)[0].split("/"):
        m = _VAR.match(seg)
        if m:
            seg = "{" + m[1] + "}"
        elif "${" in seg:
            seg = re.sub(r"\$\{([^}]+)\}", r"{\1}", seg)
        elif _UUID.match(seg) or (seg.isdigit() and len(seg) >= 3):
            seg = "{id}"
        segs.append(seg)
    return "/".join(segs) or "/"


def request(etapa: int, passo: int, method: str, endpoint_nome: str, descricao: str) -> str:
    """`1.0 POST - /api/login - Autenticar Usuário`"""
    return f"{etapa}.{passo} {method.upper()} - {endpoint_nome} - {descricao.strip()}"


def controller(numero: int, nome: str) -> str:
    return f"{numero} - {nome.strip()}"


def group(de: int, ate: int, nome: str) -> str:
    return f"{de}-{ate} - {nome.strip()}"


def extractor(tipo: str, variavel: str) -> str:
    return f"{'JSON' if tipo == 'json' else 'Regex'} Extractor - {variavel}"


def assertion(tipo: str, esperado: str) -> str:
    label = {"status": "Status", "texto": "Texto", "json_existe": "JSON"}[tipo]
    return f"Assertion - {label} {esperado}"[:120]
