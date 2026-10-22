"""The smart import never deletes the only copy of a PDF on a DOI/title match.

A DOI or title match names the same work, not the same file. Only when the
matched Item holds its PDF in all/ is the inbox file a second copy that may
go. An Item without a PDF (DOI intake, BibTeX import, Diagnosis "Keep without
PDF") or whose file in all/ is gone leaves the inbox file the only copy: it
stays in the input folder, and the stream says how to attach it by hand.
Review 2026-10-18 — the same bug class as the CLI import
(``pipeline.process_paper``).

A browser upload is different: it is always a copy, the user's original stays
on their disk. It gets an input-folder name of its own (never a file another
upload already holds, which that upload's import would remove under it) and
goes on such a match like any duplicate upload (review 2026-10-18, C1).
"""
from __future__ import annotations

import importlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

import services.smart_import_pipeline as smart_import_pipeline
import ui_catalog
from literature_manager import Config

import_router = importlib.import_module("routers.import")

PDF = b"%PDF-1.4 fake content for the only-copy test"
TITLE = "A Sufficiently Long Matching Title For Dedup"
OTHER_TITLE = "An Entirely Different Heading On Page One"
DOI = "10.9/only-copy"
NEW_KEYS = ("import.step.duplicateItemNoPdf", "import.step.duplicateItemPdfMissing")


@pytest.fixture
def files():
    """Creates files in the input folder or in all/ and removes what is left
    of them afterwards — a kept inbox file must not leak into other tests."""
    made: list[Path] = []

    def _make(folder: str, name: str, data: bytes = PDF) -> Path:
        path = Path(folder) / name
        path.write_bytes(data)
        made.append(path)
        return path

    _make.track = made.append
    yield _make
    for path in made:
        path.unlink(missing_ok=True)


def _events(resp) -> list[dict]:
    return [json.loads(ln.strip()[6:]) for ln in resp.text.split("\n")
            if ln.strip().startswith("data: ")]


def _error(resp) -> dict:
    events = _events(resp)
    assert not any(e["type"] == "complete" for e in events), events
    return next(e for e in events if e["type"] == "error")


def _seed(db, *, filename: str, doi: str = "", title: str = TITLE) -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, doi) "
            "VALUES (?, ?, ?, ?, 'Doe, J.', ?)",
            (f"seed-{doi or title}", filename, filename or "seed.pdf", title, doi),
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


def _count(db) -> int:
    conn = db._connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        conn.close()


def _run(text: str, doi: str | None, send):
    """One smart import with the PDF text and DOI given, offline."""
    query = (f"?do_doi={'true' if doi else 'false'}"
             "&do_categories=false&do_abstract=false&do_validate=false")
    with ExitStack() as stack:
        stack.enter_context(patch.object(smart_import_pipeline, "extract_text_from_pdf",
                                         return_value=text))
        stack.enter_context(patch.object(smart_import_pipeline, "discover_doi", return_value=doi))
        stack.enter_context(patch.object(smart_import_pipeline, "fetch_crossref_metadata",
                                         return_value=None))
        stack.enter_context(patch.object(import_router, "create_symlinks", return_value=None))
        stack.enter_context(patch.object(
            import_router, "index_paper_after_import",
            return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}))
        return send(query)


def _process(client, name: str, *, text: str, doi: str | None = None):
    return _run(text, doi, lambda q: client.post(f"/api/import/process-smart/{name}{q}"))


def _upload(client, name: str, *, text: str, doi: str | None = None,
            data: bytes = PDF + b" upload"):
    return _run(text, doi, lambda q: client.post(
        f"/api/import/upload-smart{q}", files={"file": (name, data, "application/pdf")}))


def _inbox_slot(files, name: str, counters: int = 2) -> Path:
    """The input-folder path an upload named ``name`` takes, emptied first;
    it and its counter variants (``_1`` …) are removed after the test."""
    path = Path(Config.INPUT_DIR) / name
    for n in range(counters + 1):
        variant = path if n == 0 else path.with_name(f"{path.stem}_{n}{path.suffix}")
        variant.unlink(missing_ok=True)
        files.track(variant)
    return path


