"""Characterization tests for maintenance endpoints.

Freezes TODAY's behaviour of:
  POST /api/maintenance/rebuild-links       (rebuild category folders + links)
  POST /api/maintenance/update-page-counts  (update page counts from PDFs)
  POST /api/maintenance/full-refresh        (SSE stream — 6-step full refresh)

FS effects run against the temp base dir (conftest sets LITERATUR_BASE_DIR to a
temp dir; Config.ALL_DIR and Config.CATEGORIES_DIR live under it).

External IO is mocked:
  - fitz.open         patched via patch('fitz.open', ...) — import is local
  - validate_propose  patched via patch.object(validation_policy, ...) AsyncMock
  - validate_apply    patched via patch.object(validation_policy, ...) AsyncMock
  - fetch_crossref_metadata, extract_text_from_pdf, categorize_with_llm,
    OpenAlexClient, chunk_paper_to_db, create_symlinks — all patched on the
    routers.maintenance module (their new home after #92)

Windows-specific quirks frozen:
  - create_symlinks uses os.link() (hardlink) on win32, NOT os.symlink()
  - rebuild-links always returns removed=0 (static constant, no actual count)

Purpose: catch regressions when maintenance endpoints are extracted as a
router/service (issue #92). Assert on observable behaviour (HTTP response
shapes, DB side-effects), NOT on internal function names.
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

import routers.maintenance
import validation_policy
import webapp
from literature_manager import Config


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
# Shared DB helpers
# ---------------------------------------------------------------------------

def _seed_paper(
    db,
    filename: str = "maint_test.pdf",
    with_file: bool = False,
    doi: str = "10.0/maint",
) -> int:
    """Insert a paper row and return its paper_id. Optionally create a dummy file."""
    if with_file:
        filepath = Path(Config.ALL_DIR) / filename
        filepath.write_bytes(b"%PDF-1.4 fake content for maintenance tests")
    file_hash = f"maint-{filename}"
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_hash, filename, filename, "Maintenance Test Paper", "Maint, A.", 2023, doi or None),
        )
        pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_category(db, name: str = "MaintCat") -> int:
    """Insert (or find) a category row and return its id."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO categories (name, description, keywords) VALUES (?, ?, ?)",
            (name, "", ""),
        )
        conn.commit()
        if cur.lastrowid:
            cid = cur.lastrowid
        else:
            cid = conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()["id"]
    finally:
        conn.close()
    return cid


def _assign_category(db, paper_id: int, cat_id: int):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO paper_categories (paper_id, category_id, confidence, assigned_by) "
            "VALUES (?, ?, ?, ?)",
            (paper_id, cat_id, 0.9, "manual"),
        )
        conn.commit()
    finally:
        conn.close()


def _get_page_count(db, paper_id: int):
    conn = db._connect()
    try:
        row = conn.execute("SELECT page_count FROM papers WHERE id=?", (paper_id,)).fetchone()
        return row["page_count"] if row else None
    finally:
        conn.close()


def _get_field(db, paper_id: int, field: str):
    conn = db._connect()
    try:
        row = conn.execute(f"SELECT {field} FROM papers WHERE id=?", (paper_id,)).fetchone()
        return row[field] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# full-refresh IO patch helpers
# ---------------------------------------------------------------------------

def _mock_fitz_doc(page_count: int = 5) -> MagicMock:
    doc = MagicMock()
    doc.__len__ = lambda s: page_count
    doc.close = MagicMock()
    return doc


def _all_io_patches(page_count: int = 5, chunk_return: int = 0):
    """Return list of patch CMs that silence every external IO in full-refresh."""
    doc = _mock_fitz_doc(page_count)
    return [
        patch("fitz.open", return_value=doc),
        patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                     return_value={"confidence": "low", "changes": {}, "category_suggestions": []}),
        patch.object(validation_policy, "validate_apply", new_callable=AsyncMock,
                     return_value={"status": "ok"}),
        patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None),
        patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""),
        patch.object(routers.maintenance, "categorize_with_llm", return_value=[]),
        patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
            fetch_works_by_doi=MagicMock(return_value=[]),
        )),
        patch.object(routers.maintenance, "chunk_paper_to_db", return_value=chunk_return),
    ]


# ===========================================================================
# POST /api/maintenance/rebuild-links
# ===========================================================================

