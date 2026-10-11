"""Ordering regressions of the smart import (LIB-B1, LIB-B2).

LIB-B2: file first, DB row second; a failed copy leaves no Item behind, and a
retry never deletes the only copy of a PDF.
LIB-B1: the category-folder entries are created after the categories are
assigned, not before.
"""
from __future__ import annotations

import importlib
import json
import os
from unittest.mock import MagicMock, patch

import services.smart_import_pipeline as smart_import_pipeline
from literature_manager import Config

import_router = importlib.import_module("routers.import")

PDF = b"%PDF-1.4 fake content for smart-import ordering test"
_SKIP_ALL = "?do_doi=false&do_categories=false&do_abstract=false&do_validate=false"


def _events(resp) -> list[dict]:
    return [json.loads(ln.strip()[6:]) for ln in resp.text.split("\n")
            if ln.strip().startswith("data: ")]


def _of_type(events, t):
    return next((e for e in events if e.get("type") == t), None)


def _plain(title: str):
    return (
        patch.object(smart_import_pipeline, "extract_text_from_pdf", return_value=title + "\nbody"),
        patch.object(smart_import_pipeline, "discover_doi", return_value=None),
        patch.object(import_router, "index_paper_after_import",
                     return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}),
    )


def _count_rows(db) -> int:
    conn = db._connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        conn.close()


