"""Decide quais entries do HAR vão para o catálogo. Cada exclusão tem um motivo."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harjmx.models import Entry

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


def base_domain(host: str) -> str:
    labels = host.lower().strip(".").split(".")
    if len(labels) < 2 or all(p.isdigit() for p in labels):     # localhost, IPs
        return host.lower()
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _GENERIC_SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _matches_domain(host: str, domain: str) -> bool:
    host, domain = host.lower(), domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def is_static(e: Entry) -> bool:
    segment = e.path.lower().rsplit("/", 1)[-1]
    ext = "." + segment.rsplit(".", 1)[-1] if "." in segment else ""
    if ext in STATIC_EXTENSIONS:
        return True
    return e.response.mime.startswith(STATIC_MIME_PREFIXES)


def is_preflight(e: Entry) -> bool:
    return e.method == "OPTIONS"


def load_denylist(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


@dataclass
class FilterConfig:
    main_host: str
    extra_domains: list[str] = field(default_factory=list)  # --host
    denylist: list[str] = field(default_factory=list)
    include_static: bool = False

    @property
    def allowed_domains(self) -> list[str]:
        return [base_domain(self.main_host), *self.extra_domains]


def exclusion_reason(e: Entry, cfg: FilterConfig) -> str | None:
    """None quando a entry fica; senão o motivo: nao-http | preflight | denylist | terceiro | estatico."""
    if e.scheme not in ("http", "https") or not e.host:
        return "nao-http"
    if is_preflight(e):
        return "preflight"
    if any(_matches_domain(e.host, d) for d in cfg.denylist):
        return "denylist"
    if not any(_matches_domain(e.host, d) for d in cfg.allowed_domains):
        return "terceiro"
    if not cfg.include_static and is_static(e):
        return "estatico"
    return None


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
