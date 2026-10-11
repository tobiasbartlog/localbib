"""Add-on Contract 2.3 (#226): ``LibraryApi.get_reference`` carries the Item
id, ``has_pdf`` and ``volume``/``issue``/``pages``. The core's host service and
the fake an Add-on tests against answer the same keys."""
from __future__ import annotations

import json
import os

import host_services
from literature_manager import Config
from plugin_api.testing import InMemoryLibrary

REFERENCE_KEYS = {"citekey", "id", "title", "authors", "year", "journal", "doi",
                  "abstract", "has_pdf", "volume", "issue", "pages"}


def _insert(db, title, *, filename="", raw=None) -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, "
            "cite_key, raw_metadata) VALUES (?, ?, ?, ?, 'Doe, J.', 2020, ?, ?)",
            (f"h-{title}", filename, title, title, title.lower().replace(" ", ""),
             json.dumps(raw) if raw is not None else ""),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_the_core_reports_pdf_and_bibliographic_details(client, db, monkeypatch, tmp_path):
    # Its own library folder: the suite-wide all/ must not keep a stray PDF
    # (other tests count the files there).
    monkeypatch.setattr(Config, "ALL_DIR", str(tmp_path))
    (tmp_path / "with.pdf").write_bytes(b"%PDF-1.4")
    with_pdf = _insert(db, "With Pdf", filename="with.pdf",
                       raw={"volume": "12", "number": "3", "pages": "45--67"})
    _insert(db, "Without Pdf", filename="")
    _insert(db, "Gone Pdf", filename="gone.pdf", raw={"page": "9"})

    lib = host_services._CoreLibraryApi()
    ref = lib.get_reference("withpdf")
    assert set(ref) == REFERENCE_KEYS
    assert ref["id"] == with_pdf and ref["has_pdf"] is True
    assert (ref["volume"], ref["issue"], ref["pages"]) == ("12", "3", "45--67")

    bare = lib.get_reference("withoutpdf")
    assert bare["has_pdf"] is False
    assert (bare["volume"], bare["issue"], bare["pages"]) == (None, None, None)
    # A filename whose file is missing is no PDF in the library.
    gone = lib.get_reference("gonepdf")
    assert gone["has_pdf"] is False and gone["pages"] == "9"


def test_full_text_is_read_only_from_a_stored_pdf(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ALL_DIR", str(tmp_path))
    (tmp_path / "with.pdf").write_bytes(b"%PDF-1.4")
    _insert(db, "With Pdf", filename="with.pdf")
    _insert(db, "Without Pdf", filename="")
    _insert(db, "Gone Pdf", filename="gone.pdf")
    read = []
    monkeypatch.setattr(host_services, "extract_text_from_pdf",
                        lambda path, max_pages: read.append(os.path.basename(path)) or "Volltext")
    lib = host_services._CoreLibraryApi()
    assert lib.get_full_text("withpdf") == "Volltext"
    assert lib.get_full_text("withoutpdf") is None
    assert lib.get_full_text("gonepdf") is None
    assert read == ["with.pdf"]


def test_a_lookup_by_cite_key_never_loads_the_whole_library(client, db, monkeypatch):
    """A literature check asks once per cite key (#226). Each answer must be a
    targeted query — not ``get_all_papers()`` (every column incl. the OCR
    text of every Item) per key."""
    _insert(db, "With Pdf", raw={"volume": "7"})
    _insert(db, "Other")

    def full_load():
        raise AssertionError("get_reference loaded the whole library")

    monkeypatch.setattr(host_services.db, "get_all_papers", full_load)
    lib = host_services._CoreLibraryApi()
    ref = lib.get_reference("withpdf")
    assert ref["title"] == "With Pdf" and ref["volume"] == "7"
    assert lib.get_reference("other")["title"] == "Other"
    assert lib.get_reference("fehlt2020") is None
    assert lib.get_reference("") is None
    assert lib.get_abstract("other") is None


def test_the_lookup_searches_the_cite_key_index(client, db):
    """``idx_papers_cite_key`` is partial (``WHERE cite_key != ''``): only a
    query that repeats that condition may use it — else SQLite scans every
    row (OCR text included). The keyless-row check reads covering indexes."""
    conn = db._connect()
    try:
        lookup = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + host_services._CITE_KEY_LOOKUP, ("x",))]
        legacy = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + host_services._KEYLESS_ROWS)]
    finally:
        conn.close()
    assert lookup == ["SEARCH papers USING INDEX idx_papers_cite_key (cite_key=?)"]
    assert all("COVERING INDEX" in step for step in legacy if step.startswith("SCAN papers"))


def test_a_miss_reads_no_rows_while_every_item_has_a_key(client, db, monkeypatch):
    _insert(db, "With Pdf")
    statements: list[str] = []
    real = host_services._get_conn

    def traced():
        conn = real()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(host_services, "_get_conn", traced)
    assert host_services._CoreLibraryApi().get_reference("fehlt2020") is None
    assert not any("cite_key IS NULL" in s for s in statements), statements


def test_a_row_without_a_stored_key_is_still_found_by_its_legacy_key(client, db):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, cite_key) "
            "VALUES ('h-legacy', '', 'Legacy', 'Legacy', 'Mustermann, M.', 2019, '')"
        )
        conn.commit()
    finally:
        conn.close()
    legacy = host_services._cite_key({"authors": "Mustermann, M.", "year": 2019})
    ref = host_services._CoreLibraryApi().get_reference(legacy)
    assert ref is not None and ref["title"] == "Legacy" and ref["citekey"] == legacy


def test_the_fake_answers_the_same_keys_with_defaults():
    lib = InMemoryLibrary([
        {"citekey": "a2020", "title": "A"},
        {"citekey": "b2021", "title": "B", "id": 42, "has_pdf": True, "pages": "1--2"},
    ])
    a = lib.get_reference("a2020")
    assert REFERENCE_KEYS - {"authors", "year", "journal", "doi", "abstract"} <= set(a)
    assert a["id"] == 1 and a["has_pdf"] is False and a["volume"] is None
    b = lib.get_reference("b2021")
    assert b["id"] == 42 and b["has_pdf"] is True and b["pages"] == "1--2"
    assert lib.search_references("B")[0]["has_pdf"] is True
