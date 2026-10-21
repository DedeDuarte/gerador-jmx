"""Linha de comando: prepare | show | match | validate | build.

Liga as etapas do pipeline (har_reader → correlations → catalog → plan → jmx_builder). Todos os comandos,
menos o prepare, recebem a pasta do script jornadas/scripts/<proj>, que lembra o HAR e os filtros usados.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harjmx import catalog, correlations, har_reader, jmx_builder, plan
from harjmx.correlations import Candidato
from harjmx.har_reader import Entry

# --- Pasta do script jornadas/scripts/<proj> ----------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@dataclass
class Session:
    har: str
    extra_domains: list[str] = field(default_factory=list)
    include_static: bool = False
    denylist: str = str(CONFIG_DIR / "denylist.txt")


class Workspace:
    """Pasta jornadas/scripts/<proj>: o .jmx e o CSV na raiz (o que o JMeter usa) e, em
    _geracao/, os resíduos da geração: session.json (HAR + filtros), catálogo, correlações e plano."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.main_host = ""
        self.excluded: dict[int, str] = {}

    gen = property(lambda self: self.path / "_geracao")
    session_file = property(lambda self: self.gen / "session.json")
    catalog_md = property(lambda self: self.gen / "catalog.md")
    catalog_json = property(lambda self: self.gen / "catalog.json")
    correlations_file = property(lambda self: self.gen / "correlations.json")
    plan_file = property(lambda self: self.gen / "plan.json")

    def save_session(self, session: Session) -> None:
        self.gen.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps(asdict(session), indent=2, ensure_ascii=False),
                                     encoding="utf-8")

    def session(self) -> Session:
        if not self.session_file.exists():
            raise SystemExit(f"{self.path} não tem session.json. Rode `harjmx prepare` primeiro.")
        return Session(**json.loads(self.session_file.read_text(encoding="utf-8")))

    def load(self) -> tuple[list[Entry], list[Entry]]:
        """(todas as entries, entries mantidas pelos filtros)."""
        s = self.session()
        all_entries, self.main_host = har_reader.load(s.har)
        cfg = har_reader.FilterConfig(
            main_host=self.main_host,
            extra_domains=s.extra_domains,
            denylist=har_reader.load_denylist(Path(s.denylist)),
            include_static=s.include_static,
        )
        kept, self.excluded = har_reader.split(all_entries, cfg)
        return all_entries, kept

    def correlations(self) -> list[Candidato]:
        if not self.correlations_file.exists():
            return []
        data = json.loads(self.correlations_file.read_text(encoding="utf-8"))
        return [Candidato.from_dict(d) for d in data]


# --- Comandos ----------------------------------------------------------------------------

