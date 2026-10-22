"""Correlações, dependências e massa de dados, resolvidas contra a sequência do plano.

Para cada valor dinâmico que uma requisição da sequência envia, a origem é a resposta mais
próxima antes dela: primeiro dentro da própria sequência; se não houver, entre as requisições
mantidas do HAR, e essa requisição entra na sequência como dependência. O login também entra
sozinho quando há passos escolhidos depois dele. Valores digitados pelo usuário (login, senha,
CEP, cupom...) viram massa de CSV; o que sobra sem origem fica fixo e é reportado. O buildId do
Next.js (`/_next/data/<buildId>/...`) sem origem vira variável global, com aviso.

`resolve(pedidas, kept)` → `Resolucao`.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

from harjmx import jsonpath
from harjmx.har_reader import Entry, path_segments

_IGNORED_REQUEST_HEADERS = {"cookie", "host", "content-length", "referer", "origin", "user-agent",
                            "accept", "accept-language", "accept-encoding", "connection", "content-type",
                            "cache-control", "pragma", "priority", "dnt", "upgrade-insecure-requests",
                            # rastreio de APM: muda a cada requisição e não precisa de correlação
                            "x-dtpc", "x-dynatrace", "traceparent", "tracestate", "newrelic",
                            "x-request-id", "x-correlation-id", "x-b3-traceid", "x-b3-spanid"}
_IGNORED_RESPONSE_HEADERS = {"set-cookie", "date", "expires", "last-modified", "content-length",
                             "content-type", "etag", "age", "server-timing", "traceresponse"}
_TOKEN_SPLIT = re.compile(r"[\s,;]+")
_RAW_TOKEN = re.compile(r"[A-Za-z0-9_\-.~+/=%:]{6,}")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"^\d+(\.\d+)+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_GENERIC_KEYS = {"id", "value", "code", "key", "token", "uuid", "number", "hash"}

# Campos que o usuário digita: o valor vira coluna do CSV.
_INPUT_KEY = re.compile(r"^(login|user_?name|e-?mail\w*|\w*password|\w*senha|pwd|cpf\w*|cnpj\w*|\w*cep"
                        r"|\w*postal_?code|\w*zip_?code|zip|cupom\w*|coupon\w*|\w*claim_?code"
                        r"|\w*telefone|\w*phone\w*|celular|mobile)$", re.I)
_PASSWORD_KEY = re.compile(r"pass|senha|pwd", re.I)


# --- Modelo -------------------------------------------------------------------------------

@dataclass
class Extractor:
    variavel: str
    tipo: str              # json | regex
    expressao: str
    campo: str = "body"    # body | headers (regex)
    match: int = 1         # ocorrência (regex com contexto curto que casa várias vezes)


@dataclass
class Variavel:
    nome: str
    valor: str
    tipo: str                                 # extractor | csv | global
    origem_har_id: int | None = None          # requisição cujo extractor define a variável
    extractor: Extractor | None = None
    primeiro_uso: int | None = None

    def em_escopo(self, har_id: int, pos: dict[int, int]) -> bool:
        """A variável pode substituir valores na requisição `har_id`?"""
        return self.origem_har_id is None or pos[self.origem_har_id] < pos[har_id]


@dataclass
class Passo:
    har_id: int
    descricao: str
    motivo: str = ""       # vazio: pedido no plano; senão, por que entrou sozinho


@dataclass
class EtapaFinal:
    nome: str
    passos: list[Passo] = field(default_factory=list)


@dataclass
class Fixo:
    """Valor dinâmico que ficou literal no script."""
    har_id: int
    local: str
    motivo: str


@dataclass
class Resolucao:
    etapas: list[EtapaFinal]
    variaveis: list[Variavel]
    fixos: list[Fixo]
    avisos: list[str] = field(default_factory=list)

    def sequencia(self) -> list[Passo]:
        return [p for et in self.etapas for p in et.passos]

    def automaticas(self) -> list[Passo]:
        return [p for p in self.sequencia() if p.motivo]


# --- Valores enviados ---------------------------------------------------------------------

def is_strong(value: str) -> bool:
    """Valor específico o bastante para não casar por acaso (tokens, IDs, hashes)."""
    v = value.strip()
    if len(v) < 6 or len(set(v)) < 3 or _DATE.match(v) or _VERSION.match(v):
        return False
    if v.lower() in {"undefined", "application/json"}:
        return False
    if any(c.isdigit() for c in v):
        return True
    # Sem dígitos só vale se for longo e não parecer palavra (camelCase, slug).
    return len(v) >= 24 and not re.fullmatch(r"[A-Za-z\-_ ]+", v)


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
    if local.startswith("path."):
        # Segmento de path só é ID se tiver cara de ID (dígitos suficientes ou UUID), não de nome de rota.
        vals = [v for v in vals if _UUID.match(v) or sum(c.isdigit() for c in v) >= 3]
    return [v for v in vals if is_strong(v)]


def _next_build_id(e: Entry) -> str | None:
    """Local do buildId em `/_next/data/<buildId>/...` (Next.js), que muda a cada deploy."""
    segs = path_segments(e)
    for i in range(len(segs) - 2):
        if segs[i] == "_next" and segs[i + 1] == "data":
            return f"path.{i + 2}"
    return None


def _field_key(local: str) -> str:
    """'body.$.address.postalCode' → 'postalCode'; 'form.username' → 'username'."""
    tail = local.split(".", 1)[-1]
    keys = re.findall(r"[A-Za-z_][\w\-]*", tail.replace("$", ""))
    return keys[-1] if keys else ""


def _is_input(local: str, value: str) -> bool:
    if local.startswith(("path.", "header.")) or len(value) < 4 or value.lower() in {"true", "false", "null"}:
        return False
    return bool(_INPUT_KEY.match(_field_key(local)))


def _request_text(e: Entry) -> str:
    parts = [e.url, *(v for k, v in e.headers if k.lower() != "cookie")]
    if e.body:
        parts.append(e.body.text)
    return "\n".join(parts)


def _headers_text(e: Entry) -> str:
    return "\n".join(f"{k}: {v}" for k, v in e.response.headers
                     if k.lower() not in _IGNORED_RESPONSE_HEADERS)


# --- Expressões de extração ---------------------------------------------------------------

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


class _Respostas:
    """Textos das respostas por entry, calculados uma vez."""

    def __init__(self, kept: list[Entry]):
        self.by_id = {e.id: e for e in kept}
        self._json: dict[int, dict[str, str]] = {}
        self._req: dict[int, str] = {}
        self._hdr: dict[int, str] = {}

    def json_paths(self, eid: int) -> dict[str, str]:
        if eid not in self._json:
            e, paths = self.by_id[eid], {}
            if e.response.is_json:
                for p, v in jsonpath.flatten(e.response.json):
                    if not isinstance(v, (bool, type(None))):
                        paths.setdefault(jsonpath.as_text(v), p)
            self._json[eid] = paths
        return self._json[eid]

    def request_text(self, eid: int) -> str:
        if eid not in self._req:
            self._req[eid] = _request_text(self.by_id[eid])
        return self._req[eid]

    def headers(self, eid: int) -> str:
        if eid not in self._hdr:
            self._hdr[eid] = _headers_text(self.by_id[eid])
        return self._hdr[eid]

    def origem(self, eid: int, value: str) -> Extractor | None:
        """Extractor (sem nome) que tira `value` da resposta de `eid`, se ela o contém.

        Resposta que só ecoa o que a própria requisição mandou não é origem."""
        body = self.by_id[eid].response.text or ""
        hdr = self.headers(eid)
        if value not in body and value not in hdr and value not in self.json_paths(eid):
            if html.escape(value) not in body and value.replace("/", "\\/") not in body:
                return None
        if value in self.request_text(eid):
            return None
        path = self.json_paths(eid).get(value)
        if path:
            return Extractor("", "json", path)
        for variant in dict.fromkeys([value, html.escape(value), value.replace("/", "\\/")]):
            if variant in body:
                built = build_regex(body, variant)
                if built:
                    return Extractor("", "regex", built[0], "body", built[1])
        if value in hdr:
            built = build_regex(hdr, value)
            if built:
                return Extractor("", "regex", built[0], "headers", built[1])
        return None


# --- Nomes --------------------------------------------------------------------------------

def _camel(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if not parts:
        return "valor"
    first = parts[0][0].lower() + parts[0][1:]
    name = first + "".join(p[0].upper() + p[1:] for p in parts[1:])
    return name if not name[0].isdigit() else "v" + name


def _suggest_name(ex: Extractor, uso_local: str) -> str:
    if ex.tipo == "json":
        keys = [a or b for a, b in re.findall(r"\.([A-Za-z_$][\w$]*)|\['([^']+)'\]", ex.expressao)]
        keys = [part for k in keys for part in k.split(".") if part]    # 'prop.product.defaultSku'
        if keys:
            if keys[-1].lower() in _GENERIC_KEYS and len(keys) > 1:
                return _camel(keys[-2] + "_" + keys[-1])     # user.id → userId
            return _camel(keys[-1])
    tail = _field_key(uso_local)
    return _camel(tail) if tail and not tail.isdigit() and tail != "body" else "valor"


def titulo(variavel: str) -> str:
    """'sessionConfirmationNumber' → 'Session Confirmation Number'."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", variavel).replace("_", " ").split()
    return " ".join(w[0].upper() + w[1:] for w in words) or variavel


