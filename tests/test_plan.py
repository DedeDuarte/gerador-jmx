import copy

import pytest

from harjmx.cli import Workspace
from harjmx.plan import validate


def _run(work, plan):
    ws = Workspace(work)
    _, kept = ws.load()
    return validate(plan, kept, ws.excluded, ws.correlations())


def test_reference_plan_is_valid(mini_work, mini_plan):
    res = _run(mini_work, mini_plan)
    assert res.ok, res.report()
    assert {v.nome: v.valor for v in res.variaveis}["senha"] == "Senha@123"


def _broken(plan, mutate):
    p = copy.deepcopy(plan)
    mutate(p)
    return p


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p["requisicoes"][0].update(har_id=99), "#99 não está no catálogo: id inexistente"),
    (lambda p: p["requisicoes"][0].update(har_id=4), "excluída como 'terceiro' (rode o prepare com --host"),
    (lambda p: p["requisicoes"][1].update(passo=2), "etapa 1: passos devem ser 0..1"),
    (lambda p: p["etapas"].append({"numero": 5, "nome": "X"}), "os números devem ser 1..4"),
    (lambda p: p["grupos"].append({"de": 1, "ate": 2, "nome": "Y"}), "sobrepõe outro grupo"),
    (lambda p: (p["requisicoes"].pop(0), p["requisicoes"][0].update(passo=0)), "correlação C1: a origem #1 não está no plano"),
    (lambda p: p["correlacoes"].append({"id": "C9"}), "correlação C9 não existe"),
    (lambda p: p["parametros"][0].update(local="form.nao_existe"), "'form.nao_existe' não existe em #5"),
    (lambda p: p["parametros"][0].update(variavel="userId"), "variável 'userId' definida duas vezes"),
    (lambda p: p["requisicoes"][2].setdefault("extractors", []).append(
        {"variavel": "x", "tipo": "json", "expressao": "$.nada"}), "`$.nada` não encontra nada"),
    (lambda p: p["requisicoes"][0].update(endpoint_nome="/login?x=1"), "endpoint_nome deve começar"),
    (lambda p: p["requisicoes"][0].update(campo_errado=1), "Extra inputs are not permitted"),
])
def test_errors_are_actionable(mini_work, mini_plan, mutate, expected):
    p = _broken(mini_plan, mutate)
    res = _run(mini_work, p)
    assert not res.ok
    assert any(expected in e for e in res.erros), res.report()



def test_aleatorio_generalizes_jsonpath(mini_work, mini_plan):
    mini_plan["correlacoes"][3] = {"id": "C4", "aleatorio": True}
    res = _run(mini_work, mini_plan)
    assert res.ok, res.report()
    order = next(v for v in res.variaveis if v.nome == "orderId")
    assert (order.extractor.expressao, order.extractor.match) == ("$.orders[*].orderId", 0)


def test_valor_de_when_response_was_not_recorded(tmp_path, mini_plan):
    import json

    from conftest import FIXTURES
    from harjmx.cli import main

    har = json.loads((FIXTURES / "mini_login.har").read_text(encoding="utf-8"))
    del har["log"]["entries"][6]["response"]["content"]["text"]      # #7 sem corpo
    har_path = tmp_path / "sem_corpo.har"
    har_path.write_text(json.dumps(har), encoding="utf-8")
    work = tmp_path / "w"
    assert main(["prepare", str(har_path), "--out", str(work)]) == 0
    assert "respostas sem corpo gravado" in Workspace(work).catalog_md.read_text(encoding="utf-8")

    mini_plan["correlacoes"] = [c for c in mini_plan["correlacoes"] if c["id"] != "C4"]
    extractor = {"variavel": "orderId", "tipo": "json", "expressao": "$.orders[0].orderId"}
    mini_plan["requisicoes"][2]["extractors"] = [extractor]
    res = _run(work, mini_plan)
    assert any("não foi gravada no HAR" in e for e in res.erros), res.report()

    extractor["valor_de"] = "9:path.2"
    res = _run(work, mini_plan)
    assert res.ok, res.report()
    assert next(v for v in res.variaveis if v.nome == "orderId").valor == "ORD-77881234"
