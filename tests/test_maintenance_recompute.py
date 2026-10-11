"""POST /api/maintenance/recompute — block ② of Settings → Refresh (#213).

Replaces the characterization of the former full refresh and of the single
maintenance endpoints (page counts, category links, the two index runs): each
of the five steps is checked by its effect on the database or the file system,
plus the stream format, the refusals, and the promise that no curated field is
ever written.

External I/O is faked at the system boundary only: PyMuPDF (``fitz.open``),
OpenAlex (``routers.maintenance.OpenAlexClient``), the PDF text extraction of
the chunker and the embedding HTTP call (``embedding_index.embed_texts``).
Every test gets its own library folders (``Config.ALL_DIR`` /
``Config.CATEGORIES_DIR`` under ``tmp_path``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import embedding_index
import pdf_chunking
import routers.maintenance as maintenance
from literature_manager import Config
from tests.llm_helpers import configure_llm

ALL_STEPS = ["page_counts", "citations", "passages", "search_index", "category_folders"]
# What a recompute may write on an item; everything else is curated.
DERIVED_COLUMNS = {"page_count", "openalex_id", "cited_by_count", "openalex_updated_at"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def library(tmp_path, monkeypatch, db):
    """Fresh PDF and category folders for one test."""
    all_dir = tmp_path / "all"
    cat_dir = tmp_path / "kategorien"
    all_dir.mkdir()
    cat_dir.mkdir()
    monkeypatch.setattr(Config, "ALL_DIR", str(all_dir))
    monkeypatch.setattr(Config, "CATEGORIES_DIR", str(cat_dir))
    monkeypatch.setattr(Config, "LINK_MODE", "copy")
    return tmp_path


def _events(resp) -> list:
    return [json.loads(line[6:]) for line in resp.text.split("\n") if line.startswith("data: ")]


def _run(client, steps):
    resp = client.post("/api/maintenance/recompute", json={"steps": steps})
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert events and events[-1]["type"] == "done", events
    return events


def _add_paper(db, filename="p.pdf", *, pdf=True, doi="10.1000/ABC", title="A Title", **extra) -> int:
    if pdf and filename:
        (Path(Config.ALL_DIR) / filename).write_bytes(b"%PDF-1.4 recompute test " + filename.encode())
    # A real item always has its cite key (add_paper gives it one); without,
    # opening the Database would backfill it.
    cols = {"file_hash": f"hash-{filename or title}", "filename": filename or "", "original_filename": filename or "",
            "title": title, "authors": "Doe, J.", "year": 2021, "doi": doi,
            "cite_key": f"Doe2021-{filename or title}", **extra}
    conn = db._connect()
    try:
        cur = conn.execute(
            f"INSERT INTO papers ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            list(cols.values()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _category(db, name, paper_id=None, parent_id=None) -> int:
    conn = db._connect()
    try:
        cid = conn.execute(
            "INSERT INTO categories (name, description, keywords, parent_id) VALUES (?, '', '', ?)",
            (name, parent_id),
        ).lastrowid
        if paper_id:
            conn.execute(
                "INSERT INTO paper_categories (paper_id, category_id, confidence, assigned_by) VALUES (?, ?, 1.0, 'manual')",
                (paper_id, cid),
            )
        conn.commit()
        return cid
    finally:
        conn.close()


def _row(db, paper_id) -> dict:
    conn = db._connect()
    try:
        return dict(conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone())
    finally:
        conn.close()


def _count(db, sql, *args) -> int:
    conn = db._connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _fitz_doc(pages: int) -> MagicMock:
    doc = MagicMock()
    doc.__len__ = lambda self: pages
    return doc


def _work(doi, oa_id="W1", cited=7):
    w = MagicMock()
    w.doi, w.id, w.cited_by_count = doi, oa_id, cited
    return w


def _openalex(works=None, error=None):
    client = MagicMock()
    if error:
        client.fetch_works_by_doi.side_effect = error
    else:
        client.fetch_works_by_doi.return_value = works or []
    return patch.object(maintenance, "OpenAlexClient", return_value=client), client


# ---------------------------------------------------------------------------
# Request and stream
# ---------------------------------------------------------------------------

class TestRequest:
    def test_no_step_is_refused(self, client, library):
        resp = client.post("/api/maintenance/recompute", json={"steps": []})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.recompute_no_step"

    def test_an_unknown_step_is_refused(self, client, library):
        resp = client.post("/api/maintenance/recompute", json={"steps": ["page_counts", "abstracts"]})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.recompute_unknown_step"

    def test_search_index_without_an_embedding_role_is_refused(self, client, library, db):
        _add_paper(db)
        # conftest: embedding role unbound
        assert not Config.llm_ready("embedding")
        with patch.object(embedding_index, "embed_texts") as embed:
            resp = client.post("/api/maintenance/recompute", json={"steps": ["page_counts", "search_index"]})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "error.recompute_embedding_not_ready"
        embed.assert_not_called()

    def test_a_second_run_while_one_is_active_is_refused_in_the_stream(self, client, library, monkeypatch):
        monkeypatch.setitem(maintenance._RUN, "active", True)
        resp = client.post("/api/maintenance/recompute", json={"steps": ["page_counts"]})
        assert _events(resp) == [{"type": "error", "message": "error.recompute_running"}]

    @pytest.mark.parametrize("url", [
        "/api/maintenance/full-refresh",
        "/api/maintenance/rebuild-links",
        "/api/maintenance/update-page-counts",
    ])
    def test_the_old_endpoints_are_gone(self, client, url):
        assert client.post(url).status_code in (404, 405)


class TestStream:
    def test_progress_and_done_in_the_format_of_the_former_full_refresh(self, client, library, db):
        _add_paper(db, "a.pdf")
        with patch("fitz.open", return_value=_fitz_doc(3)):
            resp = client.post("/api/maintenance/recompute", json={"steps": ["page_counts"]})
        assert "text/event-stream" in resp.headers["content-type"]
        events = _events(resp)
        progress = [e for e in events if e["type"] == "progress"]
        assert progress
        assert {"type", "message", "step", "index", "steps", "current", "total", "percent", "stats"} <= progress[0].keys()
        assert progress[0]["message"] == "maintenance.recompute.step.page_counts"
        assert progress[-1]["percent"] == 100
        done = events[-1]
        assert done == {
            "type": "done", "steps": ["page_counts"], "total": 1, "errors": 0,
            "stats": {"page_counts": {"updated": 1, "errors": 0}},
        }

    def test_steps_run_in_their_fixed_order(self, client, library, db):
        _add_paper(db, "a.pdf")
        oa, _ = _openalex()
        with oa, patch("fitz.open", return_value=_fitz_doc(3)):
            events = _run(client, ["category_folders", "page_counts", "citations"])
        order = []
        for e in events:
            if e["type"] == "progress" and e["step"] not in order:
                order.append(e["step"])
        assert order == ["page_counts", "citations", "category_folders"]
        assert events[-1]["steps"] == order
        assert [e["index"] for e in events if e["type"] == "progress" and e["current"] == 0] == [1, 2, 3]

    def test_an_empty_library_finishes_every_step(self, client, library, db):
        oa, _ = _openalex()
        with oa:
            events = _run(client, ["page_counts", "citations", "passages", "category_folders"])
        assert events[-1]["total"] == 0
        assert events[-1]["errors"] == 0
        assert {e["step"] for e in events if e["type"] == "progress"} == {
            "page_counts", "citations", "passages", "category_folders"}


# ---------------------------------------------------------------------------
# The five steps
# ---------------------------------------------------------------------------

class TestPageCounts:
    def test_page_counts_are_read_from_the_pdfs(self, client, library, db):
        pid = _add_paper(db, "a.pdf")
        with patch("fitz.open", return_value=_fitz_doc(12)) as opened:
            events = _run(client, ["page_counts"])
        assert _row(db, pid)["page_count"] == 12
        assert opened.call_args[0][0] == os.path.join(Config.ALL_DIR, "a.pdf")
        assert events[-1]["stats"]["page_counts"] == {"updated": 1, "errors": 0}

    def test_an_item_without_a_pdf_is_skipped_and_an_unreadable_one_counted(self, client, library, db):
        _add_paper(db, "", pdf=False, title="No PDF")
        _add_paper(db, "gone.pdf", pdf=False)          # file missing: the Diagnosis' finding
        broken = _add_paper(db, "broken.pdf")
        with patch("fitz.open", side_effect=RuntimeError("not a PDF")) as opened:
            events = _run(client, ["page_counts"])
        assert opened.call_count == 1
        assert _row(db, broken)["page_count"] == 0
        assert events[-1]["stats"]["page_counts"] == {"updated": 0, "errors": 1}


class TestCitations:
    def test_citation_counts_come_from_openalex(self, client, library, db):
        pid = _add_paper(db, "a.pdf", doi="10.1000/ABC")
        other = _add_paper(db, "b.pdf", doi="https://doi.org/10.2/X")
        _add_paper(db, "c.pdf", doi="", title="No DOI")
        oa, oa_client = _openalex([_work("10.1000/abc", "W11", 42), _work("10.2/x", "W22", 3)])
        with oa:
            events = _run(client, ["citations"])
        asked = oa_client.fetch_works_by_doi.call_args
        assert sorted(asked[0][0]) == ["10.1000/ABC", "https://doi.org/10.2/X"]
        assert asked[1] == {"raise_errors": True}
        assert (_row(db, pid)["openalex_id"], _row(db, pid)["cited_by_count"]) == ("W11", 42)
        assert _row(db, pid)["openalex_updated_at"]
        assert _row(db, other)["cited_by_count"] == 3
        assert events[-1]["stats"]["citations"] == {"updated": 2, "errors": 0}

    def test_a_failed_request_counts_its_items_as_errors(self, client, library, db):
        pid = _add_paper(db, "a.pdf")
        oa, _ = _openalex(error=OSError("offline"))
        with oa:
            events = _run(client, ["citations"])
        assert events[-1]["stats"]["citations"] == {"updated": 0, "errors": 1}
        assert _row(db, pid)["cited_by_count"] == 0


class TestPassages:
    def test_passages_are_cut_for_items_without_any(self, client, library, db):
        fresh = _add_paper(db, "fresh.pdf")
        chunked = _add_paper(db, "chunked.pdf")
        _add_paper(db, "", pdf=False, title="No PDF")
        conn = db._connect()
        conn.execute("INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                     "VALUES (?, 0, 1, 1, 'kept')", (chunked,))
        conn.commit()
        conn.close()
        text = "A sentence about concrete. " * 120
        with patch.object(pdf_chunking, "_extract_pages_text", return_value=[(1, text)]) as extract:
            events = _run(client, ["passages"])
        assert extract.call_count == 1                   # only the item without passages
        created = _count(db, "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", fresh)
        assert created > 1
        assert _count(db, "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", chunked) == 1
        assert events[-1]["stats"]["passages"] == {"updated": created, "errors": 0}


class TestSearchIndex:
    def test_items_and_passages_get_vectors_of_the_bound_model(self, client, library, db, monkeypatch):
        configure_llm(monkeypatch, embedding="embed-new")
        pid = _add_paper(db, "a.pdf")
        conn = db._connect()
        conn.execute("INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                     "VALUES (?, 0, 1, 1, 'one passage')", (pid,))
        conn.commit()
        conn.close()
        with patch.object(embedding_index, "embed_texts", side_effect=lambda texts, mode: [[0.1, 0.2]] * len(texts)):
            events = _run(client, ["search_index"])
        assert _count(db, "SELECT COUNT(*) FROM paper_embeddings WHERE model = 'embed-new'") == 1
        assert _count(db, "SELECT COUNT(*) FROM chunk_embeddings WHERE model = 'embed-new'") == 1
        assert events[-1]["stats"]["search_index"] == {"updated": 2, "errors": 0, "items": 1, "passages": 1}

    def test_a_clean_run_drops_the_vectors_of_the_old_model(self, client, library, db, monkeypatch):
        pid = _add_paper(db, "a.pdf")
        configure_llm(monkeypatch, embedding="embed-old")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.5, 0.5]]):
            embedding_index.embed_paper_to_db(pid)
        configure_llm(monkeypatch, embedding="embed-new")
        assert client.get("/api/maintenance/embeddings/status").json()["stale"] is True
        with patch.object(embedding_index, "embed_texts", side_effect=lambda texts, mode: [[0.1, 0.2]] * len(texts)):
            _run(client, ["search_index"])
        status = client.get("/api/maintenance/embeddings/status").json()
        assert status["stale"] is False
        assert (status["indexed"], status["total"]) == (1, 1)

    def test_a_failed_embedding_is_counted_and_keeps_the_old_vectors(self, client, library, db, monkeypatch):
        pid = _add_paper(db, "a.pdf")
        configure_llm(monkeypatch, embedding="embed-old")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.5, 0.5]]):
            embedding_index.embed_paper_to_db(pid)
        configure_llm(monkeypatch, embedding="embed-new")
        with patch.object(embedding_index, "embed_texts", side_effect=RuntimeError("gateway down")):
            events = _run(client, ["search_index"])
        assert events[-1]["stats"]["search_index"]["errors"] == 1
        assert events[-1]["errors"] == 1
        assert client.get("/api/maintenance/embeddings/status").json()["stale"] is True


class TestCategoryFolders:
    def test_entries_are_placed_in_the_current_link_mode(self, client, library, db, monkeypatch):
        pid = _add_paper(db, "a.pdf")
        parent = _category(db, "Materials")
        _category(db, "Concrete", pid, parent_id=parent)
        _add_paper(db, "uncategorised.pdf")
        monkeypatch.setattr(Config, "LINK_MODE", "hardlink")
        events = _run(client, ["category_folders"])
        entry = Path(Config.CATEGORIES_DIR) / "Materials" / "Concrete" / "a.pdf"
        assert entry.is_file() and not entry.is_symlink()
        assert os.stat(entry).st_nlink == 2                 # a hard link to the PDF
        assert events[-1]["stats"]["category_folders"] == {"updated": 1, "errors": 0, "fallbacks": 0}

    def test_switching_the_mode_leaves_nothing_of_the_old_one_behind(self, client, library, db, monkeypatch):
        pid = _add_paper(db, "a.pdf")
        _category(db, "Concrete", pid)
        cat = Path(Config.CATEGORIES_DIR)
        # What earlier runs left: a hard link, a shortcut of the same item, a
        # folder of a category that no longer exists.
        (cat / "Concrete").mkdir()
        os.link(Path(Config.ALL_DIR) / "a.pdf", cat / "Concrete" / "a.pdf")
        (cat / "Concrete" / "a.pdf.lnk").write_bytes(b"old shortcut")
        (cat / "Gone").mkdir()
        (cat / "Gone" / "a.pdf").write_bytes(b"old copy")
        monkeypatch.setattr(Config, "LINK_MODE", "copy")
        _run(client, ["category_folders"])
        assert sorted(p.name for p in cat.rglob("*")) == ["Concrete", "a.pdf"]
        entry = cat / "Concrete" / "a.pdf"
        assert os.stat(entry).st_nlink == 1                 # a copy, no longer the hard link
        assert entry.read_bytes() == (Path(Config.ALL_DIR) / "a.pdf").read_bytes()

    def test_the_pdfs_themselves_are_never_touched(self, client, library, db, monkeypatch):
        pid = _add_paper(db, "a.pdf")
        _category(db, "Concrete", pid)
        pdf = Path(Config.ALL_DIR) / "a.pdf"
        before = pdf.read_bytes()
        monkeypatch.setattr(Config, "LINK_MODE", "hardlink")
        _run(client, ["category_folders"])
        monkeypatch.setattr(Config, "LINK_MODE", "copy")
        _run(client, ["category_folders"])
        assert pdf.read_bytes() == before
        assert os.stat(pdf).st_nlink == 1                   # the old hard link is gone, the PDF stays
        assert sorted(p.name for p in Path(Config.ALL_DIR).iterdir()) == ["a.pdf"]

    def test_a_failed_hard_link_falls_back_and_is_counted(self, client, library, db, monkeypatch):
        import filenames

        pid = _add_paper(db, "a.pdf")
        _category(db, "Concrete", pid)
        monkeypatch.setattr(Config, "LINK_MODE", "hardlink")

        def cross_device(src, dst):
            raise OSError(18, "Invalid cross-device link")

        def shortcut(source, link_path):
            Path(link_path + ".lnk").write_text(source)
            return "shortcut"

        monkeypatch.setattr(filenames.os, "link", cross_device)
        monkeypatch.setattr(filenames, "_place_shortcut", shortcut)
        events = _run(client, ["category_folders"])
        assert (Path(Config.CATEGORIES_DIR) / "Concrete" / "a.pdf.lnk").is_file()
        assert events[-1]["stats"]["category_folders"] == {"updated": 1, "errors": 0, "fallbacks": 1}


# ---------------------------------------------------------------------------
# The pure half (services/recompute.py)
# ---------------------------------------------------------------------------

class TestService:
    def test_steps_are_ordered_and_deduplicated(self):
        from services import recompute

        assert recompute.normalize_steps(["category_folders", "page_counts", "page_counts"]) == [
            "page_counts", "category_folders"]

    @pytest.mark.parametrize("steps, code", [
        ([], "error.recompute_no_step"),
        (None, "error.recompute_no_step"),
        (["page_counts", "abstracts"], "error.recompute_unknown_step"),
    ])
    def test_refusals_carry_a_code(self, steps, code):
        from services import recompute

        with pytest.raises(recompute.StepError) as err:
            recompute.normalize_steps(steps)
        assert err.value.code == code

    def test_works_match_items_by_doi_whatever_its_spelling(self):
        from services import recompute

        papers = [{"id": 1, "doi": "10.1/ABC"}, {"id": 2, "doi": "https://doi.org/10.1/abc"},
                  {"id": 3, "doi": "doi:10.2/x."}, {"id": 4, "doi": ""}]
        works = [_work("10.1/abc"), _work("10.2/x"), _work("10.9/none")]
        assert [(pid, w.doi) for pid, w in recompute.match_works(papers, works)] == [
            (1, "10.1/abc"), (2, "10.1/abc"), (3, "10.2/x")]

    def test_an_empty_step_is_complete(self):
        from services import recompute

        assert recompute.percent(0, 0) == 100
        assert recompute.percent(1, 3) == 33


# ---------------------------------------------------------------------------
# Only derived data
# ---------------------------------------------------------------------------

def test_no_curated_field_is_touched(client, library, db, monkeypatch):
    configure_llm(monkeypatch, embedding="embed-new")
    pid = _add_paper(db, "a.pdf", doi="10.1000/abc", abstract="My abstract.", journal="J",
                     notes="my notes", tags="t1, t2")
    _category(db, "Concrete", pid)
    before = _row(db, pid)
    cats_before = _count(db, "SELECT COUNT(*) FROM paper_categories WHERE paper_id = ?", pid)
    oa, _ = _openalex([_work("10.1000/abc", "W9", 5)])
    text = "A sentence about concrete. " * 120
    with oa, patch("fitz.open", return_value=_fitz_doc(9)), \
            patch.object(pdf_chunking, "_extract_pages_text", return_value=[(1, text)]), \
            patch.object(embedding_index, "embed_texts", side_effect=lambda texts, mode: [[0.1, 0.2]] * len(texts)):
        events = _run(client, ALL_STEPS)
    assert events[-1]["errors"] == 0
    after = _row(db, pid)
    changed = {k for k in before if before[k] != after[k]}
    assert changed <= DERIVED_COLUMNS, f"curated columns written: {changed - DERIVED_COLUMNS}"
    assert after["page_count"] == 9 and after["cited_by_count"] == 5
    assert _count(db, "SELECT COUNT(*) FROM paper_categories WHERE paper_id = ?", pid) == cats_before
