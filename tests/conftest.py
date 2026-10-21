import json
import shutil
from pathlib import Path

import pytest

from harjmx.commands import Workspace, main

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def mini_work(tmp_path) -> Path:
    """Pasta do script preparada a partir do HAR sintético, com o plano de referência copiado."""
    work = tmp_path / "mini"
    assert main(["prepare", str(FIXTURES / "mini_login.har"), "--out", str(work)]) == 0
    shutil.copy(FIXTURES / "mini_login.plan.json", Workspace(work).plan_file)
    return work


@pytest.fixture
def mini_plan() -> dict:
    return json.loads((FIXTURES / "mini_login.plan.json").read_text(encoding="utf-8"))