class _Nomes:
    def __init__(self):
        self.usados: set[str] = set()

    def novo(self, base: str) -> str:
        name, n = base, 2
        while name in self.usados:
            name, n = f"{base}{n}", n + 1
        self.usados.add(name)
        return name


# --- Login --------------------------------------------------------------------------------

def is_login(e: Entry) -> bool:
    """POST que manda um campo de senha preenchido e não falhou."""
    if e.method != "POST" or not e.body or e.response.status >= 400:
        return False
    if e.body.kind == "form":
        return any(_PASSWORD_KEY.search(k) and v for k, v in e.body.params)
    if e.body.kind == "json":
        return any(_PASSWORD_KEY.search(_field_key("body." + p)) and v not in (None, "")
                   for p, v in jsonpath.flatten(e.body.json))
    return False


def _add_login(etapas: list[EtapaFinal], kept: list[Entry]) -> None:
    by_id = {e.id: e for e in kept}
    pedidos = [p.har_id for et in etapas for p in et.passos]
    if any(is_login(by_id[i]) for i in pedidos):
        return
    logins = [e for e in kept if is_login(e) and e.id < max(pedidos)]
    if not logins:
        return
    login = logins[-1]
    idx = next(k for k, et in enumerate(etapas) if any(p.har_id > login.id for p in et.passos))
    etapas.insert(idx, EtapaFinal("Login", [Passo(login.id, "Login",
                                                  "login: os passos seguintes dependem da sessão")]))


