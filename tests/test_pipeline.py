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
        # Seed an existing paper with a known DOI.
        db.add_paper({
            "file_hash": "other-hash", "filename": "x.pdf", "original_filename": "x.pdf",
            "title": "Existing", "authors": "", "year": 2020, "doi": "10.9/dup",
            "isbn": "", "abstract": "", "journal": "", "publisher": "",
            "raw_metadata": "", "ocr_text": "",
        })
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
