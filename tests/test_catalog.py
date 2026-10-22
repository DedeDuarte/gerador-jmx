import json

from harjmx import catalog, har_reader


def _entries(tmp_path, *items):
    """items: (método, path, referer, segundos, tamanho da resposta)."""
    out = []
    for method, path, referer, sec, size in items:
        out.append({
            "startedDateTime": f"2026-10-20T12:00:{sec:02d}.000Z",
            "request": {"method": method, "url": f"https://loja.test{path}", "queryString": [],
                        "headers": [{"name": "Referer", "value": f"https://loja.test{referer}"},
                                    {"name": "Sec-Fetch-Mode", "value": "cors"}]},
            "response": {"status": 200, "headers": [],
                         "content": {"mimeType": "application/json", "text": json.dumps({"x": "a" * size})}},
        })
    path = tmp_path / "c.har"
    path.write_text(json.dumps({"log": {"entries": out}}), encoding="utf-8")
    return har_reader.load(path)[0]


def test_actions_show_destination_star_and_footer(tmp_path):
    entries = _entries(tmp_path,
                       ("GET", "/api/menu", "/", 0, 900),                          # #1 maior resposta
                       ("GET", "/_next/data/b1/c/jaquetas.json", "/", 0, 100),     # #2 leva à próxima página
                       ("GET", "/api/produtos", "/c/jaquetas", 5, 50),             # #3
                       ("POST", "/api/cart/addItem", "/c/jaquetas", 9, 10))        # #4 ação
    md, _ = catalog.build(entries, entries, {}, "loja.test", "c.har")
    assert "### A1 +0.0s — página / → /c/jaquetas" in md
    assert "★ #2 GET" in md and "★ #1" not in md          # a navegação vence a resposta maior
    assert "★ #4 POST" in md                               # verbo de ação
    assert md.rstrip().endswith("Fim do catálogo: 3 ações, 4 requisições.")
