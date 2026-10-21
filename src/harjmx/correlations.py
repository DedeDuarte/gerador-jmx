"""ETAPA 1 — Correlações.

Detecta valores dinâmicos: um valor enviado na requisição Y que veio da resposta de X (X < Y).
Parte dos valores *usados* nas requisições e procura a primeira resposta anterior que os contém.
Assim só aparecem candidatos que de fato importam para o JMeter funcionar.

`detect(entries)` → (candidatos `Candidato`, valores sem origem `SemOrigem`).
"""
from __future__ import annotations

import bisect
import html
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import unquote

from harjmx import jsonpath
from harjmx.har_reader import Entry, path_segments

_IGNORED_REQUEST_HEADERS = {"cookie", "host", "content-length", "referer", "origin", "user-agent",
                            "accept", "accept-language", "accept-encoding", "connection", "content-type",
                            "cache-control", "pragma", "priority", "dnt", "upgrade-insecure-requests"}
_IGNORED_RESPONSE_HEADERS = {"set-cookie", "date", "expires", "last-modified", "content-length",
                             "content-type", "etag", "age", "server-timing", "traceresponse"}
_TOKEN_SPLIT = re.compile(r"[\s,;]+")
_RAW_TOKEN = re.compile(r"[A-Za-z0-9_\-.~+/=%:]{6,}")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"^\d+(\.\d+)+$")
_GENERIC_KEYS = {"id", "value", "code", "key", "token", "uuid", "number", "hash"}


@dataclass
class Origem:
    har_id: int
    tipo: str          # json | regex
    expressao: str
    campo: str = "body"  # body | headers (regex)
    match: int = 1       # ocorrência (regex com contexto curto que casa várias vezes)


@dataclass
class Uso:
    har_id: int
    local: str         # path | query.<k> | form.<k> | header.<k> | body.<jsonpath> | body


@dataclass
class Candidato:
    id: str
    variavel: str
    valor: str
    origem: Origem
    usos: list[Uso] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Candidato":
        return Candidato(d["id"], d["variavel"], d["valor"], Origem(**d["origem"]),
                         [Uso(**u) for u in d["usos"]])


def is_strong(value: str) -> bool:
    """Valor específico o bastante para não casar por acaso (tokens, IDs, hashes)."""
    v = value.strip()
    if len(v) < 6 or len(set(v)) < 3 or _DATE.match(v) or _VERSION.match(v):
        return False
    if v.lower() in {"undefined", "application/json"}:
        return False
    has_digit = any(c.isdigit() for c in v)
    if has_digit:
        return True
    # Sem dígitos só vale se for longo e não parecer palavra/slug.
    return len(v) >= 16 and not re.fullmatch(r"[a-z\-_ ]+", v)


def request_fields(e: Entry) -> list[tuple[str, str]]:
    """Valores enviados por uma requisição, com o local de cada um."""
    out: list[tuple[str, str]] = [(f"path.{i}", unquote(seg)) for i, seg in enumerate(path_segments(e))]
    out += [(f"query.{k}", v) for k, v in e.query]
    for k, v in e.headers:
        if k.lower() not in _IGNORED_REQUEST_HEADERS and not k.lower().startswith("sec-"):
            out.append((f"header.{k}", v))
    if e.body:
        if e.body.kind == "json":
            out += [(f"body.{p}", jsonpath.as_text(v)) for p, v in jsonpath.flatten(e.body.json)
                    if not isinstance(v, (bool, type(None)))]
        elif e.body.kind == "form":
            out += [(f"form.{k}", v) for k, v in e.body.params]
        else:
            out += [("body", t) for t in _RAW_TOKEN.findall(e.body.text)]
    return out


def _usage_values(local: str, value: str) -> list[str]:
    """O valor inteiro e, para headers como 'Bearer xyz', cada pedaço."""
    vals = [value]
    if local.startswith("header."):
        vals += [t for t in _TOKEN_SPLIT.split(value) if t != value]
    return [v for v in vals if is_strong(v)]


def _headers_text(e: Entry) -> str:
    return "\n".join(f"{k}: {v}" for k, v in e.response.headers
                     if k.lower() not in _IGNORED_RESPONSE_HEADERS)


def _request_text(e: Entry) -> str:
    parts = [e.url, *(v for k, v in e.headers if k.lower() != "cookie")]
    if e.body:
        parts.append(e.body.text)
    return "\n".join(parts)


class _Corpus:
    """Concatena textos por entry para achar 'primeira entry que contém v' com um único find."""

    def __init__(self, items: list[tuple[int, str]]):
        self.ids, self.starts, chunks, pos = [], [], [], 0
        for eid, text in items:
            self.ids.append(eid)
            self.starts.append(pos)
            chunks.append(text)
            pos += len(text) + 1
        self.text = "\x00".join(chunks)

    def first(self, value: str) -> int | None:
        i = self.text.find(value)
        if i < 0:
            return None
        return self.ids[bisect.bisect_right(self.starts, i) - 1]


