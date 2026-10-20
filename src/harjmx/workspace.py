"""Pasta de trabalho de um projeto (work/<proj>): lembra o HAR e os filtros usados no prepare."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harjmx.correlation import Candidato
from harjmx.har import filters, loader
from harjmx.models import Entry

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@dataclass
class Session:
    har: str
    extra_domains: list[str] = field(default_factory=list)
    include_static: bool = False
    denylist: str = str(CONFIG_DIR / "denylist.txt")


class Workspace:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: list[Entry] | None = None
        self.main_host = ""
        self.excluded: dict[int, str] = {}

    session_file = property(lambda self: self.path / "session.json")
    catalog_md = property(lambda self: self.path / "catalog.md")
    catalog_json = property(lambda self: self.path / "catalog.json")
    correlations_file = property(lambda self: self.path / "correlations.json")
    plan_file = property(lambda self: self.path / "plan.json")

    def save_session(self, session: Session) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps(asdict(session), indent=2, ensure_ascii=False),
                                     encoding="utf-8")

    def session(self) -> Session:
        if not self.session_file.exists():
            raise SystemExit(f"{self.path} não tem session.json. Rode `harjmx prepare` primeiro.")
        return Session(**json.loads(self.session_file.read_text(encoding="utf-8")))

    def load(self) -> tuple[list[Entry], list[Entry]]:
        """(todas as entries, entries mantidas pelos filtros)."""
        s = self.session()
        all_entries, self.main_host = loader.load(s.har)
        cfg = filters.FilterConfig(
            main_host=self.main_host,
            extra_domains=s.extra_domains,
            denylist=filters.load_denylist(Path(s.denylist)),
            include_static=s.include_static,
        )
        kept, self.excluded = filters.split(all_entries, cfg)
        self._entries = kept
        return all_entries, kept

    def correlations(self) -> list[Candidato]:
        if not self.correlations_file.exists():
            return []
        data = json.loads(self.correlations_file.read_text(encoding="utf-8"))
        return [Candidato.from_dict(d) for d in data]
