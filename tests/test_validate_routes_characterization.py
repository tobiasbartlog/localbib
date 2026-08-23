"""Characterization tests for the validate-domain routes (routers/validate.py).

Freezes TODAY's behaviour of:
  POST /api/papers/bulk-validate
  POST /api/papers/discover-dois
  POST /api/papers/apply-doi
  POST /api/papers/fetch-abstracts
  POST /api/papers/{id}/generate-abstract
  POST /api/papers/{id}/ocr

The propose/apply pair is already covered by test_validate_propose_apply.py
(which stays authoritative for those two routes). This file characterizes the
remaining six routes that were untested before #86.

All external IO is mocked:
  - metadata_validation.fetch_crossref_abstract — patched on the module
  - fetch_crossref_metadata                     — patched on routers.validate
  - extract_text_from_pdf                        — patched on routers.validate
  - metadata_validation.CrossRefStrategy         — patched on routers.validate
  - ocr_pdf / ocr_pdf_searchable                 — patched on routers.validate
  - metadata_validation.llm_find_abstract_in_text
  - metadata_validation.llm_generate_abstract

The LLM is never called (KICONNECT_API_KEY is "test-key" via conftest but LLM
calls are avoided by: for OCR/abstract routes, the PDF text is empty so the LLM
path is not reached; for the service unit tests, LLMClient is mocked).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import metadata_validation
import routers.validate
import webapp
from literature_manager import Config


# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

def _seed_paper(
    db,
    filename: str = "char_test.pdf",
    doi: str = "",
    abstract: str = "",
    with_file: bool = False,
) -> int:
    """Insert a minimal paper row and return its ID."""
    if with_file:
        filepath = Path(Config.ALL_DIR) / filename
        filepath.write_bytes(b"%PDF-1.4 fake content for validate char tests")
    file_hash = f"validate-char-{filename}"
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi, abstract) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_hash, filename, filename, "Test Title", "Tester, T", 2023, doi, abstract),
        )
        pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return pid


# ===========================================================================
# POST /api/papers/bulk-validate
# ===========================================================================

class TestBulkValidate:
    """Characterize /api/papers/bulk-validate."""

    def _mock_propose(self, monkeypatch, confidence="low", changes=None, category_suggestions=None):
        """Patch validate_propose on routers.validate to return a stub proposal."""
        async def _stub(paper_id, options=None):
            return {
                "paper_id": paper_id,
                "changes": changes or {},
                "current": {},
                "category_suggestions": category_suggestions or [],
                "warnings": [],
                "confidence": confidence,
                "diagnostics": {"crossref_data": None, "llm_data": {}},
            }
        monkeypatch.setattr(routers.validate, "validate_propose", _stub)

    def _mock_apply(self, monkeypatch):
        async def _stub(paper_id, data):
            return {"id": paper_id, "title": "Applied", "categories": []}
        monkeypatch.setattr(routers.validate, "validate_apply", _stub)

    def test_returns_200_on_empty_list(self, client, db, monkeypatch):
        self._mock_propose(monkeypatch)
        resp = client.post("/api/papers/bulk-validate", json={"paper_ids": []})
        assert resp.status_code == 200

    def test_response_shape(self, client, db, monkeypatch):
        self._mock_propose(monkeypatch)
        resp = client.post("/api/papers/bulk-validate", json={"paper_ids": []})
        body = resp.json()
        assert "results" in body
        assert "review_queue" in body

    def test_noop_when_no_changes(self, client, db, seed_paper, monkeypatch):
        """If proposal has no changes and no category_suggestions → status=noop."""
        self._mock_propose(monkeypatch, confidence="low", changes={}, category_suggestions=[])
        resp = client.post(
            "/api/papers/bulk-validate",
            json={"paper_ids": [seed_paper], "use_llm": False},
        )
        body = resp.json()
        assert body["results"][0]["status"] == "noop"
        assert body["review_queue"] == []

    def test_high_confidence_auto_applied(self, client, db, seed_paper, monkeypatch):
        """High-confidence proposals with changes are auto-applied."""
        self._mock_propose(
            monkeypatch,
            confidence="high",
            changes={"title": "Auto Title"},
        )
        self._mock_apply(monkeypatch)
        resp = client.post(
            "/api/papers/bulk-validate",
            json={"paper_ids": [seed_paper], "use_llm": False},
        )
        body = resp.json()
        assert body["results"][0]["status"] == "applied"
        assert body["review_queue"] == []

    def test_low_confidence_goes_to_review_queue(self, client, db, seed_paper, monkeypatch):
        """Low-confidence proposals are put into review_queue, not auto-applied."""
        self._mock_propose(
            monkeypatch,
            confidence="low",
            changes={"doi": "10.1/new"},
        )
        resp = client.post(
            "/api/papers/bulk-validate",
            json={"paper_ids": [seed_paper], "use_llm": False},
        )
        body = resp.json()
        assert body["results"][0]["status"] == "review"
        assert len(body["review_queue"]) == 1
        assert body["review_queue"][0]["id"] == seed_paper

    def test_error_per_paper_does_not_abort_batch(self, client, db, monkeypatch):
        """An exception on one paper is caught; the rest continue."""
        async def _failing_propose(paper_id, options=None):
            raise ValueError("something broke")
        monkeypatch.setattr(routers.validate, "validate_propose", _failing_propose)

        resp = client.post(
            "/api/papers/bulk-validate",
            json={"paper_ids": [9999, 9998], "use_llm": False},
        )
        body = resp.json()
        assert resp.status_code == 200
        for r in body["results"]:
            assert r["status"] == "error"
            assert "error" in r

    def test_multiple_papers_each_get_result(self, client, db, monkeypatch):
        """Result list has one entry per input paper_id."""
        self._mock_propose(monkeypatch)
        ids = [1, 2, 3]
        resp = client.post(
            "/api/papers/bulk-validate",
            json={"paper_ids": ids, "use_llm": False},
        )
        body = resp.json()
        assert len(body["results"]) == len(ids)


# ===========================================================================
# POST /api/papers/discover-dois
# ===========================================================================

class TestDiscoverDois:
    """Characterize /api/papers/discover-dois."""

    def test_returns_200(self, client, db):
        resp = client.post("/api/papers/discover-dois")
        assert resp.status_code == 200

    def test_response_shape(self, client, db):
        resp = client.post("/api/papers/discover-dois")
        body = resp.json()
        assert "total_checked" in body
        assert "candidates" in body

    def test_empty_library_zero_candidates(self, client, db):
        resp = client.post("/api/papers/discover-dois")
        body = resp.json()
        assert body["total_checked"] == 0
        assert body["candidates"] == []

    def test_paper_with_doi_is_skipped(self, client, db):
        """Papers that already have a DOI are NOT checked."""
        _seed_paper(db, filename="hasdoi.pdf", doi="10.1/existing")
        resp = client.post("/api/papers/discover-dois")
        body = resp.json()
        assert body["total_checked"] == 0

    def test_paper_without_pdf_is_skipped(self, client, db):
        """Paper row exists but the file on disk does not → skipped gracefully."""
        _seed_paper(db, filename="missing_file.pdf", doi="")  # no with_file
        resp = client.post("/api/papers/discover-dois")
        body = resp.json()
        # total_checked may be 1 (attempted) or 0 (file check skips it) depending
        # on implementation. What we freeze: no crash, candidates is empty.
        assert body["candidates"] == []
        assert resp.status_code == 200

    def test_candidate_shape_when_doi_found(self, client, db, monkeypatch):
        """When CrossRefStrategy finds a DOI, candidate has the right shape."""
        _seed_paper(db, filename="nodoi.pdf", doi="", with_file=True)

        # Patch extract_text_from_pdf to return substantial text so the strategy runs
        monkeypatch.setattr(routers.validate, "extract_text_from_pdf",
                            lambda *a, **kw: "A" * 200)

        # Patch CrossRefStrategy to find a DOI
        class _FakeStrategy:
            last_doi = "10.99/found"
            last_crossref_data = {"title": "Found Title", "authors": "Auth, B", "year": 2021}

            def run(self, text, paper):
                pass

        with patch.object(routers.validate, "CrossRefStrategy", return_value=_FakeStrategy()):
            resp = client.post("/api/papers/discover-dois")

        body = resp.json()
        assert resp.status_code == 200
        assert len(body["candidates"]) == 1
        c = body["candidates"][0]
        assert c["doi"] == "10.99/found"
        assert "local" in c
        assert "crossref" in c

    def test_no_text_paper_produces_no_candidate(self, client, db, monkeypatch):
        """If extract_text_from_pdf returns empty, paper is skipped."""
        _seed_paper(db, filename="empty_text.pdf", doi="", with_file=True)
        monkeypatch.setattr(routers.validate, "extract_text_from_pdf",
                            lambda *a, **kw: "")
        resp = client.post("/api/papers/discover-dois")
        body = resp.json()
        assert body["candidates"] == []


# ===========================================================================
# POST /api/papers/apply-doi
# ===========================================================================

class TestApplyDoi:
    """Characterize /api/papers/apply-doi."""

    def test_404_when_paper_missing(self, client, db):
        resp = client.post(
            "/api/papers/apply-doi",
            json={"paper_id": 999999, "doi": "10.1/x"},
        )
        assert resp.status_code == 404

    def test_stores_doi_on_paper(self, client, db):
        pid = _seed_paper(db, filename="appdoi.pdf")
        with patch.object(routers.validate, "fetch_crossref_metadata", return_value=None):
            resp = client.post(
                "/api/papers/apply-doi",
                json={"paper_id": pid, "doi": "10.99/new"},
            )
        assert resp.status_code == 200
        # Verify DB write
        conn = db._connect()
        try:
            row = conn.execute("SELECT doi FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        assert row["doi"] == "10.99/new"

    def test_response_shape(self, client, db):
        pid = _seed_paper(db, filename="appdoi2.pdf")
        with patch.object(routers.validate, "fetch_crossref_metadata", return_value=None):
            resp = client.post(
                "/api/papers/apply-doi",
                json={"paper_id": pid, "doi": "10.99/shape"},
            )
        body = resp.json()
        assert body["status"] == "ok"
        assert body["paper_id"] == pid
        assert body["doi"] == "10.99/shape"

    def test_crossref_metadata_fills_empty_fields(self, client, db):
        """If CrossRef returns data for an empty field, it is persisted."""
        pid = _seed_paper(db, filename="appdoi3.pdf")
        crossref_data = {
            "title": "CrossRef Title", "authors": "CR Auth", "year": 2022,
            "abstract": "CR Abstract", "journal": "CR Journal",
        }
        with patch.object(routers.validate, "fetch_crossref_metadata", return_value=crossref_data):
            resp = client.post(
                "/api/papers/apply-doi",
                json={"paper_id": pid, "doi": "10.1/cr"},
            )
        assert resp.status_code == 200
        conn = db._connect()
        try:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        # title was already "Test Title" from _seed_paper → NOT overwritten (field already set)
        # abstract was empty → should be filled
        assert row["abstract"] == "CR Abstract"

    def test_crossref_does_not_overwrite_existing_fields(self, client, db):
        """CrossRef data must NOT overwrite already-filled paper fields."""
        conn = db._connect()
        try:
            cur = conn.execute(
                "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi, abstract) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("cr-keep", "cr_keep.pdf", "cr_keep.pdf",
                 "Original Title", "Original Auth", 2020, "", "Existing Abstract"),
            )
            pid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        crossref_data = {"title": "CR Title", "abstract": "CR Abstract"}
        with patch.object(routers.validate, "fetch_crossref_metadata", return_value=crossref_data):
            client.post("/api/papers/apply-doi", json={"paper_id": pid, "doi": "10.1/cr2"})

        conn = db._connect()
        try:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        assert row["title"] == "Original Title"       # NOT overwritten
        assert row["abstract"] == "Existing Abstract"  # NOT overwritten


# ===========================================================================
# POST /api/papers/fetch-abstracts
# ===========================================================================

class TestFetchAbstracts:
    """Characterize /api/papers/fetch-abstracts."""

    def test_returns_200(self, client, db):
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.status_code == 200

    def test_response_shape(self, client, db):
        resp = client.post("/api/papers/fetch-abstracts")
        body = resp.json()
        assert "total" in body
        assert "fetched" in body

    def test_empty_library_zero_total(self, client, db):
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.json()["total"] == 0

    def test_paper_with_abstract_is_skipped(self, client, db):
        _seed_paper(db, doi="10.1/x", abstract="Already has abstract")
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.json()["total"] == 0  # skipped; not a candidate

    def test_paper_without_doi_is_skipped(self, client, db):
        _seed_paper(db, doi="", abstract="")
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.json()["total"] == 0

    def test_fetches_abstract_when_crossref_returns_one(self, client, db, monkeypatch):
        pid = _seed_paper(db, doi="10.1/abstract", abstract="")
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "A great abstract.",
        )
        resp = client.post("/api/papers/fetch-abstracts")
        body = resp.json()
        assert resp.status_code == 200
        assert body["total"] == 1
        assert body["fetched"] == 1
        # Verify DB write
        conn = db._connect()
        try:
            row = conn.execute("SELECT abstract FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        assert row["abstract"] == "A great abstract."

    def test_no_fetch_when_crossref_returns_empty(self, client, db, monkeypatch):
        _seed_paper(db, doi="10.1/noabs", abstract="")
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "",
        )
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.json()["fetched"] == 0

    def test_paper_ids_filter(self, client, db, monkeypatch):
        """Passing paper_ids in body restricts which papers are checked."""
        pid_a = _seed_paper(db, filename="fa_a.pdf", doi="10.1/a", abstract="")
        pid_b = _seed_paper(db, filename="fa_b.pdf", doi="10.1/b", abstract="")
        fetched_dois = []
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: (fetched_dois.append(doi) or "abs"),
        )
        resp = client.post(
            "/api/papers/fetch-abstracts",
            json={"paper_ids": [pid_a]},
        )
        assert resp.status_code == 200
        assert "10.1/a" in fetched_dois
        assert "10.1/b" not in fetched_dois

    def test_200_with_no_body(self, client, db):
        """No body → no paper_ids filter → all candidates processed."""
        resp = client.post("/api/papers/fetch-abstracts")
        assert resp.status_code == 200


# ===========================================================================
# POST /api/papers/{id}/generate-abstract
# ===========================================================================

class TestGenerateAbstract:
    """Characterize /api/papers/{id}/generate-abstract."""

    def test_404_when_paper_missing(self, client, db):
        resp = client.post("/api/papers/999999/generate-abstract")
        assert resp.status_code == 404

    def test_crossref_abstract_used_when_doi_present(self, client, db, monkeypatch):
        pid = _seed_paper(db, doi="10.1/cr", abstract="")
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "CrossRef Abstract.",
        )
        resp = client.post(f"/api/papers/{pid}/generate-abstract")
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "ok"
        assert body["source"] == "crossref"
        assert "CrossRef Abstract" in body["abstract"]

    def test_persists_abstract_to_db(self, client, db, monkeypatch):
        pid = _seed_paper(db, doi="10.1/cr2", abstract="")
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "Stored abstract.",
        )
        resp = client.post(f"/api/papers/{pid}/generate-abstract")
        assert resp.status_code == 200

        conn = db._connect()
        try:
            row = conn.execute("SELECT abstract, abstract_source FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        assert row["abstract"] == "Stored abstract."
        assert row["abstract_source"] == "crossref"

    def test_error_response_when_no_abstract_found(self, client, db, monkeypatch):
        """No DOI, no PDF text, no LLM key → error status."""
        pid = _seed_paper(db, doi="", abstract="")
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "",
        )
        # Also suppress the LLM branch by forcing KICONNECT_API_KEY to empty
        import os
        old = os.environ.get("KICONNECT_API_KEY", "")
        os.environ["KICONNECT_API_KEY"] = ""
        try:
            # Re-evaluate Config (Config caches the key at init time)
            from literature_manager import Config as _Cfg
            old_key = _Cfg.KICONNECT_API_KEY
            _Cfg.KICONNECT_API_KEY = ""
            resp = client.post(f"/api/papers/{pid}/generate-abstract")
            _Cfg.KICONNECT_API_KEY = old_key
        finally:
            os.environ["KICONNECT_API_KEY"] = old

        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "error"

    def test_llm_path_finds_abstract_in_pdf(self, client, db, monkeypatch):
        """When CrossRef misses but LLM finds it in PDF text, source is 'pdf'."""
        pid = _seed_paper(db, filename="genabs.pdf", doi="", abstract="", with_file=True)
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "",
        )
        monkeypatch.setattr(
            routers.validate, "extract_text_from_pdf",
            lambda *a, **kw: "A" * 200,
        )
        monkeypatch.setattr(
            metadata_validation, "llm_find_abstract_in_text",
            lambda llm, title, authors, text: "Found abstract.",
        )
        resp = client.post(f"/api/papers/{pid}/generate-abstract")
        body = resp.json()
        assert resp.status_code == 200
        assert body["source"] == "pdf"
        assert "Found abstract." in body["abstract"]

    def test_llm_path_generates_abstract_when_not_found(self, client, db, monkeypatch):
        """When both CrossRef and llm_find_abstract fail, llm_generate is used."""
        pid = _seed_paper(db, filename="genabs2.pdf", doi="", abstract="", with_file=True)
        monkeypatch.setattr(
            metadata_validation, "fetch_crossref_abstract",
            lambda doi, fetch: "",
        )
        monkeypatch.setattr(
            routers.validate, "extract_text_from_pdf",
            lambda *a, **kw: "B" * 200,
        )
        monkeypatch.setattr(
            metadata_validation, "llm_find_abstract_in_text",
            lambda llm, title, authors, text: "",
        )
        monkeypatch.setattr(
            metadata_validation, "llm_generate_abstract",
            lambda llm, title, authors, text: "Generated abstract.",
        )
        resp = client.post(f"/api/papers/{pid}/generate-abstract")
        body = resp.json()
        assert resp.status_code == 200
        assert body["source"] == "generated"
        assert "Generated abstract." in body["abstract"]


# ===========================================================================
# POST /api/papers/{id}/ocr
# ===========================================================================

class TestOcrPaper:
    """Characterize /api/papers/{id}/ocr."""

    def test_404_when_paper_missing(self, client, db):
        resp = client.post("/api/papers/999999/ocr")
        assert resp.status_code == 404

    def test_404_when_pdf_file_missing(self, client, db):
        pid = _seed_paper(db, filename="missing_ocr.pdf")  # no with_file
        resp = client.post(f"/api/papers/{pid}/ocr")
        assert resp.status_code == 404

    def test_no_text_response_when_ocr_returns_empty(self, client, db, monkeypatch):
        pid = _seed_paper(db, filename="empty_ocr.pdf", with_file=True)
        monkeypatch.setattr(routers.validate, "ocr_pdf", lambda *a, **kw: "")
        resp = client.post(f"/api/papers/{pid}/ocr")
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "no_text"
        assert body["chars"] == 0

    def test_ocr_text_stored_in_db(self, client, db, monkeypatch):
        pid = _seed_paper(db, filename="stored_ocr.pdf", with_file=True)
        monkeypatch.setattr(routers.validate, "ocr_pdf", lambda *a, **kw: "Extracted OCR text.")
        monkeypatch.setattr(routers.validate, "ocr_pdf_searchable", lambda *a, **kw: True)
        # Suppress LLM branch
        from literature_manager import Config as _Cfg
        old_key = _Cfg.KICONNECT_API_KEY
        _Cfg.KICONNECT_API_KEY = ""
        try:
            resp = client.post(f"/api/papers/{pid}/ocr")
        finally:
            _Cfg.KICONNECT_API_KEY = old_key
        assert resp.status_code == 200

        conn = db._connect()
        try:
            row = conn.execute("SELECT ocr_text FROM papers WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        assert "Extracted OCR text." in row["ocr_text"]

    def test_response_contains_ocr_meta_fields(self, client, db, monkeypatch):
        pid = _seed_paper(db, filename="meta_ocr.pdf", with_file=True)
        monkeypatch.setattr(routers.validate, "ocr_pdf", lambda *a, **kw: "Some text here.")
        monkeypatch.setattr(routers.validate, "ocr_pdf_searchable", lambda *a, **kw: False)
        from literature_manager import Config as _Cfg
        old_key = _Cfg.KICONNECT_API_KEY
        _Cfg.KICONNECT_API_KEY = ""
        try:
            resp = client.post(f"/api/papers/{pid}/ocr")
        finally:
            _Cfg.KICONNECT_API_KEY = old_key
        body = resp.json()
        assert "_ocr_chars" in body
        assert "_ocr_llm" in body
        assert "_ocr_searchable" in body
        assert body["_ocr_chars"] == len("Some text here.")
        assert body["_ocr_searchable"] is False

    def test_pages_query_param_forwarded_to_ocr_pdf(self, client, db, monkeypatch):
        """The ``pages`` query parameter controls how many pages ocr_pdf processes."""
        pid = _seed_paper(db, filename="pages_ocr.pdf", with_file=True)
        captured = {}
        def _ocr(filepath, max_pages):
            captured["max_pages"] = max_pages
            return "text " * 10
        monkeypatch.setattr(routers.validate, "ocr_pdf", _ocr)
        monkeypatch.setattr(routers.validate, "ocr_pdf_searchable", lambda *a, **kw: True)
        from literature_manager import Config as _Cfg
        old_key = _Cfg.KICONNECT_API_KEY
        _Cfg.KICONNECT_API_KEY = ""
        try:
            client.post(f"/api/papers/{pid}/ocr?pages=3")
        finally:
            _Cfg.KICONNECT_API_KEY = old_key
        assert captured.get("max_pages") == 3

    def test_default_pages_is_10(self, client, db, monkeypatch):
        pid = _seed_paper(db, filename="defpages_ocr.pdf", with_file=True)
        captured = {}
        def _ocr(filepath, max_pages):
            captured["max_pages"] = max_pages
            return "text " * 10
        monkeypatch.setattr(routers.validate, "ocr_pdf", _ocr)
        monkeypatch.setattr(routers.validate, "ocr_pdf_searchable", lambda *a, **kw: True)
        from literature_manager import Config as _Cfg
        old_key = _Cfg.KICONNECT_API_KEY
        _Cfg.KICONNECT_API_KEY = ""
        try:
            client.post(f"/api/papers/{pid}/ocr")
        finally:
            _Cfg.KICONNECT_API_KEY = old_key
        assert captured.get("max_pages") == 10
