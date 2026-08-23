"""Tests for the citekey-based agent retrieval endpoints (#104,
docs/PRD-semantische-suche.md Phase 4):

  GET /api/search/reference/{citekey}
  GET /api/search/reference/{citekey}/chunks

Plain lookup by identity (no ranking, no embedding model involved) --
complements the search endpoints in routers/search.py: a search hit carries
a citekey, these resolve it to full metadata/abstract or raw chunks.
"""
from __future__ import annotations


def _seed_chunk(db, paper_id, chunk_text, chunk_index=0, page_start=1, page_end=None):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (paper_id, chunk_index, page_start, page_end or page_start, chunk_text),
        )
        chunk_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return chunk_id


# seed_paper (tests/conftest.py) inserts authors="Doe, John", year=2024 with
# no explicit cite_key -- the legacy fallback (cite_key_generator.base_key)
# yields "Doe2024" deterministically.
SEED_CITEKEY = "Doe2024"


class TestGetReference:
    def test_returns_metadata_by_citekey(self, client, db, seed_paper):
        resp = client.get(f"/api/search/reference/{SEED_CITEKEY}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["citekey"] == SEED_CITEKEY
        assert body["title"] == "Test Paper Title"
        assert body["authors"] == "Doe, John"
        assert body["year"] == 2024
        assert body["doi"] == "10.1000/test"
        assert body["abstract"] == ""

    def test_unknown_citekey_returns_404(self, client, db):
        resp = client.get("/api/search/reference/NoSuchKey9999")
        assert resp.status_code == 404

    def test_pure_json_no_session_required(self, client, db, seed_paper):
        """Acceptance #104: usable via curl without a browser session."""
        resp = client.get(f"/api/search/reference/{SEED_CITEKEY}")
        assert resp.headers["content-type"].startswith("application/json")


class TestGetReferenceChunks:
    def test_returns_chunks_with_page_start(self, client, db, seed_paper):
        _seed_chunk(db, seed_paper, "first chunk of text", chunk_index=0, page_start=1)
        _seed_chunk(db, seed_paper, "second chunk of text", chunk_index=1, page_start=2)

        resp = client.get(f"/api/search/reference/{SEED_CITEKEY}/chunks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["citekey"] == SEED_CITEKEY
        assert body["total"] == 2
        assert len(body["chunks"]) == 2
        assert body["chunks"][0]["page_start"] == 1
        assert body["chunks"][0]["chunk_text"] == "first chunk of text"

    def test_pagination_limit_and_offset(self, client, db, seed_paper):
        for i in range(5):
            _seed_chunk(db, seed_paper, f"chunk {i}", chunk_index=i, page_start=i + 1)

        resp = client.get(f"/api/search/reference/{SEED_CITEKEY}/chunks", params={"limit": 2, "offset": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["chunks"]) == 2
        assert body["chunks"][0]["chunk_text"] == "chunk 2"

    def test_paper_without_chunks_returns_empty_list(self, client, db, seed_paper):
        resp = client.get(f"/api/search/reference/{SEED_CITEKEY}/chunks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["chunks"] == []

    def test_unknown_citekey_returns_404(self, client, db):
        resp = client.get("/api/search/reference/NoSuchKey9999/chunks")
        assert resp.status_code == 404
