"""Characterization tests for reference extraction endpoints.

Freezes TODAY's behaviour of:
  POST /api/papers/{id}/extract-references      (SSE stream)
  GET  /api/papers/{id}/references              (CRUD list)
  PUT  /api/papers/{id}/references/{ref_id}     (update + rematch)
  DELETE /api/papers/{id}/references/{ref_id}   (delete)
  POST /api/papers/{id}/references/add          (manual add)
  POST /api/papers/bulk-extract-references      (SSE stream)

LLM and CrossRef HTTP are mocked via unittest.mock.patch.object on the
module-level helpers in webapp.py — no network calls, fully deterministic.

Purpose: catch regressions when reference_extraction is extracted as a
service (issue #88). Assert on observable behaviour (response shapes +
DB side-effects), NOT on internal function names.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import services.reference_extraction as _ref_svc
import webapp
from literature_manager import Config

# ---------------------------------------------------------------------------
# SSE helpers (same style as test_import_smart_characterization.py)
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
# Shared patch helpers
# ---------------------------------------------------------------------------

FAKE_PAGES = ["Page 1 content", "References\nSmith 2020\nDoe 2021"]
FAKE_REF_TEXT = "Smith, J. (2020). A Great Paper. Journal of Science.\nDoe, A. (2021). Another Work. Nature."

FAKE_REFS = [
    {"title": "A Great Paper", "authors": "Smith, John", "year": 2020, "journal": "Journal of Science", "doi": ""},
    {"title": "Another Work", "authors": "Doe, Alice", "year": 2021, "journal": "Nature", "doi": "10.1000/nature2021"},
]

_NO_MATCH = {"matched_paper_id": None, "match_confidence": 0.0}


def _patch_pages(pages=None):
    return patch.object(_ref_svc, "extract_all_pdf_pages", return_value=pages if pages is not None else FAKE_PAGES)


def _patch_ref_section(text=None):
    return patch.object(_ref_svc, "find_reference_section", return_value=text if text is not None else FAKE_REF_TEXT)


def _patch_llm_refs(refs=None):
    # Both the single-paper path (extract_references_from_chunk per chunk) and
    # the bulk path (llm_extract_references wrapper, which now delegates to the
    # same primitive) funnel through extract_references_from_chunk. FAKE_REF_TEXT
    # is short → one chunk → the full ref list is returned once.
    return patch.object(_ref_svc, "extract_references_from_chunk", return_value=refs if refs is not None else FAKE_REFS)


def _patch_crossref_no_doi():
    return patch.object(_ref_svc, "crossref_doi_lookup", return_value=None)


def _patch_no_match():
    return patch.object(_ref_svc, "match_ref_to_library", return_value=_NO_MATCH)


def _seed_paper(db, filename="ref_test.pdf", with_file=True) -> int:
    """Insert a paper row and optionally create the dummy PDF. Returns paper_id.

    The file_hash is derived from the filename so that multiple calls within
    the same test (with distinct filenames) don't hit the UNIQUE constraint.
    """
    if with_file:
        filepath = Path(Config.ALL_DIR) / filename
        filepath.write_bytes(b"%PDF-1.4 fake content for reference tests")
    file_hash = f"hashref-{filename}"
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_hash, filename, filename, "Reference Test Paper", "Tester, T.", 2023),
        )
        pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_ref(db, source_paper_id: int, title: str, ref_index: int = 1,
                doi: str = "", matched_paper_id=None) -> int:
    """Insert a paper_references row and return its id."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_references "
            "(source_paper_id, ref_index, title, authors, year, journal, doi, "
            " matched_paper_id, match_confidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_paper_id, ref_index, title, "Auth, A.", 2020, "J.", doi,
             matched_paper_id, 0.9 if matched_paper_id else 0.0, "pdf_llm"),
        )
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return rid


# ===========================================================================
# POST /api/papers/{paper_id}/extract-references
# ===========================================================================