# --- Resolução ----------------------------------------------------------------------------

def resolve(pedidas: list[tuple[str, list[int]]], kept: list[Entry]) -> Resolucao:
    """`pedidas`: [(nome da etapa, [har_ids])] na ordem do plano; `kept`: entries mantidas, ordem do HAR."""
    etapas = [EtapaFinal(nome, [Passo(i, nome) for i in ids]) for nome, ids in pedidas]
    _add_login(etapas, kept)
    resp = _Respostas(kept)
    ids_har = [e.id for e in kept]
    nomes = _Nomes()
    variaveis: dict[str, Variavel] = {}
    fixos: dict[str, Fixo] = {}
    avisos: list[str] = []

    i = 0
    while True:
        seq = [(k, p) for k, et in enumerate(etapas) for p in et.passos]
        if i >= len(seq):
            break
        k, passo = seq[i]
        e = resp.by_id[passo.har_id]
        na_seq = {p.har_id for _, p in seq}
        dependencia = None
        build_local = _next_build_id(e)
        for local, raw in request_fields(e):
            if raw in variaveis:
                continue
            if _is_input(local, raw):
                variaveis[raw] = Variavel(nomes.novo(_camel(_field_key(local))), raw, "csv", primeiro_uso=e.id)
                continue
            pendentes: list[tuple[str, str]] = []
            valores = [raw] if local == build_local else _usage_values(local, raw)
            for value in valores:
                if value in variaveis:
                    pendentes.clear()                # um pedaço (ex.: token do 'Bearer') já resolve o campo
                    continue
                antes = [p.har_id for _, p in seq[:i]]
                origem = next(((eid, ex) for eid in reversed(antes) if (ex := resp.origem(eid, value))), None)
                if origem is None:
                    fora = (x for x in reversed(ids_har) if x < e.id and x not in na_seq)
                    origem = next(((eid, ex) for eid in fora if (ex := resp.origem(eid, value))), None)
                    if origem is not None:
                        dependencia = origem[0]
                if origem is None and local == build_local:
                    variaveis[value] = Variavel(nomes.novo("buildId"), value, "global", primeiro_uso=e.id)
                    avisos.append("buildId do Next.js ficou fixo em User Defined Variables: atualize a cada "
                                  "deploy do site (ou grave o HAR com o HTML para ele ser correlacionado)")
                    continue
                if origem is None:
                    adiante = [p.har_id for _, p in seq[i + 1:]] + [x for x in ids_har if x > e.id]
                    depois = next((x for x in adiante if resp.origem(x, value)), None)
                    pendentes.append((value, f"a origem #{depois} vem depois" if depois else "sem origem no HAR"))
                    continue
                eid, ex = origem
                ex.variavel = nomes.novo(_suggest_name(ex, local))
                variaveis[value] = Variavel(ex.variavel, value, "extractor", eid, ex, primeiro_uso=e.id)
                pendentes.clear()
                if dependencia is not None:
                    break
            if dependencia is not None:
                break
            for value, motivo in pendentes:
                fixos.setdefault(value, Fixo(e.id, local, motivo))
        if dependencia is not None:
            nome = next(v.nome for v in variaveis.values() if v.origem_har_id == dependencia)
            idx = etapas[k].passos.index(passo)
            etapas[k].passos.insert(idx, Passo(dependencia, f"Obter {titulo(nome)}",
                                               f"origem de {nome} (usado em #{e.id})"))
            continue                         # processa a dependência agora; `e` volta na sequência
        i += 1

    ordem = sorted(variaveis.values(), key=lambda v: v.tipo != "extractor")
    return Resolucao(etapas, ordem, list(fixos.values()), avisos)
