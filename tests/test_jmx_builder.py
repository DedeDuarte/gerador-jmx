import os
import shutil
import xml.etree.ElementTree as ET

import pytest

from harjmx.commands import Workspace, main
from harjmx.correlations import EtapaFinal, Passo
from harjmx.jmx_builder import Controller, arrange, endpoint_name, request_name, xml_value

from conftest import FIXTURES, GOLDEN


def test_endpoint_naming():
    assert endpoint_name("/api/products/${idProduto}") == "/api/products/{idProduto}"
    assert endpoint_name("/api/orders/123456/items?x=1") == "/api/orders/{id}/items"
    assert endpoint_name("/a/3f2b8c1e-1111-2222-3333-444455556666") == "/a/{id}"
    assert request_name(1, 0, "post", "/api/login", "Autenticar Usuário") == \
        "1.0 POST - /api/login - Autenticar Usuário"


def test_structure_follows_boas_praticas():
    etapas = [EtapaFinal("E1", [Passo(1, "d")]),
              EtapaFinal("E2", [Passo(2, "d"), Passo(3, "d")]),
              EtapaFinal("E3", [Passo(4, "d")])]
    top = arrange(etapas, {i: f"s{i}" for i in range(1, 5)})
    # 1.0 solta | "2 - E2" [2.0, 2.1] | 3.0 solta
    assert top[0] == "s1"
    assert isinstance(top[1], Controller) and top[1].nome == "2 - E2" and top[1].filhos == ["s2", "s3"]
    assert top[2] == "s4"


def test_xml_value_escapes_newlines():
    assert xml_value('{"a":\n"<b>&"}') == "{&quot;a&quot;:&#10;&quot;&lt;b&gt;&amp;&quot;}"
    assert xml_value(True) == "true" and xml_value(None) == ""


def test_build_matches_golden(mini_work):
    assert main(["build", str(mini_work)]) == 0
    jmx = (mini_work / "mini_login.jmx").read_text(encoding="utf-8")
    ET.fromstring(jmx.encode("utf-8"))
    golden = GOLDEN / "mini_login.jmx"
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(jmx, encoding="utf-8")
    assert jmx == golden.read_text(encoding="utf-8"), "rode com UPDATE_GOLDEN=1 se a mudança for intencional"
    csv_lines = (mini_work / "massa.csv").read_text(encoding="utf-8").splitlines()
    assert csv_lines == ["username,password", "ana@teste.com,Senha@123"]


def test_build_refuses_invalid_plan(mini_work):
    Workspace(mini_work).plan_file.write_text('{"nome_teste": "x", "etapas": []}', encoding="utf-8")
    assert main(["build", str(mini_work)]) == 1
    assert not (mini_work / "x.jmx").exists()


def test_prepare_does_not_overwrite_a_script(mini_work):
    assert main(["build", str(mini_work)]) == 0
    assert main(["prepare", str(FIXTURES / "mini_login.har"), "--out", str(mini_work)]) == 1
    assert main(["prepare", str(FIXTURES / "mini_login.har"), "--out", str(mini_work), "--force"]) == 0


def test_destino_com_til_vai_para_a_home(tmp_path, monkeypatch):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    assert main(["prepare", str(FIXTURES / "mini_login.har"), "--out", "~/jmeter/mini"]) == 0
    shutil.copy(FIXTURES / "mini_login.plan.json", Workspace("~/jmeter/mini").plan_file)
    assert main(["build", "~/jmeter/mini"]) == 0
    assert (tmp_path / "jmeter" / "mini" / "mini_login.jmx").exists()
    assert (tmp_path / "jmeter" / "mini" / "_geracao" / "catalog.md").exists()