class TestRebuildLinks:
    """Characterization tests for POST /api/maintenance/rebuild-links.

    The endpoint:
      1. Deletes all files and empty subdirs under CATEGORIES_DIR
      2. Calls os.makedirs(CATEGORIES_DIR)
      3. Calls _rebuild_category_folders(db)  — creates category subdirs
      4. Iterates all papers, calls create_symlinks for each
         → success: rebuilt++; exception: failed++, error appended
      5. Returns {status, rebuilt, failed, removed=0, errors}

    QUIRK: removed is always 0 (no counting of actually removed items).
    Windows: create_symlinks uses os.link() (hardlink), not os.symlink().
    """

    def test_status_code_200(self, client, db):
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.status_code == 200

    def test_response_shape(self, client, db):
        """Freeze the response key set."""
        resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert {"status", "rebuilt", "failed", "removed", "errors"}.issubset(body.keys())

    def test_status_always_ok(self, client, db):
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.json()["status"] == "ok"

    def test_empty_library_rebuilt_zero_failed_zero(self, client, db):
        """Empty library → rebuilt=0, failed=0, errors=[]."""
        resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert body["rebuilt"] == 0
        assert body["failed"] == 0
        assert body["errors"] == []

    def test_removed_is_always_zero(self, client, db):
        """QUIRK frozen: removed=0 always regardless of what was actually removed."""
        _seed_paper(db, filename="rl_removed_quirk.pdf")
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.json()["removed"] == 0

    def test_one_paper_no_categories_rebuilt_one(self, client, db):
        """A paper exists but has no categories → create_symlinks called without
        creating any files; rebuilt=1 because no exception was raised."""
        _seed_paper(db, filename="rl_nocat.pdf")
        resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert body["rebuilt"] == 1
        assert body["failed"] == 0

    def test_multiple_papers_rebuilt_equals_paper_count(self, client, db):
        """rebuilt counts each successful create_symlinks call."""
        _seed_paper(db, filename="rl_multi_a.pdf")
        _seed_paper(db, filename="rl_multi_b.pdf")
        resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert body["rebuilt"] == 2
        assert body["failed"] == 0

    def test_create_symlinks_exception_increments_failed(self, client, db):
        """When create_symlinks raises, failed is incremented and status stays 'ok'."""
        _seed_paper(db, filename="rl_fail.pdf")
        with patch.object(routers.maintenance, "create_symlinks", side_effect=OSError("link error")):
            resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert body["failed"] == 1
        assert body["rebuilt"] == 0
        assert body["status"] == "ok"

    def test_exception_message_appears_in_errors_list(self, client, db):
        """The error string (filename + exception) is collected in 'errors'."""
        _seed_paper(db, filename="rl_errmsg.pdf")
        with patch.object(routers.maintenance, "create_symlinks", side_effect=OSError("distinct_err_msg")):
            resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert len(body["errors"]) >= 1

    def test_categories_dir_created_when_absent(self, client, db):
        """CATEGORIES_DIR is (re)created after the call even if it was missing."""
        import shutil
        cat_dir = Path(Config.CATEGORIES_DIR)
        if cat_dir.exists():
            shutil.rmtree(cat_dir)
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.status_code == 200
        assert cat_dir.is_dir()

    def test_category_subdir_created_under_categories_dir(self, client, db):
        """When a category exists, _rebuild_category_folders creates its subdir."""
        _insert_category(db, "RebuildSubDirCat")
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.status_code == 200
        assert (Path(Config.CATEGORIES_DIR) / "RebuildSubDirCat").is_dir()

    def test_mixed_success_and_failure_counted_separately(self, client, db):
        """rebuilt and failed are independent counters."""
        _seed_paper(db, filename="rl_ok.pdf")
        _seed_paper(db, filename="rl_bad.pdf")
        call_count = [0]

        def side_effect(db_, paper_id, filename):
            call_count[0] += 1
            if "rl_bad" in filename:
                raise OSError("intentional")

        with patch.object(routers.maintenance, "create_symlinks", side_effect=side_effect):
            resp = client.post("/api/maintenance/rebuild-links")
        body = resp.json()
        assert body["rebuilt"] == 1
        assert body["failed"] == 1
        assert body["status"] == "ok"

    def test_windows_creates_hardlink_not_symlink(self, client, db):
        """QUIRK frozen (Windows only): on win32 create_symlinks uses os.link()
        (hardlink), NOT os.symlink(). A file (not a symlink) appears in the
        category dir after rebuild-links."""
        if sys.platform != "win32":
            pytest.skip("Windows hardlink behavior test only runs on win32")
        pid = _seed_paper(db, filename="rl_win_link.pdf", with_file=True)
        cid = _insert_category(db, "WinLinkCat")
        _assign_category(db, pid, cid)
        resp = client.post("/api/maintenance/rebuild-links")
        assert resp.status_code == 200
        link_path = Path(Config.CATEGORIES_DIR) / "WinLinkCat" / "rl_win_link.pdf"
        assert link_path.exists(), f"Expected hardlink at {link_path}"
        assert not link_path.is_symlink(), "Expected hardlink/copy (not symlink) on Windows"


