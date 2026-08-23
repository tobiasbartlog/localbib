"""Characterization snapshot for POST /api/analysis/build.

Pins the response shape (nodes / edges / stats keys). The current
implementation is the 700-line `build_citation_network` function in
webapp.py, which issue #24 will replace with a thin orchestrator. This
test must remain green across that refactor.
"""
import webapp


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


def _seed_paper_with_doi(db, file_hash: str, doi: str, title: str, year: int = 2024) -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, doi)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (file_hash, f"{file_hash}.pdf", f"{file_hash}.pdf",
             title, "Doe, J.", year, doi),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_build_citation_network_returns_expected_shape(client, db, monkeypatch):
    _seed_paper_with_doi(db, "h1", "10.1000/own1", "Own Paper One")
    _seed_paper_with_doi(db, "h2", "10.1000/own2", "Own Paper Two")

    def fake_openalex_get(url, params=None, timeout=None, **kw):
        # Generic minimal /works batch response — no referenced_works,
        # so traversal terminates quickly and we just verify the shape.
        return _FakeResponse({
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1000/own1",
                    "title": "Own Paper One",
                    "authorships": [],
                    "publication_year": 2024,
                    "cited_by_count": 5,
                    "referenced_works": [],
                },
                {
                    "id": "https://openalex.org/W2",
                    "doi": "https://doi.org/10.1000/own2",
                    "title": "Own Paper Two",
                    "authorships": [],
                    "publication_year": 2024,
                    "cited_by_count": 3,
                    "referenced_works": [],
                },
            ]
        })

    monkeypatch.setattr(webapp.http_requests, "get", fake_openalex_get)
    monkeypatch.setattr(webapp.time, "sleep", lambda *a, **kw: None)

    resp = client.post("/api/analysis/build?depth=1")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Contract: top-level keys consumed by the frontend network view
    for key in ("nodes", "edges", "stats", "missing_sources",
                "own_paper_stats", "referenced_papers"):
        assert key in body, f"missing top-level key: {key}"
    assert isinstance(body["nodes"], list)
    assert isinstance(body["edges"], list)

    stats = body["stats"]
    for key in ("own_papers", "papers_found_in_openalex", "total_references",
                "shared_references", "missing_sources", "pdf_reference_edges",
                "depth"):
        assert key in stats, f"missing stats key: {key}"
    assert stats["depth"] == 1
