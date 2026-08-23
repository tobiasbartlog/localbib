"""Characterization tests for the smart-import pipeline endpoints.

Freezes TODAY's behaviour of:
  POST /api/import/upload                          (synchronous)
  POST /api/import/upload-smart                   (SSE stream)
  POST /api/import/process-smart/{filename}       (SSE stream from INPUT_DIR)

External HTTP (CrossRef, LLM) is mocked via unittest.mock.patch.
No network calls are made — tests are deterministic and offline.

Purpose: catch regressions when smart_import_pipeline is extracted
(issue #87).  Assert on observable behaviour (response shapes + DB
side-effects), NOT on internal function names.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import webapp
import services.smart_import_pipeline as smart_import_pipeline
from literature_manager import Config

# Issue #87 relocated the import handlers + the smart pipeline. Patch targets are
# repointed to where the calls now resolve (mock-location fix, not behaviour
# change): the pure metadata pipeline (text/doi/crossref) lives in
# ``services.smart_import_pipeline``; persistence (process_paper, chunking,
# symlinks, rematch) lives in the import router (dotted path "routers.import",
# loaded via importlib because "import" is a keyword).
import_router = importlib.import_module("routers.import")

# ---------------------------------------------------------------------------
# Fake PDF bytes — not fitz-parseable, but the fitz call is wrapped in
# try/except so _page_count silently becomes 0.  This is the today-quirk we
# freeze: page_count=0 for uploaded test PDFs.
# ---------------------------------------------------------------------------
FAKE_PDF = b"%PDF-1.4 fake content for smart-import characterization test"


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _parse_sse(response) -> list[dict]:
    """Extract data payloads from an SSE response body."""
    events: list[dict] = []
    for line in response.text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _event_by_type(events: list[dict], t: str) -> dict | None:
    for e in events:
        if e.get("type") == t:
            return e
    return None


# ---------------------------------------------------------------------------
# Shared patch helpers for the smart pipeline
# ---------------------------------------------------------------------------

def _patch_text(text: str = "Smart Import Paper Title\n\nSome body text."):
    return patch.object(smart_import_pipeline, "extract_text_from_pdf", return_value=text)


def _patch_no_doi():
    return patch.object(smart_import_pipeline, "discover_doi", return_value=None)


def _patch_no_chunks():
    """Neutralises the post-import index hook (#153): no chunks, no vectors."""
    return patch.object(
        import_router,
        "index_paper_after_import",
        return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0},
    )


def _patch_no_symlinks():
    return patch.object(import_router, "create_symlinks", return_value=None)


# Disable all optional automation flags for minimal happy-path tests
_SKIP_ALL = "?do_doi=false&do_categories=false&do_abstract=false&do_validate=false"


# ===========================================================================
# POST /api/import/upload  —  synchronous classic import
# ===========================================================================

