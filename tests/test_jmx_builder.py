import os
import xml.etree.ElementTree as ET

import pytest

from harjmx.commands import Workspace, main
from harjmx.jmx_builder import Controller, arrange, endpoint_name, request_name, xml_value
from harjmx.plan import Plan

from conftest import GOLDEN


def test_endpoint_naming():
    assert endpoint_name("/api/products/${idProduto}") == "/api/products/{idProduto}"
    assert endpoint_name("/api/orders/123456/items?x=1") == "/api/orders/{id}/items"
    assert endpoint_name("/a/3f2b8c1e-1111-2222-3333-444455556666") == "/a/{id}"
    assert request_name(1, 0, "post", "/api/login", "Autenticar Usuário") == \
        "1.0 POST - /api/login - Autenticar Usuário"


def _plan(etapas, reqs, grupos=()):
    return Plan.model_validate({
        "nome_teste": "t",
        "etapas": [{"numero": n, "nome": f"E{n}"} for n in etapas],
        "grupos": list(grupos),
        "requisicoes": [{"har_id": i, "etapa": e, "passo": p, "descricao": "d"} for i, e, p in reqs],
    })


def test_structure_follows_boas_praticas():
    plan = _plan([1, 2, 3, 4, 5],
                 [(1, 1, 0), (2, 2, 0), (3, 2, 1), (4, 3, 0), (5, 3, 1), (6, 4, 0), (7, 5, 0)],
                 [{"de": 3, "ate": 4, "nome": "Grupo"}])
    top = arrange(plan, {i: f"s{i}" for i in range(1, 8)})
    # 1.0 solta | "2 - E2" | "3-4 - Grupo" [ "3 - E3", 4.0 solta ] | 5.0 solta
    assert top[0] == "s1"
    assert isinstance(top[1], Controller) and top[1].nome == "2 - E2" and top[1].filhos == ["s2", "s3"]
    grupo = top[2]
    assert grupo.nome == "3-4 - Grupo"
    assert grupo.filhos[0].nome == "3 - E3" and grupo.filhos[1] == "s6"
    assert top[3] == "s7"


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
    assert csv_lines == ["usuario,senha", "ana@teste.com,Senha@123"]


def test_build_refuses_invalid_plan(mini_work):
    Workspace(mini_work).plan_file.write_text('{"nome_teste": "x", "etapas": [], "requisicoes": []}',
                                              encoding="utf-8")
    assert main(["build", str(mini_work)]) == 1
    assert not (mini_work / "x.jmx").exists()
