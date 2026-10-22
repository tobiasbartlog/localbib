"""What the classic import routes report (review 2026-10-18, C1/C5).

``POST /api/import/upload``, ``/process`` and ``/process/{filename}`` go
through ``pipeline.process_paper``. When a hash match attaches the input to an
Item that had no file, the PDF did land in the library: the routes report the
Item as imported, not as "skipped: already in the database or failed". And a
browser upload is a copy (the user's original stays on their disk), so on a
DOI/title match with an Item without a stored PDF it goes like any duplicate
upload instead of waiting in the input folder.
"""
from __future__ import annotations

import hashlib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

import paper_ingest
import pipeline
from literature_manager import Config

PDF = b"%PDF-1.4 fake content for the classic report test"
DOI = "10.9/classic-report"
NO_INDEX = {"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """An input folder and an all/ of this test's own: ``/process`` runs
    through every PDF in the input folder, and nothing may leak out."""
    inbox, stored = tmp_path / "input", tmp_path / "all"
    inbox.mkdir()
    stored.mkdir()
    monkeypatch.setattr(Config, "INPUT_DIR", str(inbox))
    monkeypatch.setattr(Config, "ALL_DIR", str(stored))
    return inbox, stored


@pytest.fixture(autouse=True)
def offline():
    """The attach path and the pipeline, without text extraction, network,
    LLM or the index hooks."""
    with ExitStack() as stack:
        for target, name, value in (
            (paper_ingest, "unlock_pdf", None),
            (paper_ingest, "extract_text_from_pdf", "attached text"),
            (paper_ingest, "create_symlinks", None),
            (paper_ingest, "index_paper_after_import", NO_INDEX),
            (pipeline, "unlock_pdf", None),
            (pipeline, "extract_text_from_pdf", "Some Classic Report Heading\nbody"),
            (pipeline, "discover_doi", DOI),
            (pipeline, "fetch_crossref_metadata", None),
        ):
            stack.enter_context(patch.object(target, name, return_value=value))
        yield


def _seed(db, *, file_hash: str, filename: str = "", doi: str = "") -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, doi) "
            "VALUES (?, ?, 'seed.pdf', 'Seeded Classic Report Item', 'Doe, J.', ?)",
            (file_hash, filename, doi),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _filename(db, pid: int) -> str:
    conn = db._connect()
    try:
        return conn.execute("SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()[0]
    finally:
        conn.close()


def _fileless(db, data: bytes) -> int:
    """An Item that kept the hash of ``data`` but has no file (Diagnosis
    "Keep without PDF")."""
    return _seed(db, file_hash=hashlib.sha256(data).hexdigest())


def test_process_single_reports_an_attached_pdf_as_imported(client, db, folders):
    inbox, stored = folders
    data = PDF + b" attach single"
    pid = _fileless(db, data)
    (inbox / "attach_single.pdf").write_bytes(data)

    body = client.post("/api/import/process/attach_single.pdf").json()

    assert body == {"status": "ok", "paper_id": pid}
    assert (stored / _filename(db, pid)).read_bytes() == data
    assert not list(inbox.iterdir())


def test_process_all_reports_attached_as_ok_and_a_second_copy_as_skipped(client, db, folders):
    inbox, stored = folders
    data = PDF + b" attach batch"
    pid = _fileless(db, data)
    (inbox / "a_first.pdf").write_bytes(data)
    (inbox / "b_second.pdf").write_bytes(data)  # same bytes: only matches now

    body = client.post("/api/import/process").json()

    assert body["processed"] == 2
    assert body["results"][0] == {"filename": "a_first.pdf", "status": "ok", "paper_id": pid}
    assert body["results"][1]["filename"] == "b_second.pdf"
    assert body["results"][1]["status"] == "skipped"
    assert [p.name for p in stored.iterdir()] == [_filename(db, pid)]
    assert not list(inbox.iterdir())


@pytest.mark.parametrize("filename", ["", "classic_gone.pdf"])
def test_the_classic_upload_drops_its_copy_on_a_match_without_stored_pdf(client, db, folders,
                                                                          filename):
    inbox, stored = folders
    pid = _seed(db, file_hash="seed-classic", filename=filename, doi=DOI)

    body = client.post("/api/import/upload",
                       files={"file": ("upload_nopdf.pdf", PDF, "application/pdf")}).json()

    assert body == {"status": "skipped", "reason": body["reason"], "filename": "upload_nopdf.pdf"}
    assert not list(inbox.iterdir()), "an upload is a copy — it does not wait in the input folder"
    assert _filename(db, pid) == filename
    assert not list(stored.iterdir())


def test_process_single_keeps_an_inbox_file_on_a_match_without_stored_pdf(client, db, folders):
    """The same match on a file the user put in the input folder keeps it:
    it may be the only copy."""
    inbox, _stored = folders
    _seed(db, file_hash="seed-classic", doi=DOI)
    (inbox / "inbox_nopdf.pdf").write_bytes(PDF)

    body = client.post("/api/import/process/inbox_nopdf.pdf").json()

    assert body["status"] == "skipped"
    assert (inbox / "inbox_nopdf.pdf").read_bytes() == PDF
