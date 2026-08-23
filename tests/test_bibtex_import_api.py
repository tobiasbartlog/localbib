"""BibTeX import endpoints — preview (parse + match, no writes) and commit.

The import itself is entity-agnostic; the SPA assigns the returned cite keys
to a plugin-side entity (ADR-0004).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webapp
import routers.bibtex_import as _bibtex_router

BIB = r"""
@article{doe_test_2024,
  title  = {Test Paper Title},
  author = {Doe, John},
  year   = {2024},
  doi    = {10.1000/test},
}

@article{neu2021,
  title   = {A Completely New Work on Trains},
  author  = {Neu, Nina},
  journal = {Rail Journal},
  year    = {2021},
  doi     = {10.5555/neu},
}
"""

TEX = r"Nur eines wird zitiert: \cite{neu2021}."


def _preview(client, bib=BIB, tex=None):
    files = {"bib_file": ("paper.bib", bib, "text/plain")}
    if tex is not None:
        files["tex_file"] = ("paper.tex", tex, "text/plain")
    resp = client.post("/api/import/bibtex/preview", files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_preview_matches_existing_paper_by_doi(client, db, seed_paper):
    data = _preview(client)
    assert data["total"] == 2
    assert data["matched_count"] == 1
    assert data["cited_count"] is None

    by_key = {e["key"]: e for e in data["entries"]}
    matched = by_key["doe_test_2024"]
    assert matched["match"]["paper_id"] == seed_paper
    assert matched["match"]["strategy"] == "doi"
    # Bestand gewinnt: proposed key is the library paper's key (here: empty backfill-less seed)
    assert matched["proposed_cite_key"] == matched["match"]["cite_key"]

    new = by_key["neu2021"]
    assert new["match"] is None
    assert new["proposed_cite_key"] == "neu2021"


def test_preview_tex_filter_flags_cited_entries(client, db):
    data = _preview(client, tex=TEX)
    by_key = {e["key"]: e for e in data["entries"]}
    assert by_key["neu2021"]["cited"] is True
    assert by_key["doe_test_2024"]["cited"] is False
    assert data["cited_count"] == 1


def test_preview_rejects_garbage(client):
    resp = client.post(
        "/api/import/bibtex/preview",
        files={"bib_file": ("x.bib", "@article{broken,", "text/plain")},
    )
    # bibtexparser is lenient; broken input yields no entries rather than 422
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert resp.json()["total"] == 0


def test_commit_creates_paper_with_bib_cite_key(client, db):
    resp = client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": "neu2021", "title": "A Completely New Work on Trains",
        "authors": "Neu, Nina", "year": 2021, "doi": "10.5555/neu",
        "journal": "Rail Journal",
    }]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == 1
    r = data["results"][0]
    assert r["status"] == "created"
    assert r["cite_key"] == "neu2021"
    assert r["has_pdf"] is False

    conn = db._connect()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (r["paper_id"],)).fetchone()
    conn.close()
    assert row["title"] == "A Completely New Work on Trains"
    assert row["filename"] == ""  # metadata-only paper
    assert row["cite_key"] == "neu2021"


def test_commit_stores_import_source_and_exposes_it(client, db):
    """source_name (the uploaded .bib filename) is persisted as import_source
    and surfaced via GET /api/papers so the SPA can stamp/filter by origin."""
    resp = client.post("/api/import/bibtex/commit", json={
        "entries": [{
            "key": "quelle2022", "title": "Werk mit Herkunft",
            "authors": "Quelle, Q.", "year": 2022,
        }],
        "source_name": "meine_referenzen.bib",
    })
    assert resp.status_code == 200, resp.text
    pid = resp.json()["results"][0]["paper_id"]

    conn = db._connect()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert row["import_source"] == "meine_referenzen.bib"
    assert row["original_filename"] == "BibTeX-Import: quelle2022"
    assert row["filename"] == ""  # no PDF on disk → SPA shows "kein PDF"

    papers = client.get("/api/papers").json()
    mine = next(p for p in papers if p["id"] == pid)
    assert mine["import_source"] == "meine_referenzen.bib"


def test_pdf_endpoint_404_for_paper_without_file(client, db):
    """PDF-lose Importe (leeres filename) -> 404, nicht 500. Vorher lief
    FileResponse auf das ALL_DIR-Verzeichnis und warf RuntimeError."""
    resp = client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": "nofile2023", "title": "Ohne Datei", "authors": "Leer, L.",
    }]})
    pid = resp.json()["results"][0]["paper_id"]
    for suffix in ("/pdf", "/download"):
        r = client.get(f"/api/papers/{pid}{suffix}")
        assert r.status_code == 404, f"{suffix}: {r.status_code}"


def test_commit_reuses_matched_paper(client, db, seed_paper):
    resp = client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": "doe_test_2024", "title": "Test Paper Title",
        "authors": "Doe, John", "year": 2024, "doi": "10.1000/test",
        "matched_paper_id": seed_paper,
    }]})
    data = resp.json()
    assert data["matched"] == 1 and data["created"] == 0
    assert data["results"][0]["paper_id"] == seed_paper
    assert data["results"][0]["has_pdf"] is True


def test_commit_dedupes_cite_key_collision(client, db):
    # First import takes the key…
    client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": "kollision", "title": "Erstes Werk", "authors": "Eins, E."}]})
    # …a different work with the same .bib key gets a suffix.
    resp = client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": "kollision", "title": "Zweites voellig anderes Werk", "authors": "Zwei, Z."}]})
    r = resp.json()["results"][0]
    assert r["status"] == "created"
    assert r["cite_key"] == "kollisiona"


def test_commit_skips_duplicate_since_preview(client, db):
    entry = {"key": "dup2020", "title": "Doppelt importiertes Werk", "authors": "Dup, D."}
    first = client.post("/api/import/bibtex/commit", json={"entries": [entry]}).json()
    second = client.post("/api/import/bibtex/commit", json={"entries": [entry]}).json()
    assert first["created"] == 1
    assert second["created"] == 0 and second["matched"] == 1
    assert second["results"][0]["paper_id"] == first["results"][0]["paper_id"]


@pytest.mark.real_enrichment
def test_commit_enriches_created_paper_with_abstract(client, db):
    """Frisch importierte Eintraege werden nicht-destruktiv angereichert
    (Abstract via CrossRef). Netzwerk ist gemockt."""
    from unittest.mock import patch

    with patch.object(
        webapp.metadata_validation, "fetch_crossref_abstract",
        return_value="Ein Abstract aus CrossRef.",
    ), patch.object(_bibtex_router, "OpenAlexClient") as OA:
        OA.return_value.fetch_works_by_doi.return_value = []
        resp = client.post("/api/import/bibtex/commit", json={"entries": [{
            "key": "enrichme2022", "title": "Anzureicherndes Werk",
            "authors": "Neu, Nina", "year": 2022, "doi": "10.5555/enrich",
        }]})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == 1
    assert data["enriched"] == 1
    pid = data["results"][0]["paper_id"]
    conn = db._connect()
    row = conn.execute("SELECT abstract FROM papers WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert "CrossRef" in row["abstract"]
