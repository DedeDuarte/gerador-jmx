import re

from harjmx import correlation
from harjmx.workspace import Workspace


def test_detects_all_dynamic_values(mini_work):
    cands = {c.variavel: c for c in Workspace(mini_work).correlations()}
    assert set(cands) == {"csrfToken", "userId", "accessToken", "orderId"}

    csrf = cands["csrfToken"]
    assert (csrf.origem.har_id, csrf.origem.tipo, csrf.origem.campo) == (1, "regex", "body")
    assert [(u.har_id, u.local) for u in csrf.usos] == [(5, "form.csrf_token")]

    token = cands["accessToken"]
    assert (token.origem.tipo, token.origem.expressao) == ("json", "$.access_token")
    assert [(u.har_id, u.local) for u in token.usos] == [(7, "header.Authorization"),
                                                        (9, "header.Authorization")]

    order = cands["orderId"]
    assert order.origem.expressao == "$.orders[0].orderId"
    assert {u.local for u in order.usos} == {"path.2", "body.$.orderId"}


def test_values_sent_by_client_are_not_correlations(mini_work):
    valores = {c.valor for c in Workspace(mini_work).correlations()}
    assert "ana@teste.com" not in valores          # vem do usuário, não de uma resposta
    assert "3.2.1" not in valores


def test_is_strong():
    assert correlation.is_strong("-8978932749661934233")
    assert correlation.is_strong("tok_a1b2c3")
    assert not correlation.is_strong("12345")
    assert not correlation.is_strong("profile")
    assert not correlation.is_strong("2026-10-20")
    assert not correlation.is_strong("1.23.0")


def test_build_regex_finds_exact_value():
    html = '<input type="hidden" name="a" value="x"><input type="hidden" name="csrf" value="k9j8h7g6">'
    pattern, match = correlation.build_regex(html, "k9j8h7g6")
    assert match == 1
    assert re.search(pattern, html).group(1) == "k9j8h7g6"
    assert pattern.startswith('name="csrf"')


def test_build_regex_uses_occurrence_instead_of_long_context():
    text = '[{"product_id":"111111-A","x":1},{"product_id":"222222-B","x":2}]'
    pattern, match = correlation.build_regex(text, "222222")
    assert pattern == '"product_id":"([^\\-\\r\\n]+)'
    assert match == 2
    assert [m.group(1) for m in re.finditer(pattern, text)][match - 1] == "222222"
