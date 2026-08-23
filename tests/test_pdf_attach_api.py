"""PDF attach/download endpoints — attach-pdf, fetch-oa-pdf, oa-check."""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
import respx

import webapp
import pdf_chunking
import routers.bibtex_import as _bibtex_router
from openalex_client import BASE_URL

PDF_BYTES = b"%PDF-1.4 fake attached pdf content"


def _make_book_pdf(n_pages: int, toc: list | None = None) -> bytes:
    """Baut ein echtes (leeres) PDF mit n Seiten und optionalem Inhaltsverzeichnis."""
    import fitz
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page()
    if toc:
        doc.set_toc(toc)
    data = doc.tobytes()
    doc.close()
    return data


def test_detect_book_structure_finds_chapter_via_toc(tmp_path):
    toc = [
        [1, "Introduction", 1], [1, "Second Chapter", 5],
        [1, "My Special Chapter About Databases", 10],
        [1, "Fourth Chapter", 20], [1, "Fifth Chapter", 40],
    ]
    p = tmp_path / "book.pdf"
    p.write_bytes(_make_book_pdf(70, toc))
    r = webapp.detect_book_structure(str(p), "My Special Chapter About Databases", "")
    assert r and r["detected"]
    assert r["range"]["start_page"] == 10
    assert r["range"]["end_page"] == 19  # bis zur naechsten L1-Kapitelseite - 1
    assert r["range"]["keep_cover"] is True


def test_detect_book_structure_skips_normal_paper(tmp_path):
    p = tmp_path / "paper.pdf"
    p.write_bytes(_make_book_pdf(12))
    assert webapp.detect_book_structure(str(p), "Kurzes Paper", "") is None


