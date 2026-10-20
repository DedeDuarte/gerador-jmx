"""Roda o JMX gerado no JMeter real contra o mock local. `pytest -m e2e` (precisa do JMeter)."""
import csv
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

import mock_server
from harjmx.cli import main

pytestmark = pytest.mark.e2e


def _jmeter() -> str | None:
    home = os.environ.get("JMETER_HOME", r"C:\apache-jmeter-5.6.3")
    for candidate in (Path(home) / "bin" / "jmeter.bat", Path(home) / "bin" / "jmeter"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("jmeter")


def test_generated_jmx_runs_green(mini_work):
    jmeter = _jmeter()
    if not jmeter:
        pytest.skip("JMeter não encontrado (defina JMETER_HOME)")
    assert main(["build", str(mini_work)]) == 0
    jtl = mini_work / "out.jtl"
    server = mock_server.start()
    try:
        subprocess.run([jmeter, "-n", "-t", str(mini_work / "mini_login.jmx"), "-l", str(jtl),
                        "-JVUs=2", "-JRampup=1", "-Jduration=15",
                        "-Jjmeter.save.saveservice.output_format=csv"],
                       check=True, capture_output=True, timeout=240)
    finally:
        server.shutdown()
    rows = list(csv.DictReader(jtl.open(encoding="utf-8", errors="replace")))
    failures = [(r["label"], r["responseCode"], r["failureMessage"]) for r in rows if r["success"] != "true"]
    assert not failures
    steps = Counter(r["label"].split(" ", 1)[0] for r in rows)
    assert {"1.0", "1.1", "2.0", "3.0"} <= set(steps), steps