def _put_in_input(name: str, data: bytes = PDF) -> str:
    path = os.path.join(Config.INPUT_DIR, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _post(client, name: str, query: str = _SKIP_ALL):
    return client.post(f"/api/import/process-smart/{name}{query}")


def test_failed_copy_leaves_no_item_and_keeps_the_input(client, db):
    src = _put_in_input("ordering_copyfail.pdf", PDF + b" copyfail")
    p1, p2, p3 = _plain("Ordering Copy Fail Title")
    with p1, p2, p3, patch.object(import_router.shutil, "copy2", side_effect=OSError("locked")):
        resp = _post(client, "ordering_copyfail.pdf")
    events = _events(resp)
    assert _of_type(events, "complete") is None
    assert _of_type(events, "error") is not None
    assert _count_rows(db) == 0
    assert os.path.exists(src)

    # Retry: not a "duplicate", imports fine, source is consumed only now.
    p1, p2, p3 = _plain("Ordering Copy Fail Title")
    with p1, p2, p3:
        resp = _post(client, "ordering_copyfail.pdf")
    complete = _of_type(_events(resp), "complete")
    assert complete is not None
    assert os.path.exists(os.path.join(Config.ALL_DIR, complete["filename"]))
    assert _count_rows(db) == 1


def test_failed_row_removes_the_copy_and_keeps_the_input(client, db):
    src = _put_in_input("ordering_rowfail.pdf", PDF + b" rowfail")
    before = set(os.listdir(Config.ALL_DIR))
    p1, p2, p3 = _plain("Ordering Row Fail Title")
    with p1, p2, p3, patch.object(type(db), "add_paper", side_effect=RuntimeError("db")):
        resp = _post(client, "ordering_rowfail.pdf")
    assert _of_type(_events(resp), "complete") is None
    assert set(os.listdir(Config.ALL_DIR)) == before
    assert os.path.exists(src)


def test_retry_restores_a_missing_copy_instead_of_deleting_the_only_pdf(client, db):
    data = PDF + b" restore"
    _put_in_input("ordering_restore.pdf", data)
    p1, p2, p3 = _plain("Ordering Restore Title")
    with p1, p2, p3:
        complete = _of_type(_events(_post(client, "ordering_restore.pdf")), "complete")
    stored = os.path.join(Config.ALL_DIR, complete["filename"])
    os.remove(stored)  # the state an interrupted earlier run left behind

    _put_in_input("ordering_restore_again.pdf", data)
    p1, p2, p3 = _plain("Ordering Restore Title")
    with p1, p2, p3:
        resp = _post(client, "ordering_restore_again.pdf")
    err = _of_type(_events(resp), "error")
    assert err is not None and err["message"] == "import.step.duplicateFile"
    assert os.path.exists(stored)
    assert not os.path.exists(os.path.join(Config.INPUT_DIR, "ordering_restore_again.pdf"))


def test_category_folder_entries_are_made_after_categories_are_assigned(client, db, monkeypatch):
    cat_id = db.add_category("Ordering Category")
    monkeypatch.setattr(Config, "llm_ready", lambda tier="reasoning": True)
    seen: list[list[int]] = []

    def spy(database, paper_id, filename, mode=None):
        seen.append([c["id"] for c in database.get_paper_categories(paper_id)])
        return {"placed": 0, "fallbacks": 0, "failed": 0}

    _put_in_input("ordering_cats.pdf", PDF + b" cats")
    p1, p2, p3 = _plain("Ordering Categories Title")
    fake_llm = MagicMock()
    fake_llm.last_error = None
    with p1, p2, p3, \
            patch.object(import_router, "llm_for", return_value=fake_llm), \
            patch.object(import_router, "categorize_with_llm",
                         return_value=[{"category_id": cat_id, "confidence": 0.9}]), \
            patch.object(import_router, "create_symlinks", side_effect=spy):
        resp = _post(client, "ordering_cats.pdf",
                     "?do_doi=false&do_categories=true&do_abstract=false&do_validate=false")
    complete = _of_type(_events(resp), "complete")
    assert complete is not None
    assert [c["id"] for c in complete["categories"]] == [cat_id]
    assert seen, "create_symlinks was never called"
    assert seen[-1] == [cat_id]


def test_a_hash_match_on_an_item_without_file_attaches_the_input(client, db):
    """LIB-B2 residual: the existing row kept the hash but has no file (e.g.
    Diagnosis clear_pdf). The inbox PDF is the only copy: it is attached to
    that row, never deleted."""
    data = PDF + b" attach to fileless"
    p1, p2, p3 = _plain("Ordering Fileless Title")
    _put_in_input("ordering_fileless.pdf", data)
    with p1, p2, p3:
        complete = _of_type(_events(_post(client, "ordering_fileless.pdf")), "complete")
    pid = complete["paper_id"] if "paper_id" in complete else complete["id"]
    os.remove(os.path.join(Config.ALL_DIR, complete["filename"]))
    conn = db._connect()
    try:
        conn.execute("UPDATE papers SET filename = '' WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()

    src = _put_in_input("ordering_fileless_again.pdf", data)
    with patch("import_indexing.index_paper_after_import",
               return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}), \
         patch("paper_ingest.index_paper_after_import",
               return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}):
        resp = _post(client, "ordering_fileless_again.pdf")
    err = _of_type(_events(resp), "error")
    assert err["message"] == "import.step.duplicateAttached"
    assert err["duplicate_paper_id"] == pid
    conn = db._connect()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    assert row["filename"], "the input must be attached to the file-less row"
    assert os.path.exists(os.path.join(Config.ALL_DIR, row["filename"]))
    assert not os.path.exists(src)
    assert _count_rows(db) == 1


def test_a_failed_attach_keeps_the_input(client, db):
    data = PDF + b" attach fails"
    p1, p2, p3 = _plain("Ordering Attach Fail Title")
    _put_in_input("ordering_attachfail.pdf", data)
    with p1, p2, p3:
        complete = _of_type(_events(_post(client, "ordering_attachfail.pdf")), "complete")
    os.remove(os.path.join(Config.ALL_DIR, complete["filename"]))
    conn = db._connect()
    try:
        conn.execute("UPDATE papers SET filename = ''")
        conn.commit()
    finally:
        conn.close()
    src = _put_in_input("ordering_attachfail_again.pdf", data)
    with patch("paper_ingest.attach_pdf_from_path", side_effect=OSError("disk full")):
        resp = _post(client, "ordering_attachfail_again.pdf")
    assert _of_type(_events(resp), "error")["message"] == "import.step.duplicateFile"
    assert os.path.exists(src), "the only copy must survive a failed attach"