class TestUploadClassic:
    """Characterization tests for POST /api/import/upload."""

    def test_rejects_non_pdf_filename(self, client, db):
        resp = client.post(
            "/api/import/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_rejects_empty_filename(self, client, db):
        # Quirk: an upload with empty filename bypasses the endpoint's own 400
        # guard and instead gets a 422 from FastAPI's multipart validation layer.
        # endpoint checks: not file.filename or not file.filename.lower().endswith(".pdf")
        resp = client.post(
            "/api/import/upload",
            files={"file": ("", FAKE_PDF, "application/pdf")},
        )
        assert resp.status_code in (400, 422)  # today: 422 from FastAPI

    def test_happy_path_returns_ok_and_paper_id(self, client, db):
        """When process_paper() succeeds the endpoint returns status=ok + paper_id."""
        with patch.object(import_router, "process_paper", return_value=42), \
             patch.object(import_router, "_rematch_references_for_paper", return_value=None):
            resp = client.post(
                "/api/import/upload",
                files={"file": ("paper.pdf", FAKE_PDF, "application/pdf")},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["paper_id"] == 42
        assert body["filename"].endswith(".pdf")

    def test_returns_skipped_when_process_paper_returns_none(self, client, db):
        """process_paper() returning None (duplicate or error) yields status=skipped."""
        with patch.object(import_router, "process_paper", return_value=None):
            resp = client.post(
                "/api/import/upload",
                files={"file": ("dup.pdf", FAKE_PDF, "application/pdf")},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "skipped"
        assert "reason" in body
        assert "filename" in body

    def test_renames_file_on_name_collision_in_input_dir(self, client, db):
        """When a file with the same name already sits in INPUT_DIR the endpoint
        appends a counter suffix (_1, _2 …) — characterize this naming quirk."""
        # Pre-create a file to force the collision
        collision = Path(Config.INPUT_DIR) / "unique_char.pdf"
        collision.write_bytes(b"pre-existing sentinel")

        with patch.object(import_router, "process_paper", return_value=None):
            resp = client.post(
                "/api/import/upload",
                files={"file": ("unique_char.pdf", FAKE_PDF, "application/pdf")},
            )
        assert resp.status_code == 200
        assert resp.json()["filename"] != "unique_char.pdf"
        assert resp.json()["filename"].endswith(".pdf")


# ===========================================================================
# POST /api/import/upload-smart  —  SSE pipeline
# ===========================================================================

class TestUploadSmart:
    """Characterization tests for POST /api/import/upload-smart."""

    def test_rejects_non_pdf(self, client, db):
        resp = client.post(
            "/api/import/upload-smart",
            files={"file": ("essay.docx", b"docx content", "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_response_content_type_is_sse(self, client, db):
        """The endpoint MUST return text/event-stream."""
        with _patch_text(), _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("stream_ct.pdf", FAKE_PDF, "application/pdf")},
            )
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_happy_path_emits_complete_event(self, client, db):
        """Minimal happy path (no DOI, no extras) MUST emit a 'complete' event."""
        with _patch_text("My Unique Paper Title\nBody text."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("happy.pdf", FAKE_PDF, "application/pdf")},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp)
        complete = _event_by_type(events, "complete")
        assert complete is not None, f"No 'complete' event; events={events}"
        assert isinstance(complete["paper_id"], int)
        assert "filename" in complete
        assert "title" in complete

    def test_complete_event_has_full_payload_shape(self, client, db):
        """Freeze the exact keys present in the 'complete' event payload."""
        with _patch_text("Full Shape Paper Title\nContent."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("shape.pdf", FAKE_PDF, "application/pdf")},
            )
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        expected_keys = {
            "paper_id", "filename", "title", "authors", "year",
            "doi", "isbn", "abstract", "abstract_source",
            "journal", "publisher", "categories",
        }
        assert expected_keys.issubset(complete.keys()), (
            f"Missing keys: {expected_keys - complete.keys()}"
        )

    def test_happy_path_creates_db_row(self, client, db):
        """After a successful upload the paper row must exist in the database."""
        with _patch_text("DB Row Paper Title\nContent text here."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("dbrow.pdf", FAKE_PDF, "application/pdf")},
            )
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        paper_id = complete["paper_id"]

        conn = db._connect()
        row = conn.execute(
            "SELECT file_hash, original_filename, title FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["file_hash"] != ""
        assert row["original_filename"] == "dbrow.pdf"

    def test_pipeline_emits_progress_events_before_complete(self, client, db):
        """At least one 'progress' event must precede the 'complete' event."""
        with _patch_text("Progress Test Title\nContent."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("progress.pdf", FAKE_PDF, "application/pdf")},
            )
        events = _parse_sse(resp)
        progress = [e for e in events if e.get("type") == "progress"]
        assert len(progress) >= 1

    def test_hash_dedup_emits_sse_error_not_http_error(self, client, db):
        """When the same file hash already exists in the DB the SSE stream
        emits an error event (HTTP status stays 200) — NOT an HTTP 4xx/5xx.
        This is today's quirk: duplicate signal lives inside the SSE body."""
        # First upload succeeds
        with _patch_text("Hash Dedup First Upload Title"), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            r1 = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("dup_first.pdf", FAKE_PDF, "application/pdf")},
            )
        assert _event_by_type(_parse_sse(r1), "complete") is not None

        # Second upload — identical bytes → same hash → duplicate
        with _patch_text("Hash Dedup Second Upload Different Text"), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            r2 = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("dup_second.pdf", FAKE_PDF, "application/pdf")},
            )
        assert r2.status_code == 200  # HTTP stays 200 — quirk frozen here
        events2 = _parse_sse(r2)
        err = _event_by_type(events2, "error")
        assert err is not None, f"Expected SSE error event; events={events2}"
        assert "Duplikat" in err["message"]
        assert "duplicate_paper_id" in err

    def test_content_title_dedup_emits_sse_error(self, client, db):
        """A paper with the same normalised title already in the DB triggers the
        content-duplicate check and emits an SSE error event (HTTP 200)."""
        existing_title = "Der Klimawandel und seine Folgen fuer die Arktis"
        conn = db._connect()
        conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors) "
            "VALUES (?, ?, ?, ?, ?)",
            ("existinghash_abc", "existing.pdf", "existing.pdf", existing_title, "Schmidt"),
        )
        conn.commit()
        conn.close()

        # Upload different bytes whose extracted text starts with the same title
        with _patch_text(existing_title + "\nSome additional body content."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("klimawandel.pdf", b"%PDF-1.4 other bytes xyz", "application/pdf")},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp)
        err = _event_by_type(events, "error")
        assert err is not None, f"Expected SSE error for title dup; events={events}"
        assert "vorhanden" in err["message"]
        assert "duplicate_paper_id" in err

    def test_crossref_plausible_doi_stored_in_db(self, client, db):
        """When discover_doi finds a DOI and CrossRef returns a title that matches
        the PDF text, CrossRef metadata is stored in the paper row."""
        doi = "10.1000/plausible"
        cr_title = "Plausible CrossRef Title About Machine Learning"
        pdf_text = f"{cr_title}\nMore content about machine learning studies."
        crossref_data = {
            "title": cr_title, "authors": "Doe, Jane", "year": 2023,
            "doi": doi, "abstract": "A great abstract.", "journal": "AI Journal",
            "publisher": "Springer",
        }
        with _patch_text(pdf_text), \
             patch.object(smart_import_pipeline, "discover_doi", return_value=doi), \
             patch.object(smart_import_pipeline, "fetch_crossref_metadata", return_value=crossref_data), \
             _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                "/api/import/upload-smart?do_doi=true&do_categories=false&do_abstract=false&do_validate=false",
                files={"file": ("cr_match.pdf", FAKE_PDF, "application/pdf")},
            )
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["doi"] == doi
        assert complete["title"] == cr_title

        conn = db._connect()
        row = conn.execute(
            "SELECT doi, title, authors, year FROM papers WHERE id = ?",
            (complete["paper_id"],),
        ).fetchone()
        conn.close()
        assert row["doi"] == doi
        assert row["title"] == cr_title
        assert row["authors"] == "Doe, Jane"
        assert row["year"] == 2023

    def test_crossref_plausibility_discards_mismatched_doi(self, client, db):
        """When CrossRef title words are NOT present in the PDF text the DOI is
        discarded and the complete event carries doi='' — characterise this quirk."""
        doi = "10.9999/mismatch"
        # CrossRef words absent from PDF text → overlap < 0.5 → plausibility fails
        crossref_data = {
            "title": "Komplett anderer Titel Bananen Gurken Zucchini",
            "authors": "Wrong, W.", "year": 2020,
            "doi": doi, "abstract": "", "journal": "", "publisher": "",
        }
        pdf_text = "Quantum Mechanics and Superposition States\nBy Smith et al."

        with _patch_text(pdf_text), \
             patch.object(smart_import_pipeline, "discover_doi", return_value=doi), \
             patch.object(smart_import_pipeline, "fetch_crossref_metadata", return_value=crossref_data), \
             _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                "/api/import/upload-smart?do_doi=true&do_categories=false&do_abstract=false&do_validate=false",
                files={"file": ("mismatch.pdf", FAKE_PDF, "application/pdf")},
            )
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        # DOI must be discarded when CrossRef title does not match PDF content
        assert complete["doi"] == "", (
            "Quirk: mismatched CrossRef title should cause DOI to be discarded"
        )


