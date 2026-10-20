"""Formato interno único de uma requisição do HAR, usado por todas as etapas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)