# ===========================================================================
# POST /api/maintenance/update-page-counts
# ===========================================================================

class TestUpdatePageCounts:
    """Characterization tests for POST /api/maintenance/update-page-counts.

    The endpoint:
      1. Imports fitz locally (import fitz as _fitz)
      2. Iterates all papers
      3. For each: opens Config.ALL_DIR/<basename(filename)> via fitz.open()
         → success: stores page_count, updated++
         → exception: failed++ (silently, no error list returned)
      4. Returns {status, updated, failed}
    """

    def test_status_code_200(self, client, db):
        resp = client.post("/api/maintenance/update-page-counts")
        assert resp.status_code == 200

    def test_response_shape(self, client, db):
        """Freeze the response key set."""
        resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert {"status", "updated", "failed"}.issubset(body.keys())

    def test_status_always_ok(self, client, db):
        resp = client.post("/api/maintenance/update-page-counts")
        assert resp.json()["status"] == "ok"

    def test_empty_library_updated_zero_failed_zero(self, client, db):
        resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert body["updated"] == 0
        assert body["failed"] == 0

    def test_paper_with_missing_pdf_increments_failed(self, client, db):
        """If the PDF is absent, fitz.open raises → failed incremented, updated=0."""
        _seed_paper(db, filename="pc_missing.pdf", with_file=False)
        resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert body["failed"] == 1
        assert body["updated"] == 0

    def test_paper_with_valid_pdf_increments_updated(self, client, db):
        """Mocked fitz returning page_count=10 → updated=1, failed=0."""
        _seed_paper(db, filename="pc_valid.pdf", with_file=True)
        mock_doc = _mock_fitz_doc(page_count=10)
        with patch("fitz.open", return_value=mock_doc):
            resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert body["updated"] == 1
        assert body["failed"] == 0

    def test_page_count_stored_in_db(self, client, db):
        """The page_count value returned by fitz is written to papers.page_count."""
        pid = _seed_paper(db, filename="pc_persist.pdf", with_file=True)
        mock_doc = _mock_fitz_doc(page_count=7)
        with patch("fitz.open", return_value=mock_doc):
            client.post("/api/maintenance/update-page-counts")
        assert _get_page_count(db, pid) == 7

    def test_fitz_exception_does_not_raise_http_error(self, client, db):
        """Exceptions in fitz.open are caught per-paper; endpoint never returns 5xx."""
        _seed_paper(db, filename="pc_exc.pdf", with_file=True)
        with patch("fitz.open", side_effect=RuntimeError("fitz boom")):
            resp = client.post("/api/maintenance/update-page-counts")
        assert resp.status_code == 200
        assert resp.json()["failed"] == 1

    def test_response_has_no_errors_list(self, client, db):
        """QUIRK: unlike rebuild-links, update-page-counts does NOT return an 'errors' list.
        Failures are only counted in 'failed'; error details are not surfaced."""
        _seed_paper(db, filename="pc_noerrs.pdf", with_file=False)
        resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert "errors" not in body

    def test_multiple_papers_counted_individually(self, client, db):
        """updated and failed are summed across all papers independently."""
        _seed_paper(db, filename="pc_good.pdf", with_file=True)
        _seed_paper(db, filename="pc_bad.pdf", with_file=False)

        def _side_effect(path):
            if "pc_good" in path:
                return _mock_fitz_doc(3)
            raise FileNotFoundError(f"No such file: {path}")

        with patch("fitz.open", side_effect=_side_effect):
            resp = client.post("/api/maintenance/update-page-counts")
        body = resp.json()
        assert body["updated"] == 1
        assert body["failed"] == 1

    def test_filepath_uses_basename_of_filename(self, client, db):
        """The PDF path is built from Config.ALL_DIR + os.path.basename(filename).
        Papers with paths (e.g. 'subdir/file.pdf') use only the basename."""
        pid = _seed_paper(db, filename="basename_test.pdf", with_file=True)
        # Temporarily put a subdir prefix on the filename column
        conn = db._connect()
        try:
            conn.execute(
                "UPDATE papers SET filename='subdir/basename_test.pdf' WHERE id=?", (pid,)
            )
            conn.commit()
        finally:
            conn.close()
        mock_doc = _mock_fitz_doc(4)
        with patch("fitz.open", return_value=mock_doc) as mock_open:
            resp = client.post("/api/maintenance/update-page-counts")
        # fitz.open should be called with just the basename in ALL_DIR
        called_path = str(mock_open.call_args[0][0])
        assert "subdir" not in called_path or called_path.endswith("basename_test.pdf")
        assert "basename_test.pdf" in called_path