# ===========================================================================
# POST /api/import/process-smart/{filename}  —  SSE from INPUT_DIR
# ===========================================================================

class TestProcessSmartFromInput:
    """Characterization tests for POST /api/import/process-smart/{filename}."""

    def test_404_when_file_not_in_input_dir(self, client, db):
        resp = client.post("/api/import/process-smart/ghost_file.pdf")
        assert resp.status_code == 404

    def test_404_detail_mentions_filename(self, client, db):
        resp = client.post("/api/import/process-smart/missing_paper.pdf")
        assert resp.status_code == 404
        assert "missing_paper.pdf" in resp.json()["detail"]

    def test_happy_path_processes_file_from_input_dir(self, client, db):
        """A PDF placed in INPUT_DIR is processed and emits a 'complete' event."""
        pdf_path = Path(Config.INPUT_DIR) / "input_test_char.pdf"
        pdf_path.write_bytes(FAKE_PDF)

        with _patch_text("Input Dir Characterization Paper\nContent here."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/process-smart/input_test_char.pdf{_SKIP_ALL}",
            )
        assert resp.status_code == 200
        events = _parse_sse(resp)
        complete = _event_by_type(events, "complete")
        assert complete is not None, f"No complete event; events={events}"
        assert isinstance(complete["paper_id"], int)

    def test_process_smart_creates_db_row(self, client, db):
        """After process-smart completes the paper row exists in the database."""
        pdf_path = Path(Config.INPUT_DIR) / "psmart_dbrow.pdf"
        pdf_path.write_bytes(FAKE_PDF + b" extra-unique-bytes-psmart")

        with _patch_text("Process Smart DB Row Title\nContent."), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            resp = client.post(
                f"/api/import/process-smart/psmart_dbrow.pdf{_SKIP_ALL}",
            )
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        conn = db._connect()
        row = conn.execute(
            "SELECT id, original_filename FROM papers WHERE id = ?",
            (complete["paper_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["original_filename"] == "psmart_dbrow.pdf"

    def test_hash_dedup_emits_sse_error_event(self, client, db):
        """If the same hash was already imported, process-smart emits SSE error."""
        dedup_bytes = FAKE_PDF + b" dedup-process-smart-sentinel"

        # First: import via upload-smart to populate the DB
        with _patch_text("Process Smart Dedup Title"), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            r1 = client.post(
                f"/api/import/upload-smart{_SKIP_ALL}",
                files={"file": ("ps_src.pdf", dedup_bytes, "application/pdf")},
            )
        assert _event_by_type(_parse_sse(r1), "complete") is not None

        # Second: place same bytes in INPUT_DIR and run process-smart
        dup_path = Path(Config.INPUT_DIR) / "ps_dup.pdf"
        dup_path.write_bytes(dedup_bytes)

        with _patch_text("Process Smart Dedup Title"), \
             _patch_no_doi(), _patch_no_chunks(), _patch_no_symlinks():
            r2 = client.post(
                f"/api/import/process-smart/ps_dup.pdf{_SKIP_ALL}",
            )
        assert r2.status_code == 200
        events2 = _parse_sse(r2)
        err = _event_by_type(events2, "error")
        assert err is not None, f"Expected SSE error event; events={events2}"
        assert "Duplikat" in err["message"]
        assert "duplicate_paper_id" in err

    def test_path_traversal_neutralized_to_404(self, client, db):
        """Filenames containing path-traversal components are sanitized via
        os.path.basename — the traversed path won't exist in INPUT_DIR → 404."""
        resp = client.post("/api/import/process-smart/../../../etc/passwd")
        assert resp.status_code == 404
