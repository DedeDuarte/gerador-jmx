import copy
import json

import pytest

from harjmx.commands import Workspace, main
from harjmx.plan import validate

from conftest import FIXTURES


def _run(work, plan):
    ws = Workspace(work)
    _, kept = ws.load()
    return validate(plan, kept, ws.excluded), {e.id: e for e in kept}


def test_reference_plan_is_valid(mini_work, mini_plan):
    res, _ = _run(mini_work, mini_plan)
    assert res.ok, res.erros
    seq = res.resolucao.sequencia()
    assert [p.har_id for p in seq] == [1, 5, 7, 9]
    assert [(p.har_id, p.motivo) for p in res.resolucao.automaticas()] == [(1, "origem de csrfToken (usado em #5)")]
    assert seq[0].descricao == "Obter Csrf Token"


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p["etapas"][0].update(requisicoes=[99]), "#99 não está no catálogo: id inexistente"),
    (lambda p: p["etapas"][0].update(requisicoes=[2]), "excluída como 'estatico' (rode o prepare com --include-static)"),
    (lambda p: p["etapas"][0].update(requisicoes=[4]), "excluída como 'terceiro' (rode o prepare com --host"),
    (lambda p: p["etapas"][1].update(requisicoes=[5]), "#5 aparece duas vezes"),
    (lambda p: p["etapas"][1].update(requisicoes=[]), "etapas[1].requisicoes"),
    (lambda p: p["etapas"][0].update(descricao="x"), "Extra inputs"),
    (lambda p: p.update(nome_teste="com espaço"), "nome_teste"),
])
def test_errors_are_actionable(mini_work, mini_plan, mutate, expected):
    p = copy.deepcopy(mini_plan)
    mutate(p)
    res, _ = _run(mini_work, p)
    assert not res.ok
    assert any(expected in e for e in res.erros), res.erros


def test_login_is_added_when_not_asked(mini_work, mini_plan):
    p = copy.deepcopy(mini_plan)
    del p["etapas"][0]                                   # o usuário não pediu o login
    res, _ = _run(mini_work, p)
    assert res.ok, res.erros
    etapas = res.resolucao.etapas
    assert [e.nome for e in etapas] == ["Login", "Listar Pedidos", "Confirmar Pedido"]
    assert [x.har_id for x in etapas[0].passos] == [1, 5]
    assert "login" in etapas[0].passos[1].motivo


def test_typed_values_become_csv(mini_work, mini_plan):
    res, _ = _run(mini_work, mini_plan)
    csv = {v.nome: v.valor for v in res.resolucao.variaveis if v.tipo == "csv"}
    assert csv == {"username": "ana@teste.com", "password": "Senha@123"}


def test_report_lists_what_python_decided(mini_work, mini_plan):
    res, by_id = _run(mini_work, mini_plan)
    report = res.report(by_id)
    assert "OK: 4 requisições (3 pedidas + 1 automáticas), 4 correlações, CSV: username, password" in report
    assert "#1 GET /login — origem de csrfToken" in report
    assert "orderId ← #7 json `$.orders[0].orderId`" in report
    assert "/api/orders/ORD-77881234/confirm muda estado" not in report      # 'confirm' não é add/new/...


def test_value_without_recorded_response_stays_fixed(tmp_path, mini_plan):
    har = json.loads((FIXTURES / "mini_login.har").read_text(encoding="utf-8"))
    del har["log"]["entries"][6]["response"]["content"]["text"]      # #7 sem corpo
    har_path = tmp_path / "sem_corpo.har"
    har_path.write_text(json.dumps(har), encoding="utf-8")
    work = tmp_path / "w"
    assert main(["prepare", str(har_path), "--out", str(work)]) == 0
    assert "respostas sem corpo gravado (#7)" in Workspace(work).catalog_md.read_text(encoding="utf-8")

    res, by_id = _run(work, mini_plan)
    assert res.ok, res.erros
    assert "orderId" not in {v.nome for v in res.resolucao.variaveis}
    assert any(f.har_id == 9 and f.motivo == "sem origem no HAR" for f in res.resolucao.fixos)
    assert "valor fixo em #9" in res.report(by_id)
