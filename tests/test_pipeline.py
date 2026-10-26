"""Unit tests for the ``pipeline.process_paper`` pipeline (literature_manager split, #94).

All slow/IO/network boundaries (text extraction, OCR/unlock, DOI discovery,
CrossRef, LLM categorisation, symlink creation) are monkeypatched. A real
temp-file ``Database`` and real temp PDF files are used so the DB-write and
file-copy/cleanup behaviour is exercised for real.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import import_indexing
import paper_ingest
import pipeline
from config import Config
from database import Database


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "lib"
    input_dir = base / "input"
    all_dir = base / "all"
    input_dir.mkdir(parents=True)
    all_dir.mkdir(parents=True)

    monkeypatch.setattr(Config, "ALL_DIR", str(all_dir))
    monkeypatch.setattr(Config, "INPUT_DIR", str(input_dir))
    monkeypatch.setattr(Config, "UNLOCK_PDFS", False)

    db = Database(str(base / "test.db"))

    # Neutralise the heavy/IO/network boundaries by default.
    monkeypatch.setattr(pipeline, "compute_file_hash", lambda fp: "fixed-hash")
    monkeypatch.setattr(pipeline, "extract_text_from_pdf", lambda fp, mp: "Some PDF body text")
    monkeypatch.setattr(pipeline, "discover_doi", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "fetch_crossref_metadata", lambda doi: None)
    monkeypatch.setattr(pipeline, "extract_isbn_from_text", lambda t: None)
    monkeypatch.setattr(pipeline, "extract_year_from_text", lambda t: None)
    monkeypatch.setattr(pipeline, "categorize_with_llm", lambda **k: [])
    monkeypatch.setattr(pipeline, "create_symlinks", lambda *a, **k: None)
    # The post-import index hook (#153) writes to Config.DB_PATH, not to this
    # test's own throwaway Database — neutralise it by default; the tests that
    # care about it override this again.
    monkeypatch.setattr(
        import_indexing, "index_paper_after_import",
        lambda paper_id, pdf_path: {"chunks": 0, "paper_embedded": False, "chunk_vectors": 0},
    )

    return db, input_dir, all_dir, monkeypatch


def _make_pdf(input_dir: Path, name: str = "paper.pdf") -> str:
    fp = input_dir / name
    fp.write_bytes(b"%PDF-1.4 fake")
    return str(fp)


def _clear_pdf(db: Database, all_dir: Path, paper_id: int) -> None:
    """The state Diagnosis ``clear_pdf`` leaves: the file in all/ is gone, the
    row keeps its hash but its ``filename`` is empty."""
    row = next(p for p in db.get_all_papers() if p["id"] == paper_id)
    (all_dir / row["filename"]).unlink(missing_ok=True)
    conn = db._connect()
    try:
        conn.execute("UPDATE papers SET filename = '' WHERE id = ?", (paper_id,))
        conn.commit()
    finally:
        conn.close()


def _wire_attach(db: Database, mp) -> list:
    """``paper_ingest`` writes through ``Config.DB_PATH`` (the one DB every
    caller of ``process_paper`` passes) — point it at this test's Database and
    neutralise its IO boundaries. Returns the recorded symlink calls."""
    mp.setattr(Config, "DB_PATH", db.db_path)
    links = []
    mp.setattr(paper_ingest, "create_symlinks",
               lambda _db, pid, name: links.append((pid, name)))
    mp.setattr(paper_ingest, "extract_text_from_pdf", lambda fp, mp_: "attached text")
    mp.setattr(paper_ingest, "index_paper_after_import",
               lambda pid, path: {"chunks": 0, "paper_embedded": False, "chunk_vectors": 0})
    return links


def _seed_item(db: Database, *, filename: str, doi: str = "", title: str = "Existing") -> int:
    return db.add_paper({
        "file_hash": f"seed-{doi or title}", "filename": filename,
        "original_filename": filename, "title": title, "authors": "", "year": 2020,
        "doi": doi, "isbn": "", "abstract": "", "journal": "", "publisher": "",
        "raw_metadata": "", "ocr_text": "",
    })


class TestProcessPaper:
    def test_happy_path_saves_and_copies(self, env):
        db, input_dir, all_dir, mp = env
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.1/abc")
        mp.setattr(pipeline, "fetch_crossref_metadata", lambda doi: {
            "title": "Resolved Title", "authors": "Doe, Jane", "year": 2022,
            "doi": doi, "abstract": "abs", "journal": "J", "publisher": "P", "isbn": "",
        })
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        assert pid is not None
        papers = db.get_all_papers()
        assert len(papers) == 1
        assert papers[0]["title"] == "Resolved Title"
        assert papers[0]["doi"] == "10.1/abc"
        # original removed from input, copy placed in all/
        assert not Path(path).exists()
        assert list(all_dir.glob("*.pdf"))

    def test_the_doi_record_fills_the_bibliographic_columns(self, env):
        db, input_dir, all_dir, mp = env
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.1/abc")
        mp.setattr(pipeline, "fetch_crossref_metadata", lambda doi: {
            "title": "Resolved Title", "authors": "Doe, Jane; Roe, Rob", "year": 2022, "doi": doi,
            "abstract": "", "journal": "J", "publisher": "P", "isbn": "",
            "item_type": "article", "volume": "12", "issue": "3", "pages": "45-67",
            "pub_date": "2022-05", "issn": "1234-5678",
        })
        pid = pipeline.process_paper(_make_pdf(input_dir), db)
        row = next(p for p in db.get_all_papers() if p["id"] == pid)
        assert (row["item_type"], row["volume"], row["issue"], row["pages"]) == ("article", "12", "3", "45-67")
        assert (row["pub_date"], row["issn"]) == ("2022-05", "1234-5678")

    def test_a_doi_crossref_does_not_know_is_asked_at_datacite(self, env):
        import datacite

        db, input_dir, all_dir, mp = env
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.48550/arXiv.2108.08090")
        mp.setattr(datacite, "fetch_datacite_metadata", lambda doi: {
            "title": "Graph Surrogates", "authors": "Okafor, Ada", "year": 2021,
            "item_type": "preprint", "eprint": "2108.08090"})
        pid = pipeline.process_paper(_make_pdf(input_dir), db)
        row = next(p for p in db.get_all_papers() if p["id"] == pid)
        assert (row["title"], row["item_type"], row["eprint"]) == ("Graph Surrogates", "preprint", "2108.08090")

    def test_assigns_llm_categories(self, env):
        db, input_dir, all_dir, mp = env
        cid = db.add_category("AI", None)
        mp.setattr(pipeline, "categorize_with_llm",
                   lambda **k: [{"category_id": cid, "confidence": 0.8}])
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        cats = db.get_paper_categories(pid)
        assert len(cats) == 1
        assert cats[0]["id"] == cid

    def test_hash_duplicate_skips_and_removes_input(self, env):
        db, input_dir, all_dir, mp = env
        # First import succeeds.
        first = _make_pdf(input_dir, "a.pdf")
        pipeline.process_paper(first, db)
        # Second file with same (fixed) hash is a duplicate.
        second = _make_pdf(input_dir, "b.pdf")

        result = pipeline.process_paper(second, db)

        assert result is None
        assert not Path(second).exists()  # duplicate input removed
        assert len(db.get_all_papers()) == 1

    def test_title_doi_duplicate_detected(self, env):
        db, input_dir, all_dir, mp = env
        # Seed an existing paper with a known DOI — and its file in all/, so
        # the input is a second copy, not the only one.
        _seed_item(db, filename="x.pdf", doi="10.9/dup")
        (all_dir / "x.pdf").write_bytes(b"%PDF-1.4 stored")
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.9/dup")
        path = _make_pdf(input_dir)

        result = pipeline.process_paper(path, db)

        assert result is None
        assert not Path(path).exists()
        assert len(db.get_all_papers()) == 1

    def test_fallback_title_from_first_text_line(self, env):
        db, input_dir, all_dir, mp = env
        mp.setattr(pipeline, "extract_text_from_pdf",
                   lambda fp, mp_: "My Inferred Title\nbody body body")
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        assert db.get_all_papers()[0]["title"] == "My Inferred Title"

    def test_year_and_isbn_fallbacks_applied(self, env):
        db, input_dir, all_dir, mp = env
        mp.setattr(pipeline, "extract_year_from_text", lambda t: 1999)
        mp.setattr(pipeline, "extract_isbn_from_text", lambda t: "9783161484100")
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)
        row = db.get_all_papers()[0]
        assert row["year"] == 1999
        assert row["isbn"] == "9783161484100"


class TestNoPdfIsLost:
    """A failure mid-import must never leave a DB row without its file — the
    next run would take the input PDF for a duplicate and delete the only copy."""

    def test_invented_category_id_does_not_abort_the_import(self, env):
        db, input_dir, all_dir, mp = env
        cid = db.add_category("AI", None)
        # Real categorize_with_llm, fake LLM answer with an invented id and junk.
        import llm_categorize

        class FakeLLM:
            def complete_json(self, *a, **k):
                return [{"category_id": 9999, "confidence": 0.9}, "junk",
                        {"category_id": str(cid), "confidence": 7}]

        mp.setattr(Config, "llm_ready", classmethod(lambda cls, tier="reasoning": True))
        mp.setattr(pipeline, "categorize_with_llm",
                   lambda **k: llm_categorize.categorize_with_llm(**k, llm=FakeLLM()))
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        assert pid is not None
        assert [c["id"] for c in db.get_paper_categories(pid)] == [cid]
        assert (all_dir / db.get_all_papers()[0]["filename"]).exists()
        assert not Path(path).exists()

    def test_failed_db_write_keeps_the_input_and_no_copy(self, env):
        db, input_dir, all_dir, mp = env

        def boom(data):
            raise RuntimeError("db locked")

        mp.setattr(db, "add_paper", boom)
        path = _make_pdf(input_dir)

        with pytest.raises(RuntimeError):
            pipeline.process_paper(path, db)

        assert Path(path).exists()
        assert not list(all_dir.glob("*.pdf"))

    def test_hash_duplicate_whose_copy_is_missing_is_restored(self, env):
        db, input_dir, all_dir, mp = env
        pid = pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        stored = all_dir / db.get_all_papers()[0]["filename"]
        stored.unlink()  # a row without its file, as an interrupted run left it
        again = _make_pdf(input_dir, "a.pdf")

        assert pipeline.process_paper(again, db) is None

        assert stored.exists()
        assert not Path(again).exists()
        assert len(db.get_all_papers()) == 1 and pid is not None

    def test_hash_duplicate_whose_name_is_taken_by_a_folder_keeps_the_input(self, env):
        """Drift (review 2026-10-18, C7): a folder sits in all/ under the name
        the row points to. It is not the stored PDF (the one rule,
        ``filenames.stored_pdf_path``, asks for a file), so the input is the
        only copy — and it cannot be restored under that name either. It
        stays; nothing is copied into the folder."""
        db, input_dir, all_dir, mp = env
        pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        stored = all_dir / db.get_all_papers()[0]["filename"]
        stored.unlink()
        stored.mkdir()
        again = _make_pdf(input_dir, "a.pdf")

        assert pipeline.process_paper(again, db) is None

        assert Path(again).exists(), "the only copy must not be deleted"
        assert stored.is_dir() and not any(stored.iterdir())
        assert len(db.get_all_papers()) == 1

    def test_a_failed_restore_keeps_the_input_and_leaves_no_partial_copy(self, env):
        """A copy that dies half-way must not leave a file under the row's name:
        the next run would take it for the stored PDF and delete the input."""
        db, input_dir, all_dir, mp = env
        pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        stored = all_dir / db.get_all_papers()[0]["filename"]
        stored.unlink()

        def torn_copy(src, dst):
            Path(dst).write_bytes(b"%PDF-1.4 fa")  # half of it
            raise OSError("disk full")

        mp.setattr(pipeline.shutil, "copy2", torn_copy)
        again = _make_pdf(input_dir, "a.pdf")

        assert pipeline.process_paper(again, db) is None

        assert Path(again).exists(), "the only copy must survive a failed restore"
        assert not stored.exists(), "a torn copy is no stored PDF"

    def test_hash_match_on_an_item_without_file_attaches_the_input(self, env):
        """Bug 4 (review 2026-10-18): the row kept the hash but has no file
        (Diagnosis clear_pdf). The input is the only copy — it is attached to
        that row (file in all/, symlinks), never deleted. The PDF did land in
        the library, so the Item's id comes back (C5): the callers report it
        as imported, not as "skipped"."""
        db, input_dir, all_dir, mp = env
        pid = pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        _clear_pdf(db, all_dir, pid)
        links = _wire_attach(db, mp)
        again = _make_pdf(input_dir, "again.pdf")

        assert pipeline.process_paper(again, db) == pid

        rows = db.get_all_papers()
        assert len(rows) == 1
        filename = rows[0]["filename"]
        assert filename, "the input must be attached to the file-less row"
        assert (all_dir / filename).read_bytes() == b"%PDF-1.4 fake"
        assert links == [(pid, filename)]
        assert not Path(again).exists()  # attached, so the input may go

    def test_failed_attach_keeps_the_input(self, env):
        db, input_dir, all_dir, mp = env
        pid = pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        _clear_pdf(db, all_dir, pid)
        _wire_attach(db, mp)

        def boom(paper_id, path, do_chunks=True):
            raise OSError("disk full")

        mp.setattr(paper_ingest, "attach_pdf_from_path", boom)
        again = _make_pdf(input_dir, "again.pdf")

        assert pipeline.process_paper(again, db) is None

        assert Path(again).exists(), "the only copy must survive a failed attach"
        assert db.get_all_papers()[0]["filename"] == ""

    def test_doi_match_on_an_item_without_file_keeps_the_input(self, env):
        """An Item from the DOI intake or the BibTeX import has no PDF: a
        DOI match against it makes the input the only copy of that PDF."""
        db, input_dir, all_dir, mp = env
        _seed_item(db, filename="", doi="10.9/nofile")
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.9/nofile")
        path = _make_pdf(input_dir)

        assert pipeline.process_paper(path, db) is None

        assert Path(path).exists(), "the only copy must not be deleted"
        rows = db.get_all_papers()
        assert len(rows) == 1 and rows[0]["filename"] == ""
        assert not list(all_dir.glob("*.pdf"))

    def test_title_match_on_an_item_whose_file_is_missing_keeps_the_input(self, env):
        db, input_dir, all_dir, mp = env
        title = "A Sufficiently Long Matching Title"
        _seed_item(db, filename="gone.pdf", title=title)  # row says gone.pdf, all/ has none
        mp.setattr(pipeline, "extract_text_from_pdf", lambda fp, mp_: f"{title}\nbody")
        path = _make_pdf(input_dir)

        assert pipeline.process_paper(path, db) is None

        assert Path(path).exists(), "the only copy must not be deleted"
        assert len(db.get_all_papers()) == 1
        assert not (all_dir / "gone.pdf").exists()

    @pytest.mark.parametrize("filename", ["", "gone.pdf"])
    def test_an_uploaded_copy_is_removed_on_a_match_without_stored_file(self, env, filename):
        """A browser upload is a copy — the user's original stays on their
        disk — so "never delete the only copy" does not hold for it: on a
        DOI/title match it goes like any duplicate, even when the Item has no
        PDF (review 2026-10-18, C1). The Item stays as it was."""
        db, input_dir, all_dir, mp = env
        _seed_item(db, filename=filename, doi="10.9/upload")
        mp.setattr(pipeline, "discover_doi", lambda *a, **k: "10.9/upload")
        path = _make_pdf(input_dir)

        assert pipeline.process_paper(path, db, upload=True) is None

        assert not Path(path).exists(), "the upload copy must not pile up in the input folder"
        rows = db.get_all_papers()
        assert len(rows) == 1 and rows[0]["filename"] == filename
        assert not list(all_dir.glob("*.pdf"))


class TestValidAssignments:
    def test_keeps_only_known_ids_once_and_clamps_confidence(self):
        from llm_categorize import valid_assignments

        cats = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        raw = [{"category_id": 1, "confidence": 1.5}, {"category_id": 1, "confidence": 0.2},
               {"category_id": "2"}, {"category_id": 3}, {"category_id": None}, [1], "x",
               {"category_id": 2, "confidence": "high"}]

        assert valid_assignments(raw, cats) == [
            {"category_id": 1, "confidence": 1.0},
            {"category_id": 2, "confidence": 0.0},
        ]


class TestPostImportIndexing:
    """#153: the classic path (CLI ``import``/``watch``, POST /api/import/process,
    POST /api/import/upload) all go through ``process_paper`` — so the index hook
    lives here, not in each caller."""

    def test_index_hook_runs_on_the_copied_pdf(self, env):
        db, input_dir, all_dir, mp = env
        calls = []
        mp.setattr(import_indexing, "index_paper_after_import",
                   lambda pid, path: calls.append((pid, path)) or
                   {"chunks": 5, "paper_embedded": True, "chunk_vectors": 5})
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        assert len(calls) == 1
        called_pid, called_path = calls[0]
        assert called_pid == pid
        # the copy in all/, not the (already deleted) input file
        assert Path(called_path).parent == all_dir
        assert Path(called_path).exists()

    def test_index_hook_does_not_run_for_a_duplicate(self, env):
        db, input_dir, all_dir, mp = env
        calls = []
        mp.setattr(import_indexing, "index_paper_after_import",
                   lambda pid, path: calls.append(pid) or
                   {"chunks": 0, "paper_embedded": False, "chunk_vectors": 0})
        pipeline.process_paper(_make_pdf(input_dir, "a.pdf"), db)
        pipeline.process_paper(_make_pdf(input_dir, "b.pdf"), db)  # same fixed hash

        assert len(calls) == 1

    def test_import_survives_a_broken_index_hook(self, env):
        """A dead embedding gateway (or a missing optional dependency) must never
        cost the user the import itself."""
        db, input_dir, all_dir, mp = env

        def boom(paper_id, pdf_path):
            raise RuntimeError("gateway down")

        mp.setattr(import_indexing, "index_paper_after_import", boom)
        path = _make_pdf(input_dir)

        pid = pipeline.process_paper(path, db)

        assert pid is not None
        assert len(db.get_all_papers()) == 1
        assert not Path(path).exists()  # input still cleaned up
