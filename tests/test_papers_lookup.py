"""GET /api/papers/lookup: the light lookup behind citation chips (ADR-0026)."""
from __future__ import annotations


def _insert(db, file_hash, title, authors, year, cite_key, doi="", filename="f.pdf"):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, cite_key, doi)
               VALUES (?, ?, 'f.pdf', ?, ?, ?, ?, ?)""",
            (file_hash, filename, title, authors, year, cite_key, doi),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed(db):
    return {
        "peck": _insert(db, "h1", "Earth plaster in old buildings", "Peck, Anna", 2005, "Peck2005",
                        doi="10.1000/earth.1"),
        "niu": _insert(db, "h2", "Dotted keys", "Niu, Li; Chen, Bo", 2026, "Niu.2026", filename=""),
        "minke": _insert(db, "h3", "Handbook of earth construction", "Minke, Gernot", 2012, "Minke2012"),
    }


def test_resolves_citekeys_in_order_with_light_rows(client, db):
    ids = _seed(db)
    resp = client.get("/api/papers/lookup", params=[("keys", "Niu.2026"), ("keys", "Peck2005"), ("keys", "Gone1999")])
    assert resp.status_code == 200
    papers = resp.json()["papers"]
    assert [p["cite_key"] for p in papers] == ["Niu.2026", "Peck2005"]
    assert papers[0] == {
        "id": ids["niu"], "cite_key": "Niu.2026", "title": "Dotted keys",
        "authors": "Niu, Li; Chen, Bo", "year": 2026, "doi": "", "has_pdf": False,
    }
    assert papers[1]["has_pdf"] is True


def test_keys_also_comma_separated(client, db):
    _seed(db)
    papers = client.get("/api/papers/lookup?keys=Minke2012,Peck2005").json()["papers"]
    assert [p["cite_key"] for p in papers] == ["Minke2012", "Peck2005"]


def test_query_finds_title_author_citekey_and_doi(client, db):
    _seed(db)

    def keys(q):
        return [p["cite_key"] for p in client.get("/api/papers/lookup", params={"q": q}).json()["papers"]]

    assert keys("handbook") == ["Minke2012"]
    assert keys("chen") == ["Niu.2026"]
    assert keys("niu.20") == ["Niu.2026"]
    assert keys("10.1000/earth") == ["Peck2005"]
    # A citekey prefix ranks before a mere title hit.
    assert keys("earth")[0] in {"Peck2005", "Minke2012"}
    assert keys("pe")[0] == "Peck2005"


def test_query_folds_umlauts_and_takes_wildcards_literally(client, db):
    _insert(db, "h4", "Über Lehmputz", "Özdemir, Ali", 2020, "Oezdemir2020")
    _insert(db, "h5", "Snake case keys", "Smith, Jo", 2020, "smith_2020")
    _insert(db, "h6", "Dashed keys", "Smith, Jo", 2021, "smithX2020")

    def keys(q):
        return [p["cite_key"] for p in client.get("/api/papers/lookup", params={"q": q}).json()["papers"]]

    assert keys("özdemir") == ["Oezdemir2020"]
    assert keys("ÖZDEMIR") == ["Oezdemir2020"]
    assert keys("über") == ["Oezdemir2020"]
    assert keys("smith_2020") == ["smith_2020"]
    assert keys("%") == []


def test_empty_query_lists_newest_first_and_respects_limit(client, db):
    _seed(db)
    papers = client.get("/api/papers/lookup", params={"limit": 2}).json()["papers"]
    assert [p["cite_key"] for p in papers] == ["Niu.2026", "Minke2012"]
    assert client.get("/api/papers/lookup", params={"limit": 0}).status_code == 422


def test_editor_modules_are_revalidated_after_an_update(client):
    """Relative ES-module imports carry no ``?v=`` stamp, so the server makes
    the browser ask again instead of pairing a new app.js with an old module."""
    for path in ("/static/markdown/editor.mjs", "/static/markdown/syntax.mjs"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/javascript")
        assert resp.headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/static/style.css").headers


def test_lookup_is_not_shadowed_by_the_paper_route(client, db):
    assert client.get("/api/papers/lookup").status_code == 200
    assert client.get("/api/papers/lookup?keys=").json() == {"papers": []}