def _inbox_copies(data: bytes) -> list[Path]:
    return sorted(p for p in Path(Config.INPUT_DIR).glob("*.pdf") if p.read_bytes() == data)


def test_doi_match_on_an_item_without_pdf_keeps_the_inbox_file(client, db, files):
    pid = _seed(db, filename="", doi=DOI, title="Seeded Under Another Long Title")
    src = files(Config.INPUT_DIR, "onlycopy_doi.pdf")

    err = _error(_process(client, src.name, text=OTHER_TITLE + "\nbody", doi=DOI))

    assert err["message"] == "import.step.duplicateItemNoPdf"
    assert err["duplicate_paper_id"] == pid
    assert err["kept"] is True
    assert src.exists(), "the only copy must not be deleted"
    assert _count(db) == 1
    assert _filename(db, pid) == "", "a DOI match is not attached automatically"


def test_title_match_on_an_item_without_pdf_removes_the_upload(client, db, files):
    pid = _seed(db, filename="   ")  # blank counts as no file, like ""
    data = PDF + b" title upload"
    slot = _inbox_slot(files, "onlycopy_upload.pdf")

    err = _error(_upload(client, slot.name, text=TITLE + "\nbody", data=data))

    assert err["message"] == "import.step.duplicateItemNoPdf"
    assert err["duplicate_paper_id"] == pid
    assert err["kept"] is False
    assert _inbox_copies(data) == [], "an upload is a copy — it is not kept"
    assert _count(db) == 1
    assert _filename(db, pid) == "   ", "a title match is not attached automatically"


@pytest.mark.parametrize("stored, code", [
    ("", "import.step.duplicateItemNoPdf"),
    ("onlycopy_gone_up.pdf", "import.step.duplicateItemPdfMissing"),
])
def test_doi_match_on_an_item_without_stored_pdf_removes_the_upload(client, db, files,
                                                                    stored, code):
    pid = _seed(db, filename=stored, doi=DOI, title="Seeded Under Another Long Title")
    data = PDF + b" doi upload " + code.encode()
    _inbox_slot(files, "onlycopy_doi_up.pdf")

    err = _error(_upload(client, "onlycopy_doi_up.pdf", text=OTHER_TITLE + "\nbody",
                         doi=DOI, data=data))

    assert err["message"] == code
    assert err["duplicate_paper_id"] == pid
    assert err["kept"] is False
    assert _inbox_copies(data) == []
    assert _filename(db, pid) == stored


def test_title_match_on_an_item_whose_file_is_gone_keeps_the_inbox_file(client, db, files):
    pid = _seed(db, filename="onlycopy_gone.pdf")  # the row names a file all/ does not hold
    src = files(Config.INPUT_DIR, "onlycopy_gone_in.pdf")

    err = _error(_process(client, src.name, text=TITLE + "\nbody"))

    assert err["message"] == "import.step.duplicateItemPdfMissing"
    assert err["duplicate_paper_id"] == pid
    assert err["kept"] is True
    assert src.exists(), "the only copy must not be deleted"
    assert not (Path(Config.ALL_DIR) / "onlycopy_gone.pdf").exists()
    assert _filename(db, pid) == "onlycopy_gone.pdf"


@pytest.mark.parametrize("via", ["doi", "title"])
def test_a_match_on_an_item_with_its_file_still_removes_the_duplicate(client, db, files, via):
    stored = files(Config.ALL_DIR, "onlycopy_here.pdf", b"%PDF-1.4 the stored copy")
    pid = _seed(db, filename=stored.name, doi=DOI if via == "doi" else "",
                title=TITLE if via == "title" else "Seeded Under Another Long Title")
    src = files(Config.INPUT_DIR, f"onlycopy_dup_{via}.pdf")

    err = _error(_process(client, src.name,
                          text=(TITLE if via == "title" else OTHER_TITLE) + "\nbody",
                          doi=DOI if via == "doi" else None))

    assert err["message"] == "import.step.duplicateItem"
    assert err["duplicate_paper_id"] == pid
    assert "kept" not in err
    assert not src.exists(), "a second copy of a stored PDF is removed"
    assert stored.read_bytes() == b"%PDF-1.4 the stored copy"


