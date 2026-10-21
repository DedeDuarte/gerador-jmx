from harjmx import har
from harjmx.cli import Session, Workspace

from conftest import FIXTURES


def test_base_domain():
    assert har.base_domain("www.lojasrenner.com.br") == "lojasrenner.com.br"
    assert har.base_domain("api.site.com") == "site.com"
    assert har.base_domain("127.0.0.1") == "127.0.0.1"
    assert har.base_domain("localhost") == "localhost"


def test_exclusion_reasons(tmp_path):
    ws = Workspace(tmp_path)
    ws.save_session(Session(har=str(FIXTURES / "mini_login.har")))
    _, kept = ws.load()
    assert [e.id for e in kept] == [1, 5, 7, 9]
    assert ws.excluded == {2: "estatico", 3: "denylist", 4: "terceiro", 6: "preflight", 8: "estatico"}


def test_extra_host_is_kept(tmp_path):
    ws = Workspace(tmp_path)
    ws.save_session(Session(har=str(FIXTURES / "mini_login.har"), extra_domains=["terceiro.com"]))
    _, kept = ws.load()
    assert 4 in [e.id for e in kept]


def test_form_body_keeps_params():
    entries, host = har.load(FIXTURES / "mini_login.har")
    login = entries[4]
    assert host == "127.0.0.1"
    assert login.body.kind == "form"
    assert ("username", "ana@teste.com") in login.body.params


def test_matcher():
    entries, _ = har.load(FIXTURES / "mini_login.har")
    assert har.match(entries, "POST /api/login") == [5]
    assert har.match(entries, "POST /login") == [5]          # sufixo
    assert har.match(entries, "GET /api/users/*/orders") == [7]
    assert har.match(entries, "/confirm") == [9]


def test_match_reports_excluded_entries(tmp_path):
    from harjmx.cli import _match_lines

    ws = Workspace(tmp_path)
    ws.save_session(Session(har=str(FIXTURES / "mini_login.har")))
    all_entries, _ = ws.load()
    out = _match_lines(all_entries, ws.excluded, ["* /api/users/*/orders", "GET *.png"])
    assert "#7 GET /api/users/4242001/orders" in out
    assert "(excluída: preflight)" in out                      # o OPTIONS #6
    assert "nenhuma requisição no catálogo" in out and "(excluída: estatico)" in out
