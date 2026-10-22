import json
import re

from harjmx import correlations, har_reader
from harjmx.commands import Workspace


def _har(tmp_path, *entries):
    """HAR mínimo: cada entry é (método, path, corpo enviado | None, resposta json | None)."""
    out = []
    for i, (method, path, sent, resp) in enumerate(entries):
        req = {"method": method, "url": f"https://app.test{path}", "headers": [], "queryString": []}
        if sent is not None:
            req["postData"] = {"mimeType": "application/json", "text": json.dumps(sent)}
        content = {"mimeType": "application/json", "text": json.dumps(resp)} if resp is not None else {}
        out.append({"startedDateTime": f"2026-10-20T12:00:{i:02d}.000Z", "request": req,
                    "response": {"status": 200, "headers": [], "content": content}})
    path = tmp_path / "t.har"
    path.write_text(json.dumps({"log": {"entries": out}}), encoding="utf-8")
    return har_reader.load(path)[0]


def test_reference_plan_correlations(mini_work, mini_plan):
    ws = Workspace(mini_work)
    _, kept = ws.load()
    res = correlations.resolve([(e["nome"], e["requisicoes"]) for e in mini_plan["etapas"]], kept)
    var = {v.nome: v for v in res.variaveis if v.tipo == "extractor"}
    assert set(var) == {"csrfToken", "userId", "accessToken", "orderId"}
    csrf = var["csrfToken"]
    assert (csrf.origem_har_id, csrf.extractor.tipo, csrf.extractor.campo) == (1, "regex", "body")
    assert (var["accessToken"].origem_har_id, var["accessToken"].extractor.expressao) == (5, "$.access_token")
    assert var["orderId"].extractor.expressao == "$.orders[0].orderId"
    assert "ana@teste.com" not in {v.valor for v in var.values()}    # vem do usuário: vai para o CSV


def test_nearest_origin_wins(tmp_path):
    kept = _har(tmp_path,
                ("GET", "/lista", None, {"tok": "t0k3n12345"}),          # #1 prefetch
                ("GET", "/outra", None, {}),                             # #2
                ("GET", "/lista", None, {"tok": "t0k3n12345"}),          # #3 a visita de verdade
                ("POST", "/usa", {"tok": "t0k3n12345"}, {}))             # #4
    res = correlations.resolve([("Ver", [2]), ("Usar", [4])], kept)
    assert [p.har_id for p in res.sequencia()] == [2, 3, 4]           # entrou a mais próxima, não a #1
    res = correlations.resolve([("Ver", [3]), ("Usar", [4])], kept)
    assert res.automaticas() == [] and res.variaveis[0].origem_har_id == 3


def test_origin_after_first_use(tmp_path):
    kept = _har(tmp_path,
                ("POST", "/antes", {"s": "s3ss10n999"}, None),           # #1 usa sem origem gravada
                ("GET", "/sessao", None, {"n": "s3ss10n999"}),           # #2 responde o valor
                ("POST", "/depois", {"s": "s3ss10n999"}, {}))            # #3
    res = correlations.resolve([("Depois", [3])], kept)
    assert [p.har_id for p in res.sequencia()] == [2, 3]
    assert res.variaveis[0].extractor.expressao == "$.n"
    res = correlations.resolve([("Antes", [1]), ("Depois", [3])], kept)
    assert [p.har_id for p in res.sequencia()] == [1, 2, 3]
    # #1 continua mandando o literal (e isso vira risco); #3 já usa a variável
    assert [(f.har_id, f.motivo) for f in res.fixos] == [(1, "a origem #2 vem depois")]


def test_echo_is_not_origin(tmp_path):
    kept = _har(tmp_path,
                ("POST", "/eco", {"d": "dev1c3abc9"}, {"d": "dev1c3abc9"}),   # devolve o que recebeu
                ("POST", "/usa", {"d": "dev1c3abc9"}, {}))
    res = correlations.resolve([("Usar", [2])], kept)
    assert res.automaticas() == [] and res.variaveis == []
    assert res.fixos[0].motivo == "sem origem no HAR"


def test_next_build_id_becomes_global_variable(tmp_path):
    kept = _har(tmp_path,
                ("GET", "/_next/data/b1dXyzAbCdEfGhIjKl/c/jaquetas.json", None, {"itens": []}),
                ("GET", "/_next/data/b1dXyzAbCdEfGhIjKl/c/camisetas.json", None, {"itens": []}))
    res = correlations.resolve([("Ver Jaquetas", [1]), ("Ver Camisetas", [2])], kept)
    build = [v for v in res.variaveis if v.nome == "buildId"]
    assert len(build) == 1 and build[0].tipo == "global" and build[0].valor == "b1dXyzAbCdEfGhIjKl"
    assert len(res.avisos) == 1 and "buildId do Next.js" in res.avisos[0]
    assert res.fixos == []


def test_is_strong():
    assert correlations.is_strong("-8978932749661934233")
    assert correlations.is_strong("tok_a1b2c3")
    assert not correlations.is_strong("12345")
    assert not correlations.is_strong("profile")
    assert not correlations.is_strong("2026-10-20")
    assert not correlations.is_strong("1.23.0")
    assert not correlations.is_strong("actionNewAddress")          # palavra/camelCase sem dígito
    assert not correlations.is_strong("rennerBrasilDesktop")


def test_build_regex_finds_exact_value():
    html = '<input type="hidden" name="a" value="x"><input type="hidden" name="csrf" value="k9j8h7g6">'
    pattern, match = correlations.build_regex(html, "k9j8h7g6")
    assert match == 1
    assert re.search(pattern, html).group(1) == "k9j8h7g6"
    assert pattern.startswith('name="csrf"')


def test_build_regex_uses_occurrence_instead_of_long_context():
    text = '[{"product_id":"111111-A","x":1},{"product_id":"222222-B","x":2}]'
    pattern, match = correlations.build_regex(text, "222222")
    assert pattern == '"product_id":"([^\\-\\r\\n]+)'
    assert match == 2
    assert [m.group(1) for m in re.finditer(pattern, text)][match - 1] == "222222"