def test_uploading_the_same_file_again_adds_no_copy(client, db, files):
    """Each upload of a PDF-less Item's file goes again: nothing piles up in
    the input folder for "Import all" to run through again and again."""
    pid = _seed(db, filename="")
    data = PDF + b" uploaded again and again"
    _inbox_slot(files, "onlycopy_pile.pdf")

    for _ in range(3):
        err = _error(_upload(client, "onlycopy_pile.pdf", text=TITLE + "\nbody", data=data))
        assert err["message"] == "import.step.duplicateItemNoPdf"
        assert err["duplicate_paper_id"] == pid
        assert err["kept"] is False

    assert _inbox_copies(data) == []


def test_two_uploads_of_the_same_file_get_their_own_paths(files):
    """Review 2026-10-18 (C1): reusing a byte-identical input made two uploads
    of one file — one batch, or a retry while the first import still ran —
    share ONE path; the first import removed it and the second failed with
    FileNotFoundError. Every upload is its own copy under its own name."""
    data = PDF + b" twice in one batch"
    slot = _inbox_slot(files, "onlycopy_twice.pdf")

    first, first_name = import_router._save_upload(slot.name, data)
    second, second_name = import_router._save_upload(slot.name, data)

    assert (first_name, second_name) == ("onlycopy_twice.pdf", "onlycopy_twice_1.pdf")
    assert first != second
    os.remove(first)  # what the first import does with its own copy ...
    assert Path(second).read_bytes() == data  # ... leaves the second one alone


def test_a_taken_name_gets_a_counter_and_is_never_overwritten(client, db, files):
    taken = _inbox_slot(files, "onlycopy_taken.pdf")
    taken.write_bytes(b"%PDF-1.4 someone else's file")
    data = PDF + b" classic upload"

    with patch.object(import_router, "process_paper", return_value=None):
        names = [client.post("/api/import/upload",
                             files={"file": (taken.name, data, "application/pdf")}
                             ).json()["filename"] for _ in range(2)]

    assert names == ["onlycopy_taken_1.pdf", "onlycopy_taken_2.pdf"]
    assert taken.read_bytes() == b"%PDF-1.4 someone else's file"
    assert _inbox_copies(data) == [taken.parent / name for name in names]


def test_a_kept_file_still_lands_the_item_in_the_target_collection(client, db, files):
    pid = _seed(db, filename="")
    cid = client.post("/api/collections", json={"name": "Paper X"}).json()["id"]
    src = files(Config.INPUT_DIR, "onlycopy_collection.pdf")

    err = _error(_run(TITLE + "\nbody", None, lambda q: client.post(
        f"/api/import/process-smart/{src.name}{q}&collection_id={cid}")))

    assert err["message"] == "import.step.duplicateItemNoPdf"
    assert err["collection"] == {"id": cid, "name": "Paper X", "added": True}
    assert src.exists()
    assert [p["id"] for p in client.get(f"/api/papers?collection_id={cid}").json()] == [pid]


@pytest.mark.parametrize("lang, kept_claim", [("en", "input folder"), ("de", "Input-Ordner")])
def test_the_new_messages_resolve_in_both_catalogs(lang, kept_claim):
    """One text for both outcomes: a kept inbox file and a removed upload.
    So it never says where the file went (C1)."""
    root = Path(__file__).resolve().parent.parent / "static" / "locales"
    strings = ui_catalog.Catalog(str(root), "import.step.").strings(lang)
    for key in NEW_KEYS:
        assert strings.get(key), f"core.{lang}.js lacks {key}"
        assert kept_claim not in strings[key], f"{key} still claims the file was kept"