def build_regex(text: str, value: str) -> tuple[str, int] | None:
    """(regex, ocorrência) cujo grupo 1 é exatamente `value`.

    Prefere contexto curto casando na 1ª ocorrência; se o contexto curto casar antes em outro
    lugar, usa o número da ocorrência em vez de um contexto longo e frágil.
    """
    idx = text.find(value)
    if idx < 0:
        return None
    end = idx + len(value)
    right = text[end] if end < len(text) else ""
    if right and right not in value:
        group = "([^" + re.escape(right) + "\\r\\n]+)"
    else:
        group = "(.+?)" + (re.escape(right) if right else "$")
    for size in (12, 20, 32, 48, 80):
        start = max(0, idx - size)
        # Recua até um limite natural (espaço, tag, separador) para não cortar palavras.
        while start > 0 and idx - start < size + 24 and text[start - 1] not in " \t<>{},;&?\n":
            start -= 1
        left = text[start:idx].rsplit("\n", 1)[-1]
        if not left:
            break
        pattern = re.escape(left).replace("\\ ", " ") + group
        groups = [m.group(1) for _, m in zip(range(50), re.finditer(pattern, text))]
        if groups and groups[0] == value:
            return pattern, 1
        if value in groups:
            return pattern, groups.index(value) + 1     # contexto curto + ocorrência N
    return None


def _camel(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if not parts:
        return "valor"
    first = parts[0][0].lower() + parts[0][1:]
    name = first + "".join(p[0].upper() + p[1:] for p in parts[1:])
    return name if not name[0].isdigit() else "v" + name


def _suggest_name(origem: Origem, uso_local: str) -> str:
    if origem.tipo == "json":
        keys = [a or b for a, b in re.findall(r"\.([A-Za-z_$][\w$]*)|\['([^']+)'\]", origem.expressao)]
        if keys:
            if keys[-1].lower() in _GENERIC_KEYS and len(keys) > 1:
                return _camel(keys[-2] + "_" + keys[-1])     # user.id → userId
            return _camel(keys[-1])
    tail = uso_local.split(".", 1)[-1] if "." in uso_local else uso_local
    tail = tail.rsplit(".", 1)[-1].strip("$[]'")
    return _camel(tail) if tail and not tail.isdigit() and tail != "body" else "valor"


@dataclass
class SemOrigem:
    """Valor forte enviado em várias requisições que nenhuma resposta gravada contém."""
    usos: list[Uso]


def detect(entries: list[Entry]) -> tuple[list[Candidato], list[SemOrigem]]:
    """`entries` já filtradas, na ordem do HAR. Retorna (candidatos, valores sem origem)."""
    json_first: dict[str, tuple[int, str]] = {}
    for e in entries:
        if e.response.is_json:
            for path, v in jsonpath.flatten(e.response.json):
                if not isinstance(v, (bool, type(None))):
                    json_first.setdefault(jsonpath.as_text(v), (e.id, path))
    body_corpus = _Corpus([(e.id, e.response.text or "") for e in entries])
    header_corpus = _Corpus([(e.id, _headers_text(e)) for e in entries])
    request_corpus = _Corpus([(e.id, _request_text(e)) for e in entries])
    by_id = {e.id: e for e in entries}

    found: dict[str, Candidato] = {}
    rejected: set[str] = set()
    orphans: dict[str, list[Uso]] = {}
    for e in entries:
        for local, raw_value in request_fields(e):
            for value in _usage_values(local, raw_value):
                if value in found:
                    cand = found[value]
                    if cand.origem.har_id < e.id and not any(u.har_id == e.id and u.local == local
                                                             for u in cand.usos):
                        cand.usos.append(Uso(e.id, local))
                    continue
                if value in orphans:
                    orphans[value].append(Uso(e.id, local))
                    continue
                if value in rejected:
                    continue
                origem = _find_origin(value, e.id, json_first, body_corpus, header_corpus, by_id)
                if origem is None:
                    # Ninguém antes respondeu com esse valor: gerado pelo cliente ou resposta não gravada.
                    if any(c.isdigit() for c in value):
                        orphans[value] = [Uso(e.id, local)]
                    else:
                        rejected.add(value)
                    continue
                first_sent = request_corpus.first(value)
                if first_sent is not None and first_sent <= origem.har_id:
                    rejected.add(value)        # o cliente já tinha o valor: não é correlação
                    continue
                found[value] = Candidato("", "", value, origem, [Uso(e.id, local)])

    candidates = sorted(found.values(), key=lambda c: (c.origem.har_id, c.usos[0].har_id))
    names: set[str] = set()
    for i, c in enumerate(candidates, start=1):
        c.id = f"C{i}"
        base = _suggest_name(c.origem, c.usos[0].local)
        name, n = base, 2
        while name in names:
            name, n = f"{base}{n}", n + 1
        names.add(name)
        c.variavel = name
    sem_origem = [SemOrigem(u) for u in orphans.values() if len({x.har_id for x in u}) >= 2]
    sem_origem.sort(key=lambda o: -len(o.usos))
    return candidates, sem_origem


def _find_origin(value: str, before_id: int, json_first, body_corpus: _Corpus,
                 header_corpus: _Corpus, by_id: dict[int, Entry]) -> Origem | None:
    options: list[Origem] = []
    if value in json_first and json_first[value][0] < before_id:
        eid, path = json_first[value]
        options.append(Origem(eid, "json", path))
    for variant in dict.fromkeys([value, html.escape(value), value.replace("/", "\\/")]):
        eid = body_corpus.first(variant)
        if eid is not None and eid < before_id:
            built = build_regex(by_id[eid].response.text or "", variant)
            if built:
                options.append(Origem(eid, "regex", built[0], "body", built[1]))
                break
    eid = header_corpus.first(value)
    if eid is not None and eid < before_id:
        built = build_regex(_headers_text(by_id[eid]), value)
        if built:
            options.append(Origem(eid, "regex", built[0], "headers", built[1]))
    if not options:
        return None
    # Origem mais antiga; no empate, JSON (mais legível que regex).
    return min(options, key=lambda o: (o.har_id, o.tipo != "json"))