# ===========================================================================
# POST /api/maintenance/full-refresh
# ===========================================================================

class TestFullRefresh:
    """Characterization tests for POST /api/maintenance/full-refresh.

    The endpoint returns text/event-stream.  Events are JSON with a 'type' key:
      progress: {type, message, current, total, percent, step, stats}
      done:     {type, stats, total}  (final event; NOT 'complete')

    6 steps in order: page_count → validate → abstracts → categories → openalex → chunks

    Step 2 auto-applies ONLY high-confidence proposals (confidence == 'high').
    Step 3 saves CrossRef abstracts if found; skips LLM if pdf_text is empty.
    Step 4 runs only if Config.KICONNECT_API_KEY is set (= 'test-key' in conftest).
    Step 5 updates openalex_id + cited_by_count for papers with DOIs.
    Step 6 increments stats.chunks only if chunk_paper_to_db returns > 0.
    """

    def test_response_is_sse_stream(self, client, db):
        """The endpoint must return text/event-stream."""
        resp = client.post("/api/maintenance/full-refresh")
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_empty_library_emits_done_with_keine_paper(self, client, db):
        """Empty library → single 'done' event containing 'Keine Paper gefunden'."""
        resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        events = _parse_sse(resp)
        done = _event_by_type(events, "done")
        assert done is not None, f"No 'done' event; events={events}"
        assert "Keine Paper gefunden" in done.get("message", "")

    def test_empty_library_only_done_event(self, client, db):
        """Empty library fast-path: no progress events, only the 'done' event."""
        resp = client.post("/api/maintenance/full-refresh")
        events = _parse_sse(resp)
        non_done = [e for e in events if e.get("type") != "done"]
        assert non_done == [], f"Expected no progress events on empty lib; got {non_done}"

    def test_with_papers_emits_done_event(self, client, db):
        """Non-empty library: stream must end with a 'done' event."""
        _seed_paper(db, filename="fr_basic.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None, f"No 'done' event; events={_parse_sse(resp)}"

    def test_done_event_has_stats_and_total(self, client, db):
        """Freeze 'done' event shape: must include 'stats' dict and 'total'."""
        _seed_paper(db, filename="fr_shape.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert "stats" in done
        assert "total" in done

    def test_done_event_stats_has_all_six_keys(self, client, db):
        """The stats dict in 'done' contains all 6 step counters."""
        _seed_paper(db, filename="fr_statkeys.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        expected = {"page_count", "validated", "abstracts", "categorized", "openalex", "chunks", "errors"}
        assert expected.issubset(done["stats"].keys()), (
            f"Missing stats keys: {expected - done['stats'].keys()}"
        )

    def test_done_total_equals_paper_count(self, client, db):
        """done.total equals the number of papers in the DB at the time of the call."""
        _seed_paper(db, filename="fr_total1.pdf", with_file=True)
        _seed_paper(db, filename="fr_total2.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["total"] == 2

    def test_progress_events_emitted_with_correct_shape(self, client, db):
        """Freeze the key set of 'progress' events."""
        _seed_paper(db, filename="fr_pshape.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        events = _parse_sse(resp)
        progress = [e for e in events if e.get("type") == "progress"]
        assert progress, "Expected at least one progress event"
        expected = {"type", "message", "current", "total", "percent", "step", "stats"}
        assert expected.issubset(progress[0].keys()), (
            f"Missing progress keys: {expected - progress[0].keys()}"
        )

    def test_all_six_step_names_appear_in_progress_events(self, client, db):
        """All 6 step name identifiers must appear across progress events."""
        _seed_paper(db, filename="fr_steps.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        steps = {e.get("step") for e in _parse_sse(resp) if e.get("type") == "progress"}
        expected = {"page_count", "validate", "abstracts", "categories", "openalex", "chunks"}
        assert expected.issubset(steps), f"Missing step names: {expected - steps}"

    def test_final_event_type_is_done_not_complete(self, client, db):
        """QUIRK frozen: unlike other SSE endpoints that use 'complete', full-refresh
        uses 'done' as its final event type."""
        _seed_paper(db, filename="fr_done_type.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        events = _parse_sse(resp)
        last = events[-1]
        assert last.get("type") == "done", (
            f"QUIRK: expected final event type='done', got '{last.get('type')}'"
        )

    def test_step1_page_count_stat_incremented_for_valid_pdf(self, client, db):
        """Step 1 (page_count): successful fitz open → stats.page_count += 1."""
        _seed_paper(db, filename="fr_pc_stat.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches(page_count=8):
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["page_count"] == 1

    def test_step1_page_count_persisted_to_db(self, client, db):
        """Step 1: the page count from fitz is written to papers.page_count."""
        pid = _seed_paper(db, filename="fr_pc_db.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches(page_count=12):
                stack.enter_context(cm)
            client.post("/api/maintenance/full-refresh")
        assert _get_page_count(db, pid) == 12

    def test_step2_high_confidence_proposal_auto_applied(self, client, db):
        """Step 2: a high-Confidence proposal triggers validate_apply automatically."""
        _seed_paper(db, filename="fr_hi_conf.pdf", with_file=True)
        apply_mock = AsyncMock(return_value={"status": "ok"})
        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "high",
                                        "changes": {"title": "Updated Title"},
                                        "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", apply_mock), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
                 fetch_works_by_doi=MagicMock(return_value=[]))), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        apply_mock.assert_called_once()

    def test_step2_low_confidence_proposal_not_auto_applied(self, client, db):
        """Step 2: a low-Confidence proposal is NOT auto-applied (validate_apply skipped)."""
        _seed_paper(db, filename="fr_lo_conf.pdf", with_file=True)
        apply_mock = AsyncMock(return_value={"status": "ok"})
        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "low",
                                        "changes": {"title": "Would Update"},
                                        "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", apply_mock), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
                 fetch_works_by_doi=MagicMock(return_value=[]))), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        apply_mock.assert_not_called()

    def test_step2_high_confidence_no_changes_not_applied(self, client, db):
        """Step 2: high Confidence but empty changes AND no category_suggestions
        → validate_apply is NOT called (no-op proposal)."""
        _seed_paper(db, filename="fr_hi_noop.pdf", with_file=True)
        apply_mock = AsyncMock(return_value={"status": "ok"})
        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "high",
                                        "changes": {},
                                        "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", apply_mock), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
                 fetch_works_by_doi=MagicMock(return_value=[]))), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        apply_mock.assert_not_called()

    def test_step3_crossref_abstract_stored_in_db(self, client, db):
        """Step 3: when fetch_crossref_metadata returns an abstract, it is
        saved to papers.abstract and stats.abstracts incremented."""
        pid = _seed_paper(db, filename="fr_abst.pdf", with_file=False, doi="10.0/abst")
        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "low", "changes": {}, "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", new_callable=AsyncMock,
                          return_value={"status": "ok"}), \
             patch.object(routers.maintenance, "fetch_crossref_metadata",
                          return_value={"abstract": "An important scientific abstract here."}), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
                 fetch_works_by_doi=MagicMock(return_value=[]))), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["abstracts"] == 1

        stored = _get_field(db, pid, "abstract")
        assert stored == "An important scientific abstract here."

    def test_step3_existing_abstract_not_overwritten(self, client, db):
        """Step 3: papers that already have an abstract are skipped (stats.abstracts stays 0)."""
        pid = _seed_paper(db, filename="fr_existing_abs.pdf", with_file=False)
        # Pre-set an abstract
        conn = db._connect()
        try:
            conn.execute("UPDATE papers SET abstract='Existing abstract.' WHERE id=?", (pid,))
            conn.commit()
        finally:
            conn.close()

        crossref_mock = MagicMock(return_value={"abstract": "New from CrossRef"})
        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "low", "changes": {}, "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", new_callable=AsyncMock,
                          return_value={"status": "ok"}), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", crossref_mock), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=MagicMock(
                 fetch_works_by_doi=MagicMock(return_value=[]))), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["abstracts"] == 0  # skipped because already has one
        # CrossRef must NOT have been called for a paper that already has an abstract
        crossref_mock.assert_not_called()

    def test_step5_openalex_updates_cited_by_count_and_id(self, client, db):
        """Step 5: a Work returned from OpenAlex is written to DB (openalex_id,
        cited_by_count, openalex_updated_at)."""
        pid = _seed_paper(db, filename="fr_oa.pdf", with_file=True, doi="10.0/oa_test")
        mock_work = MagicMock()
        mock_work.doi = "10.0/oa_test"
        mock_work.id = "W1234567"
        mock_work.cited_by_count = 99
        oa_client = MagicMock(fetch_works_by_doi=MagicMock(return_value=[mock_work]))

        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "low", "changes": {}, "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", new_callable=AsyncMock,
                          return_value={"status": "ok"}), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=oa_client), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["openalex"] == 1

        assert _get_field(db, pid, "openalex_id") == "W1234567"
        assert _get_field(db, pid, "cited_by_count") == 99

    def test_step5_paper_without_doi_not_queried(self, client, db):
        """Step 5: papers with no DOI are not included in the OpenAlex batch query."""
        _seed_paper(db, filename="fr_oa_nodoi.pdf", with_file=False, doi="")
        oa_client = MagicMock(fetch_works_by_doi=MagicMock(return_value=[]))

        doc = _mock_fitz_doc()
        with patch("fitz.open", return_value=doc), \
             patch.object(validation_policy, "validate_propose", new_callable=AsyncMock,
                          return_value={"confidence": "low", "changes": {}, "category_suggestions": []}), \
             patch.object(validation_policy, "validate_apply", new_callable=AsyncMock,
                          return_value={"status": "ok"}), \
             patch.object(routers.maintenance, "fetch_crossref_metadata", return_value=None), \
             patch.object(routers.maintenance, "extract_text_from_pdf", return_value=""), \
             patch.object(routers.maintenance, "categorize_with_llm", return_value=[]), \
             patch.object(routers.maintenance, "OpenAlexClient", return_value=oa_client), \
             patch.object(routers.maintenance, "chunk_paper_to_db", return_value=0):
            resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        # fetch_works_by_doi called with empty list → no paper queried
        oa_client.fetch_works_by_doi.assert_called_once_with([])

    def test_step6_chunk_stat_incremented_when_new_chunks_created(self, client, db):
        """Step 6: chunk_paper_to_db returning > 0 increments stats.chunks."""
        _seed_paper(db, filename="fr_chunk_ok.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches(chunk_return=5):
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["chunks"] == 1

    def test_step6_chunk_stat_not_incremented_when_zero_chunks(self, client, db):
        """Step 6: chunk_paper_to_db returning 0 leaves stats.chunks=0 (already chunked)."""
        _seed_paper(db, filename="fr_chunk_zero.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches(chunk_return=0):
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["chunks"] == 0

    def test_step6_paper_without_file_skipped_in_chunking(self, client, db):
        """Step 6: papers with no file on disk are silently skipped (no error, no chunk)."""
        _seed_paper(db, filename="fr_chunk_nofile.pdf", with_file=False)
        chunk_mock = MagicMock(return_value=0)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            stack.enter_context(patch.object(routers.maintenance, "chunk_paper_to_db", chunk_mock))
            resp = client.post("/api/maintenance/full-refresh")
        assert resp.status_code == 200
        # chunk_paper_to_db must NOT be called because the file is missing
        chunk_mock.assert_not_called()

    def test_validated_stat_incremented_per_paper(self, client, db):
        """Step 2: stats.validated is incremented for each paper processed, regardless
        of whether the proposal was applied or not."""
        _seed_paper(db, filename="fr_val1.pdf", with_file=True)
        _seed_paper(db, filename="fr_val2.pdf", with_file=True)
        with ExitStack() as stack:
            for cm in _all_io_patches():
                stack.enter_context(cm)
            resp = client.post("/api/maintenance/full-refresh")
        done = _event_by_type(_parse_sse(resp), "done")
        assert done is not None
        assert done["stats"]["validated"] == 2