class TestExtractReferences:
    """Characterization tests for POST /api/papers/{id}/extract-references."""

    def test_404_paper_not_found(self, client, db):
        resp = client.post("/api/papers/99999/extract-references")
        assert resp.status_code == 404
        assert "nicht gefunden" in resp.json()["detail"]

    def test_400_no_pdf_filename(self, client, db):
        """A paper row without a filename yields 400."""
        conn = db._connect()
        try:
            cur = conn.execute(
                "INSERT INTO papers (file_hash, filename, original_filename, title) "
                "VALUES (?, ?, ?, ?)",
                ("hashnopdf", "", "orig.pdf", "No PDF Paper"),
            )
            pid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        resp = client.post(f"/api/papers/{pid}/extract-references")
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_404_pdf_file_missing_on_disk(self, client, db):
        """Paper has a filename, but the file is absent from ALL_DIR → 404."""
        pid = _seed_paper(db, filename="ghost_ref.pdf", with_file=False)
        resp = client.post(f"/api/papers/{pid}/extract-references")
        assert resp.status_code == 404

    def test_response_is_sse_stream(self, client, db):
        """The endpoint must return text/event-stream."""
        pid = _seed_paper(db, filename="sse_ct_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_happy_path_emits_complete_event(self, client, db):
        """Minimal happy path emits a 'complete' event with status='ok'."""
        pid = _seed_paper(db, filename="happy_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        assert resp.status_code == 200
        events = _parse_sse(resp)
        complete = _event_by_type(events, "complete")
        assert complete is not None, f"No 'complete' event; events={events}"
        assert complete["status"] == "ok"

    def test_complete_event_shape(self, client, db):
        """Freeze the key set of the 'complete' event payload."""
        pid = _seed_paper(db, filename="shape_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        expected = {"type", "status", "total_extracted", "in_library", "with_doi", "references"}
        assert expected.issubset(complete.keys()), (
            f"Missing keys: {expected - complete.keys()}"
        )

    def test_refs_saved_to_db(self, client, db):
        """After extraction the paper_references rows exist in the DB."""
        pid = _seed_paper(db, filename="dbsave_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["total_extracted"] == len(FAKE_REFS)

        conn = db._connect()
        rows = conn.execute(
            "SELECT title FROM paper_references WHERE source_paper_id = ? ORDER BY ref_index",
            (pid,),
        ).fetchall()
        conn.close()
        assert len(rows) == len(FAKE_REFS)
        assert rows[0]["title"] == FAKE_REFS[0]["title"]
        assert rows[1]["title"] == FAKE_REFS[1]["title"]

    def test_ref_doi_from_llm_stored(self, client, db):
        """A DOI returned by the LLM is stored in the paper_references row."""
        pid = _seed_paper(db, filename="doi_ref.pdf")
        refs_with_doi = [{"title": "DOI Paper", "authors": "Auth", "year": 2020,
                          "journal": "J", "doi": "10.9999/stored"}]
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(refs_with_doi), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["with_doi"] == 1

        conn = db._connect()
        row = conn.execute(
            "SELECT doi FROM paper_references WHERE source_paper_id = ?", (pid,)
        ).fetchone()
        conn.close()
        assert row["doi"] == "10.9999/stored"

    def test_library_match_stored_when_found(self, client, db):
        """When _match_ref_to_library returns a matched_paper_id it is stored."""
        pid = _seed_paper(db, filename="match_ref.pdf")
        # Use a second distinct paper as the match target (different filename → unique hash)
        pid2 = _seed_paper(db, filename="matchable_target.pdf", with_file=False)

        refs = [{"title": "Matchable Paper", "authors": "", "year": 2022, "journal": "", "doi": ""}]
        match_result = {"matched_paper_id": pid2, "match_confidence": 0.95}
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(refs), \
             _patch_crossref_no_doi(), \
             patch.object(_ref_svc, "match_ref_to_library", return_value=match_result):
            resp = client.post(f"/api/papers/{pid}/extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["in_library"] == 1

        conn = db._connect()
        row = conn.execute(
            "SELECT matched_paper_id, match_confidence FROM paper_references WHERE source_paper_id = ?",
            (pid,),
        ).fetchone()
        conn.close()
        assert row["matched_paper_id"] == pid2
        assert abs(row["match_confidence"] - 0.95) < 0.001

    def test_already_extracted_returns_already_extracted_event(self, client, db):
        """When refs already exist and force is False, returns 'already_extracted' status."""
        pid = _seed_paper(db, filename="already_ref.pdf")
        _insert_ref(db, pid, "Existing Ref", ref_index=1)

        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        assert resp.status_code == 200
        events = _parse_sse(resp)
        complete = _event_by_type(events, "complete")
        assert complete is not None
        # Quirk frozen: when already extracted, status is 'already_extracted', NOT 'ok'
        assert complete["status"] == "already_extracted"
        assert complete["total_extracted"] == 1

    def test_force_true_deletes_existing_and_reextracts(self, client, db):
        """force=True deletes existing references and runs the full pipeline."""
        pid = _seed_paper(db, filename="force_ref.pdf")
        _insert_ref(db, pid, "Old Ref To Delete", ref_index=1)

        new_refs = [{"title": "Brand New Ref", "authors": "New, A.", "year": 2024,
                     "journal": "New J", "doi": ""}]
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(new_refs), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references?force=true")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["status"] == "ok"

        conn = db._connect()
        rows = conn.execute(
            "SELECT title FROM paper_references WHERE source_paper_id = ?", (pid,)
        ).fetchall()
        conn.close()
        titles = [r["title"] for r in rows]
        assert "Old Ref To Delete" not in titles
        assert "Brand New Ref" in titles

    def test_no_pages_extracted_emits_error(self, client, db):
        """When _extract_all_pdf_pages returns [] the stream emits an error event."""
        pid = _seed_paper(db, filename="nopages_ref.pdf")
        with _patch_pages([]):
            resp = client.post(f"/api/papers/{pid}/extract-references")
        assert resp.status_code == 200
        events = _parse_sse(resp)
        err = _event_by_type(events, "error")
        assert err is not None, f"Expected error event; events={events}"
        assert "extrahierbar" in err["message"]

    def test_no_ref_section_emits_error(self, client, db):
        """When _find_reference_section returns empty text the stream emits an error."""
        pid = _seed_paper(db, filename="norefsec_ref.pdf")
        with _patch_pages(), patch.object(_ref_svc, "find_reference_section", return_value=""):
            resp = client.post(f"/api/papers/{pid}/extract-references")
        events = _parse_sse(resp)
        err = _event_by_type(events, "error")
        assert err is not None, f"Expected error event; events={events}"
        assert "Literaturverzeichnis" in err["message"]

    def test_llm_returns_empty_emits_error(self, client, db):
        """When _llm_extract_references returns [] the stream emits an error event.
        Quirk frozen: exact message is 'KI konnte keine Referenzen extrahieren'."""
        pid = _seed_paper(db, filename="nollm_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([]):
            resp = client.post(f"/api/papers/{pid}/extract-references")
        events = _parse_sse(resp)
        err = _event_by_type(events, "error")
        assert err is not None, f"Expected error event; events={events}"
        assert "Referenzen" in err["message"]

    def test_crossref_doi_lookup_called_for_refs_without_doi(self, client, db):
        """CrossRef lookup is triggered for references that have no DOI from LLM."""
        pid = _seed_paper(db, filename="xref_lookup_ref.pdf")
        no_doi_refs = [{"title": "No DOI Paper", "authors": "NoD", "year": 2020,
                        "journal": "J", "doi": ""}]
        crossref_mock = MagicMock(return_value="10.0000/found")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(no_doi_refs), \
             patch.object(_ref_svc, "crossref_doi_lookup", crossref_mock), \
             _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        crossref_mock.assert_called()

    def test_progress_events_emitted_before_complete(self, client, db):
        """At least one 'progress' event must appear before the 'complete' event."""
        pid = _seed_paper(db, filename="progress_ref.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post(f"/api/papers/{pid}/extract-references")
        events = _parse_sse(resp)
        progress = [e for e in events if e.get("type") == "progress"]
        assert len(progress) >= 1


# ===========================================================================
# GET /api/papers/{paper_id}/references
# ===========================================================================

class TestGetReferences:
    """Characterization tests for GET /api/papers/{id}/references."""

    def test_404_paper_not_found(self, client, db):
        resp = client.get("/api/papers/88888/references")
        assert resp.status_code == 404

    def test_empty_list_when_no_refs(self, client, db):
        pid = _seed_paper(db, filename="empty_refs.pdf")
        resp = client.get(f"/api/papers/{pid}/references")
        assert resp.status_code == 200
        body = resp.json()
        assert body["paper_id"] == pid
        assert body["count"] == 0
        assert body["references"] == []

    def test_returns_existing_refs(self, client, db):
        pid = _seed_paper(db, filename="has_refs.pdf")
        _insert_ref(db, pid, "Ref Alpha", ref_index=1, doi="10.1/alpha")
        _insert_ref(db, pid, "Ref Beta", ref_index=2)
        resp = client.get(f"/api/papers/{pid}/references")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        titles = [r["title"] for r in body["references"]]
        assert "Ref Alpha" in titles
        assert "Ref Beta" in titles

    def test_response_shape(self, client, db):
        """Freeze the top-level keys of the GET response."""
        pid = _seed_paper(db, filename="shape_get_refs.pdf")
        resp = client.get(f"/api/papers/{pid}/references")
        assert resp.status_code == 200
        body = resp.json()
        assert {"paper_id", "count", "references"}.issubset(body.keys())

    def test_reference_row_includes_matched_fields(self, client, db):
        """Each reference dict includes matched_paper_id and matched_title."""
        pid = _seed_paper(db, filename="matched_shape.pdf")
        _insert_ref(db, pid, "Shape Check Ref", ref_index=1)
        resp = client.get(f"/api/papers/{pid}/references")
        ref = resp.json()["references"][0]
        assert "matched_paper_id" in ref
        assert "matched_title" in ref
        assert "matched_authors" in ref

    def test_refs_ordered_by_ref_index(self, client, db):
        """References are returned in ref_index order."""
        pid = _seed_paper(db, filename="ordered_refs.pdf")
        _insert_ref(db, pid, "Ref C", ref_index=3)
        _insert_ref(db, pid, "Ref A", ref_index=1)
        _insert_ref(db, pid, "Ref B", ref_index=2)
        resp = client.get(f"/api/papers/{pid}/references")
        refs = resp.json()["references"]
        assert [r["ref_index"] for r in refs] == [1, 2, 3]


# ===========================================================================
# PUT /api/papers/{paper_id}/references/{ref_id}
# ===========================================================================

class TestUpdateReference:
    """Characterization tests for PUT /api/papers/{id}/references/{ref_id}."""

    def test_404_ref_not_found(self, client, db):
        pid = _seed_paper(db, filename="upd404.pdf")
        resp = client.put(
            f"/api/papers/{pid}/references/99999",
            json={"title": "New Title"},
        )
        assert resp.status_code == 404
        assert "Referenz" in resp.json()["detail"]

    def test_404_wrong_paper_id(self, client, db):
        """A ref that belongs to a different paper → 404."""
        pid1 = _seed_paper(db, filename="upd_p1.pdf")
        pid2 = _seed_paper(db, filename="upd_p2.pdf", with_file=False)
        rid = _insert_ref(db, pid1, "Belongs to P1")
        resp = client.put(
            f"/api/papers/{pid2}/references/{rid}",
            json={"title": "Hijack"},
        )
        assert resp.status_code == 404

    def test_updates_title(self, client, db):
        pid = _seed_paper(db, filename="upd_title.pdf")
        rid = _insert_ref(db, pid, "Old Title")
        with _patch_no_match():
            resp = client.put(
                f"/api/papers/{pid}/references/{rid}",
                json={"title": "New Title"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New Title"

    def test_updates_doi_and_rematches(self, client, db):
        """Updating the DOI triggers a rematch and the response reflects new match.
        matched_paper_id must reference an existing paper (FK constraint)."""
        pid = _seed_paper(db, filename="upd_doi.pdf")
        pid2 = _seed_paper(db, filename="upd_doi_target.pdf", with_file=False)
        rid = _insert_ref(db, pid, "DOI Update Ref")
        match_result = {"matched_paper_id": pid2, "match_confidence": 0.99}
        with patch.object(_ref_svc, "match_ref_to_library", return_value=match_result):
            resp = client.put(
                f"/api/papers/{pid}/references/{rid}",
                json={"doi": "10.9999/new-doi"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["doi"] == "10.9999/new-doi"
        assert body["matched_paper_id"] == pid2

    def test_returns_updated_ref_dict_with_matched_fields(self, client, db):
        """The response includes matched_title and matched_authors columns."""
        pid = _seed_paper(db, filename="upd_shape.pdf")
        rid = _insert_ref(db, pid, "Shape Ref")
        with _patch_no_match():
            resp = client.put(
                f"/api/papers/{pid}/references/{rid}",
                json={"authors": "Updated, A."},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "matched_title" in body
        assert "matched_authors" in body
        assert body["authors"] == "Updated, A."

    def test_empty_string_doi_stored_as_none(self, client, db):
        """Empty-string DOI is stored as NULL (not the string '').
        Quirk frozen: the update handler converts '' to None."""
        pid = _seed_paper(db, filename="upd_empty_doi.pdf")
        rid = _insert_ref(db, pid, "Empty DOI Ref", doi="10.1/original")
        with _patch_no_match():
            resp = client.put(
                f"/api/papers/{pid}/references/{rid}",
                json={"doi": ""},
            )
        assert resp.status_code == 200
        conn = db._connect()
        row = conn.execute("SELECT doi FROM paper_references WHERE id=?", (rid,)).fetchone()
        conn.close()
        # Today's quirk: empty string '' is stored as None/NULL
        assert row["doi"] is None


# ===========================================================================
# DELETE /api/papers/{paper_id}/references/{ref_id}
# ===========================================================================

class TestDeleteReference:
    """Characterization tests for DELETE /api/papers/{id}/references/{ref_id}."""

    def test_404_ref_not_found(self, client, db):
        pid = _seed_paper(db, filename="del404.pdf")
        resp = client.delete(f"/api/papers/{pid}/references/77777")
        assert resp.status_code == 404
        assert "Referenz" in resp.json()["detail"]

    def test_404_wrong_paper_id(self, client, db):
        """Ref belonging to a different paper → 404."""
        pid1 = _seed_paper(db, filename="del_p1.pdf")
        pid2 = _seed_paper(db, filename="del_p2.pdf", with_file=False)
        rid = _insert_ref(db, pid1, "Belongs to P1 del")
        resp = client.delete(f"/api/papers/{pid2}/references/{rid}")
        assert resp.status_code == 404

    def test_returns_ok(self, client, db):
        pid = _seed_paper(db, filename="del_ok.pdf")
        rid = _insert_ref(db, pid, "Delete Me")
        resp = client.delete(f"/api/papers/{pid}/references/{rid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ref_removed_from_db(self, client, db):
        pid = _seed_paper(db, filename="del_db.pdf")
        rid = _insert_ref(db, pid, "Gone After Delete")
        client.delete(f"/api/papers/{pid}/references/{rid}")
        conn = db._connect()
        row = conn.execute("SELECT id FROM paper_references WHERE id=?", (rid,)).fetchone()
        conn.close()
        assert row is None

    def test_other_refs_not_deleted(self, client, db):
        """Deleting one ref leaves sibling refs intact."""
        pid = _seed_paper(db, filename="del_sibling.pdf")
        rid1 = _insert_ref(db, pid, "Keep Me", ref_index=1)
        rid2 = _insert_ref(db, pid, "Delete Me Too", ref_index=2)
        client.delete(f"/api/papers/{pid}/references/{rid2}")
        conn = db._connect()
        row = conn.execute("SELECT id FROM paper_references WHERE id=?", (rid1,)).fetchone()
        conn.close()
        assert row is not None


# ===========================================================================
# POST /api/papers/{paper_id}/references/add
# ===========================================================================

class TestAddReference:
    """Characterization tests for POST /api/papers/{id}/references/add."""

    def test_404_paper_not_found(self, client, db):
        resp = client.post(
            "/api/papers/55555/references/add",
            json={"title": "Some Ref"},
        )
        assert resp.status_code == 404

    def test_adds_ref_with_source_manual(self, client, db):
        pid = _seed_paper(db, filename="add_manual.pdf")
        with _patch_no_match():
            resp = client.post(
                f"/api/papers/{pid}/references/add",
                json={"title": "Manual Ref", "authors": "Man, U.", "year": 2022,
                      "journal": "Manual Journal", "doi": ""},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Manual Ref"

        conn = db._connect()
        row = conn.execute(
            "SELECT source FROM paper_references WHERE source_paper_id=?", (pid,)
        ).fetchone()
        conn.close()
        assert row["source"] == "manual"

    def test_ref_index_is_next_after_existing(self, client, db):
        """ref_index is max(existing) + 1."""
        pid = _seed_paper(db, filename="add_idx.pdf")
        _insert_ref(db, pid, "Existing 1", ref_index=1)
        _insert_ref(db, pid, "Existing 2", ref_index=2)
        with _patch_no_match():
            resp = client.post(
                f"/api/papers/{pid}/references/add",
                json={"title": "New Ref"},
            )
        assert resp.status_code == 200
        assert resp.json()["ref_index"] == 3

    def test_doi_match_stored(self, client, db):
        """When the DOI matches a library paper, matched_paper_id is set.
        FK constraint requires the matched paper to actually exist."""
        pid = _seed_paper(db, filename="add_doi_match.pdf")
        pid_target = _seed_paper(db, filename="add_doi_target.pdf", with_file=False)
        match_result = {"matched_paper_id": pid_target, "match_confidence": 0.98}
        with patch.object(_ref_svc, "match_ref_to_library", return_value=match_result):
            resp = client.post(
                f"/api/papers/{pid}/references/add",
                json={"title": "DOI Match Ref", "doi": "10.1234/match"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched_paper_id"] == pid_target
        assert abs(body["match_confidence"] - 0.98) < 0.001

    def test_response_includes_matched_columns(self, client, db):
        """Response dict must include matched_title and matched_authors."""
        pid = _seed_paper(db, filename="add_shape.pdf")
        with _patch_no_match():
            resp = client.post(
                f"/api/papers/{pid}/references/add",
                json={"title": "Shape Add Ref"},
            )
        body = resp.json()
        assert "matched_title" in body
        assert "matched_authors" in body

    def test_first_ref_gets_index_1(self, client, db):
        """First ref on a paper with no existing refs gets ref_index = 1."""
        pid = _seed_paper(db, filename="first_ref.pdf")
        with _patch_no_match():
            resp = client.post(
                f"/api/papers/{pid}/references/add",
                json={"title": "First Ever Ref"},
            )
        assert resp.status_code == 200
        assert resp.json()["ref_index"] == 1


# ===========================================================================
# POST /api/papers/bulk-extract-references
# ===========================================================================

class TestBulkExtractReferences:
    """Characterization tests for POST /api/papers/bulk-extract-references."""

    def test_response_is_sse_stream(self, client, db):
        resp = client.post("/api/papers/bulk-extract-references")
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_empty_when_no_candidates(self, client, db):
        """When all papers already have references (or there are none), emits
        complete with processed=0 immediately."""
        resp = client.post("/api/papers/bulk-extract-references")
        assert resp.status_code == 200
        events = _parse_sse(resp)
        complete = _event_by_type(events, "complete")
        assert complete is not None, f"No complete event; events={events}"
        assert complete["processed"] == 0
        assert complete["total_references"] == 0

    def test_complete_event_shape(self, client, db):
        """Freeze the key set of the 'complete' event."""
        resp = client.post("/api/papers/bulk-extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        expected = {"type", "processed", "total_references", "total_in_library", "results"}
        assert expected.issubset(complete.keys())

    def test_paper_with_existing_refs_is_skipped(self, client, db):
        """A paper that already has references is excluded from bulk extraction."""
        pid = _seed_paper(db, filename="bulk_existing.pdf")
        _insert_ref(db, pid, "Pre-existing ref")
        # No mocks for LLM needed since paper is skipped (has refs already)
        resp = client.post("/api/papers/bulk-extract-references")
        complete = _event_by_type(_parse_sse(resp), "complete")
        assert complete is not None
        assert complete["processed"] == 0  # skipped because already has refs

    def test_paper_without_file_gets_file_not_found_status(self, client, db):
        """A paper with a filename but no file on disk gets status='file_not_found'."""
        pid = _seed_paper(db, filename="bulk_missing_file.pdf", with_file=False)
        resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        assert result["status"] == "file_not_found"

    def test_no_pages_from_pdf_gets_no_text_status(self, client, db):
        """When _extract_all_pdf_pages returns [] the paper gets status='no_text'."""
        pid = _seed_paper(db, filename="bulk_notext.pdf")
        with _patch_pages([]):
            resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        assert result["status"] == "no_text"

    def test_no_ref_section_gets_no_ref_section_status(self, client, db):
        """When no reference section is found the paper gets status='no_ref_section'."""
        pid = _seed_paper(db, filename="bulk_norefsec.pdf")
        with _patch_pages(), patch.object(_ref_svc, "find_reference_section", return_value=""):
            resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        assert result["status"] == "no_ref_section"

    def test_llm_empty_result_gets_no_refs_extracted_status(self, client, db):
        """When LLM returns no refs the paper gets status='no_refs_extracted'."""
        pid = _seed_paper(db, filename="bulk_nollm.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([]):
            resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        assert result["status"] == "no_refs_extracted"

    def test_happy_path_saves_refs_and_emits_ok(self, client, db):
        """Happy path: refs saved, paper_result has status='ok', counts correct."""
        pid = _seed_paper(db, filename="bulk_happy.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        assert result["status"] == "ok"
        assert result["total_extracted"] == len(FAKE_REFS)

        complete = _event_by_type(events, "complete")
        assert complete is not None
        assert complete["processed"] == 1
        assert complete["total_references"] == len(FAKE_REFS)

        conn = db._connect()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_references WHERE source_paper_id=?", (pid,)
        ).fetchone()["cnt"]
        conn.close()
        assert count == len(FAKE_REFS)

    def test_paper_result_event_shape(self, client, db):
        """Freeze the key set of the 'paper_result' event."""
        pid = _seed_paper(db, filename="bulk_shape.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_crossref_no_doi(), _patch_no_match():
            resp = client.post("/api/papers/bulk-extract-references")
        events = _parse_sse(resp)
        result = _event_by_type(events, "paper_result")
        assert result is not None
        expected = {"type", "paper_id", "title", "status"}
        assert expected.issubset(result.keys())

    def test_source_column_is_pdf_llm(self, client, db):
        """Refs inserted by bulk-extract use source='pdf_llm', not 'manual'."""
        pid = _seed_paper(db, filename="bulk_source.pdf")
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS[:1]), \
             _patch_crossref_no_doi(), _patch_no_match():
            client.post("/api/papers/bulk-extract-references")
        conn = db._connect()
        row = conn.execute(
            "SELECT source FROM paper_references WHERE source_paper_id=?", (pid,)
        ).fetchone()
        conn.close()
        assert row["source"] == "pdf_llm"