def test_attach_finalize_trims_book_and_keeps_cover(
    client, db, no_llm_categorize, fake_text_extraction
):
    paper_id = _metadata_only_paper(client, key="bookme2020", title="Book Me")
    toc = [[1, "A", 1], [1, "B", 5], [1, "C", 10], [1, "D", 20], [1, "E", 40]]
    data = _make_book_pdf(70, toc)
    # Phase 1: Datei landen, nicht verarbeiten, Buch-Erkennung anfordern.
    r1 = client.post(
        f"/api/papers/{paper_id}/attach-pdf?do_categories=false&do_chunks=false&detect_book=true",
        files={"file": ("book.pdf", data, "application/pdf")},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["book"] and r1.json()["book"]["detected"] is True

    # Phase 2: auf Seiten 10-19 + Cover zuschneiden.
    r2 = client.post(
        f"/api/papers/{paper_id}/attach-finalize",
        json={"trim": {"start_page": 10, "end_page": 19, "keep_cover": True},
              "do_categories": False, "do_chunks": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["trimmed_pages"] == 11  # Cover + 10 Kapitelseiten

    conn = db._connect()
    row = conn.execute("SELECT page_count, filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
    conn.close()
    assert row["page_count"] == 11
    import fitz
    doc = fitz.open(os.path.join(webapp.Config.ALL_DIR, os.path.basename(row["filename"])))
    assert len(doc) == 11
    doc.close()


def test_attach_finalize_whole_book_runs_processing(client, db, fake_text_extraction):
    paper_id = _metadata_only_paper(client, key="wholebook2020", title="Whole Book")
    data = _make_book_pdf(70, [[1, "A", 1], [1, "B", 5], [1, "C", 10], [1, "D", 20]])
    client.post(
        f"/api/papers/{paper_id}/attach-pdf?do_categories=false&do_chunks=false&detect_book=true",
        files={"file": ("book.pdf", data, "application/pdf")},
    )
    # categorize_with_llm moved to routers/bibtex_import.py (#84)
    with patch.object(_bibtex_router, "categorize_with_llm", return_value=[]) as cat:
        r = client.post(
            f"/api/papers/{paper_id}/attach-finalize",
            json={"trim": None, "do_categories": True, "do_chunks": False},
        )
    assert r.status_code == 200, r.text
    assert r.json()["trimmed_pages"] is None  # nicht zugeschnitten
    assert cat.called  # Kategorisierung lief in Phase 2

    conn = db._connect()
    pc = conn.execute("SELECT page_count FROM papers WHERE id = ?", (paper_id,)).fetchone()[0]
    conn.close()
    assert pc == 70  # unveraendert


def _metadata_only_paper(client, key="attachme2020", title="Attach Me", doi=""):
    resp = client.post("/api/import/bibtex/commit", json={"entries": [{
        "key": key, "title": title, "authors": "Doe, Jane", "year": 2020, "doi": doi,
    }]})
    return resp.json()["results"][0]["paper_id"]


@pytest.fixture
def no_llm_categorize():
    # categorize_with_llm is now used inside routers/bibtex_import.py (#84)
    with patch.object(_bibtex_router, "categorize_with_llm", return_value=[]) as m:
        yield m


@pytest.fixture
def fake_text_extraction():
    # extract_text_from_pdf is now used inside routers/bibtex_import.py (#84)
    with patch.object(_bibtex_router, "extract_text_from_pdf", return_value="Volltext aus PDF"):
        yield


def test_attach_pdf_sets_hash_file_and_text(client, db, no_llm_categorize, fake_text_extraction):
    paper_id = _metadata_only_paper(client)
    resp = client.post(
        f"/api/papers/{paper_id}/attach-pdf",
        files={"file": ("manuell.pdf", PDF_BYTES, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"].endswith(".pdf")

    conn = db._connect()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    conn.close()
    assert row["filename"] == body["filename"]
    assert row["ocr_text"] == "Volltext aus PDF"
    assert len(row["file_hash"]) == 64
    # curated metadata untouched
    assert row["title"] == "Attach Me"
    # categorization ran (decision: Anhaengen + Kategorisierung)
    assert no_llm_categorize.called


def test_attach_pdf_creates_chunks(client, db, no_llm_categorize, fake_text_extraction):
    """Ein angehaengtes PDF muss gechunkt werden (sonst kein Research-Chat/RAG)."""
    paper_id = _metadata_only_paper(client, key="chunkme2020", title="Chunk Me")
    # Fake-Bytes sind kein echtes PDF -> Seiten-Extraktion mocken (echtes PDF
    # liefert hier reale Seiten); der Chunker schreibt dann paper_chunks.
    # _extract_pages_text moved to the neutral pdf_chunking module (#87)
    with patch.object(pdf_chunking, "_extract_pages_text", return_value=[(
        1,
        "Seite eins mit ausreichend Text fuer mindestens einen Chunk. "
        "Der Chunker verlangt mehr als fuenfzig Zeichen pro Chunk, daher dieser Satz.",
    )]):
        resp = client.post(
            f"/api/papers/{paper_id}/attach-pdf",
            files={"file": ("c.pdf", PDF_BYTES, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("chunks", 0) >= 1
    conn = db._connect()
    n = conn.execute(
        "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", (paper_id,)
    ).fetchone()[0]
    conn.close()
    assert n >= 1


def test_attach_pdf_skips_categories_and_chunks_when_disabled(
    client, db, no_llm_categorize, fake_text_extraction
):
    """do_categories/do_chunks=false ueberspringen die optionalen Schritte
    (die Optionen-Auswahl im Upload-Modal steuert diese Flags)."""
    paper_id = _metadata_only_paper(client, key="skipme2020", title="Skip Me")
    # _extract_pages_text moved to the neutral pdf_chunking module (#87)
    with patch.object(pdf_chunking, "_extract_pages_text", return_value=[(
        1, "Seite mit ausreichend Text fuer einen Chunk, mehr als fuenfzig Zeichen lang.",
    )]):
        resp = client.post(
            f"/api/papers/{paper_id}/attach-pdf?do_categories=false&do_chunks=false",
            files={"file": ("s.pdf", PDF_BYTES, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("chunks", 0) == 0
    assert resp.json().get("categories") == []
    assert not no_llm_categorize.called

    conn = db._connect()
    n = conn.execute(
        "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", (paper_id,)
    ).fetchone()[0]
    conn.close()
    assert n == 0


def test_bulk_delete_removes_papers(client, db):
    r = client.post("/api/import/bibtex/commit", json={"entries": [
        {"key": "del_a2020", "title": "Werk A", "authors": "X", "year": 2020},
        {"key": "del_b2021", "title": "Werk B", "authors": "Y", "year": 2021},
    ]})
    ids = [x["paper_id"] for x in r.json()["results"]]
    resp = client.post("/api/papers/bulk-delete", json={"paper_ids": ids})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2
    conn = db._connect()
    n = conn.execute(
        f"SELECT COUNT(*) FROM papers WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchone()[0]
    conn.close()
    assert n == 0


def test_attach_pdf_conflict_when_paper_has_pdf(client, db, seed_paper):
    resp = client.post(
        f"/api/papers/{seed_paper}/attach-pdf",
        files={"file": ("x.pdf", PDF_BYTES, "application/pdf")},
    )
    assert resp.status_code == 409


def test_attach_pdf_conflict_when_hash_belongs_to_other_paper(
    client, db, no_llm_categorize, fake_text_extraction
):
    first = _metadata_only_paper(client, key="erste2020", title="Erstes Werk Alpha")
    second = _metadata_only_paper(client, key="zweite2021", title="Zweites Werk Beta")
    ok = client.post(f"/api/papers/{first}/attach-pdf",
                     files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
    assert ok.status_code == 200
    dup = client.post(f"/api/papers/{second}/attach-pdf",
                      files={"file": ("b.pdf", PDF_BYTES, "application/pdf")})
    assert dup.status_code == 409
    assert str(first) in dup.json()["detail"]


def test_attach_pdf_rejects_non_pdf(client, db):
    paper_id = _metadata_only_paper(client, key="nopdf2020", title="Kein PDF Werk")
    resp = client.post(
        f"/api/papers/{paper_id}/attach-pdf",
        files={"file": ("x.pdf", b"<html>paywall</html>", "application/pdf")},
    )
    assert resp.status_code == 422


def test_attach_pdf_404(client, db):
    resp = client.post("/api/papers/999999/attach-pdf",
                       files={"file": ("x.pdf", PDF_BYTES, "application/pdf")})
    assert resp.status_code == 404


@respx.mock
def test_fetch_oa_pdf_downloads_and_attaches(client, db, no_llm_categorize, fake_text_extraction):
    paper_id = _metadata_only_paper(client, key="oadl2020", title="OA Download Werk")
    respx.get("https://repo.example.org/paper.pdf").mock(
        return_value=httpx.Response(200, content=PDF_BYTES,
                                    headers={"content-type": "application/pdf"})
    )
    resp = client.post(f"/api/papers/{paper_id}/fetch-oa-pdf",
                       json={"url": "https://repo.example.org/paper.pdf"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["source_url"] == "https://repo.example.org/paper.pdf"

    conn = db._connect()
    row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
    conn.close()
    assert row["filename"] != ""


@respx.mock
def test_fetch_oa_pdf_rejects_landing_page(client, db):
    paper_id = _metadata_only_paper(client, key="landing2020", title="Landing Page Werk")
    respx.get("https://journal.example.org/view").mock(
        return_value=httpx.Response(200, content=b"<html>Article page</html>",
                                    headers={"content-type": "text/html"})
    )
    resp = client.post(f"/api/papers/{paper_id}/fetch-oa-pdf",
                       json={"url": "https://journal.example.org/view"})
    assert resp.status_code == 422
    assert "text/html" in resp.json()["detail"]


@respx.mock
def test_oa_check_returns_pdf_url_and_fallback_links(client, db):
    respx.get(f"{BASE_URL}/works").mock(
        return_value=httpx.Response(200, json={"results": [{
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.5555/oa",
            "title": "OA Werk",
            "authorships": [],
            "publication_year": 2020,
            "cited_by_count": 0,
            "referenced_works": [],
            "best_oa_location": {
                "pdf_url": "https://repo.example.org/oa.pdf",
                "landing_page_url": "https://repo.example.org/oa",
            },
            "open_access": {"oa_url": "https://repo.example.org/oa"},
        }]})
    )
    resp = client.post("/api/import/bibtex/oa-check", json={"items": [
        {"paper_id": 1, "doi": "10.5555/oa", "title": "OA Werk"},
        {"paper_id": 2, "doi": "10.5555/unbekannt", "title": "Paywall Werk"},
    ]})
    assert resp.status_code == 200, resp.text
    r1, r2 = resp.json()["results"]
    assert r1["oa_pdf_url"] == "https://repo.example.org/oa.pdf"
    assert r2["oa_pdf_url"] == ""
    assert r2["doi_url"] == "https://doi.org/10.5555/unbekannt"
    assert "scholar.google.com" in r2["scholar_url"]