def cmd_prepare(args) -> int:
    ws = Workspace(args.out)
    ws.save_session(Session(har=str(Path(args.har).resolve()), extra_domains=args.host or [],
                            include_static=args.include_static))
    all_entries, kept = ws.load()
    cands, sem_origem = correlations.detect(kept)
    ws.correlations_file.write_text(json.dumps([c.to_dict() for c in cands], indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    md, data = catalog.build(kept, all_entries, ws.excluded, ws.main_host, cands, sem_origem,
                             Path(args.har).name)
    if args.match:
        md += ("\n## Endpoints informados pelo usuário\n\n"
               + _match_lines(all_entries, ws.excluded, args.match) + "\n")
    ws.catalog_md.write_text(md, encoding="utf-8")
    ws.catalog_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(kept)} de {len(all_entries)} requisições mantidas, {len(cands)} correlações candidatas.")
    print(f"Catálogo: {ws.catalog_md}")
    return 0


def _match_lines(entries, excluded: dict[int, str], specs: list[str]) -> str:
    """Procura em todas as entries; as excluídas aparecem com o motivo."""
    by_id = {e.id: e for e in entries}
    lines = []
    for spec in specs:
        try:
            ids = har_reader.match(entries, spec)
        except ValueError as err:
            lines.append(f"- `{spec}`: {err}")
            continue
        kept = [f"#{i} {by_id[i].method} {by_id[i].path}" for i in ids if i not in excluded]
        out = [f"#{i} {by_id[i].method} {by_id[i].host}{by_id[i].path} (excluída: {excluded[i]})"
               for i in ids if i in excluded]
        lines.append(f"- `{spec}` → " + ("; ".join(kept) if kept else "nenhuma requisição no catálogo"))
        lines += [f"    - {o}" for o in out[:10]]
    return "\n".join(lines)


def cmd_match(args) -> int:
    ws = Workspace(args.script)
    all_entries, _ = ws.load()
    print(_match_lines(all_entries, ws.excluded, args.specs))
    return 0


def cmd_show(args) -> int:
    ws = Workspace(args.script)
    all_entries, _ = ws.load()
    by_id = {e.id: e for e in all_entries}
    for i in args.ids:
        e = by_id.get(i)
        if e is None:
            print(f"#{i}: não existe")
            continue
        r = _redact if args.redact else (lambda key, value: value)
        status = f"excluída: {ws.excluded[i]}" if i in ws.excluded else "no catálogo"
        url = e.url.split("?", 1)[0] + ("?" + "&".join(f"{k}={r(k, v)}" for k, v in e.query) if e.query else "")
        print(f"===== #{e.id} {e.method} {url}  ({status})")
        print("-- request headers")
        for k, v in e.headers:
            print(f"{k}: {r(k, v)}")
        if e.body:
            print(f"-- request body ({e.body.kind})")
            if args.redact and e.body.kind == "json":
                text = json.dumps(_redact_json(e.body.json), ensure_ascii=False)
            elif args.redact and e.body.kind == "form":
                text = "&".join(f"{k}={r(k, v)}" for k, v in e.body.params)
            else:
                text = e.body.text
            print(_cut(text, args.max))
        print(f"-- response {e.response.status} {e.response.mime}")
        for k, v in e.response.headers:
            print(f"{k}: {r(k, v)}")
        if e.response.text:
            print("-- response body")
            if e.response.is_json:
                data = _redact_json(e.response.json) if args.redact else e.response.json
                body = json.dumps(data, indent=1, ensure_ascii=False)
            else:
                body = e.response.text
            print(_cut(body, args.max))
        elif e.response.mime:
            print("-- response body NÃO GRAVADO no HAR")
        print()
    return 0


_SENSITIVE = re.compile(r"pass|senha|secret|token|auth|cookie|cpf|cvv|card|cartao|login|email|user", re.I)


def _redact(key: str, value: str) -> str:
    if not _SENSITIVE.search(key) or not value:
        return value
    return f"<{len(value)} chars>"


def _redact_json(data):
    if isinstance(data, dict):
        return {k: (_redact(k, str(v)) if isinstance(v, (str, int)) and not isinstance(v, bool)
                    else _redact_json(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact_json(v) for v in data]
    return data


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… (+{len(text) - limit} chars, use --max)"


def _validated(ws: Workspace, plan_path: Path):
    _, kept = ws.load()
    raw = plan.load_plan(plan_path)
    res = plan.validate(raw, kept, ws.excluded, ws.correlations())
    return res, {e.id: e for e in kept}


def cmd_validate(args) -> int:
    ws = Workspace(args.script)
    res, _ = _validated(ws, Path(args.plan) if args.plan else ws.plan_file)
    print(res.report())
    return 0 if res.ok else 1


def cmd_build(args) -> int:
    ws = Workspace(args.script)
    res, by_id = _validated(ws, Path(args.plan) if args.plan else ws.plan_file)
    print(res.report())
    if not res.ok:
        print("Build cancelado: corrija os erros do plano.")
        return 1
    config = jmx_builder.load_config(Path(args.config))
    result = jmx_builder.build(res.plan, res.variaveis, by_id, config)
    out = Path(args.out) if args.out else ws.path / f"{res.plan.nome_teste}.jmx"
    out.write_text(result.jmx, encoding="utf-8")
    print(f"JMX: {out}")
    if result.csv_header:
        csv_path = out.parent / res.plan.csv_arquivo
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(result.csv_header)
            w.writerow(result.csv_row)
        print(f"CSV: {csv_path} ({', '.join(result.csv_header)})")
    print(f"Hosts: {', '.join(f'${{{v}}}={k}' for k, v in result.hosts.items())}")
    if result.cookies:
        print(f"Cookies pré-carregados: {', '.join(c['name'] for c in result.cookies)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")           # console do Windows é cp1252
    p = argparse.ArgumentParser(prog="harjmx", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prepare", help="filtra o HAR e gera catalog.md + correlations.json")
    sp.add_argument("har")
    sp.add_argument("--out", required=True, help="pasta do script, ex: jornadas/scripts/renner_login")
    sp.add_argument("--host", action="append", help="domínio extra da aplicação (repetível)")
    sp.add_argument("--include-static", action="store_true")
    sp.add_argument("--match", action="append", help="endpoint informado pelo usuário: 'POST /login'")
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("show", help="mostra requisição e resposta completas de um ou mais IDs")
    sp.add_argument("script")
    sp.add_argument("ids", type=int, nargs="+")
    sp.add_argument("--max", type=int, default=3000, help="limite de caracteres por body")
    sp.add_argument("--redact", action="store_true",
                    help="mascara senhas, tokens, cookies, login/e-mail (mostra só o tamanho)")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("match", help="casa endpoints ('POST /login') com IDs do catálogo")
    sp.add_argument("script")
    sp.add_argument("specs", nargs="+")
    sp.set_defaults(func=cmd_match)

    for name, func, helptext in (("validate", cmd_validate, "valida o plan.json"),
                                 ("build", cmd_build, "valida e gera o .jmx")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("script")
        sp.add_argument("--plan", help="padrão: <script>/_geracao/plan.json")
        if name == "build":
            sp.add_argument("--out", help="padrão: <script>/<nome_teste>.jmx")
            sp.add_argument("--config", default=str(CONFIG_DIR / "defaults.yaml"))
        sp.set_defaults(func=func)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
