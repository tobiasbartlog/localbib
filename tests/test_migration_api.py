"""API tests for the migration router (#174): analyze, commit (SSE), undo.

Analyze must not write, commit must write everything the options asked for,
undo must take back exactly what commit created — and nothing else.
"""

from __future__ import annotations

import json
import os

import pytest

from literature_manager import Config

FAKE_PDF = b"%PDF-1.4 migration test attachment"


def _parse_sse(response) -> list[dict]:
    events: list[dict] = []
    for line in response.text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _complete(events: list[dict]) -> dict:
    for event in events:
        if event.get("type") == "complete":
            return event
    raise AssertionError(f"no complete frame in {events}")


@pytest.fixture
def export(tmp_path):
    """A .bib export with one attached PDF, one missing one, tags, a group,
    a note and a timestamp — the whole surface of one migration item."""
    pdf = tmp_path / "files" / "smith.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(FAKE_PDF)

    bib = tmp_path / "library.bib"
    bib.write_text(
        """
@article{smith2020,
  title = {A Study of Migrated Things},
  author = {Smith, Jane},
  journal = {Journal of Things},
  year = {2020},
  doi = {10.1234/migrated},
  keywords = {machine learning, ethics},
  groups = {Diss/Kapitel 2},
  note = {Read before chapter 2},
  timestamp = {2014-07-01},
  file = {:files/smith.pdf:pdf},
}

@book{mueller2018,
  title = {Ein Buch ueber Migration},
  author = {Mueller, Anna},
  year = {2018},
  file = {Full Text PDF:files/gone.pdf:application/pdf},
}
""",
        encoding="utf-8",
    )
    return {"bib": str(bib), "pdf": str(pdf), "dir": str(tmp_path)}


def _commit(client, export, **options):
    body = {"source": "bibtex", "path": export["bib"], "options": options}
    resp = client.post("/api/migration/commit", json=body)
    assert resp.status_code == 200
    return _complete(_parse_sse(resp))


# ---------------------------------------------------------------------------
# GET /api/migration/sources
# ---------------------------------------------------------------------------

class TestSources:
    def test_lists_the_registered_adapters(self, client):
        data = client.get("/api/migration/sources").json()
        ids = {s["id"] for s in data["sources"]}
        assert {"bibtex", "ris", "zotero_rdf"} <= ids

    def test_every_source_carries_a_label_key_not_a_sentence(self, client):
        for source in client.get("/api/migration/sources").json()["sources"]:
            assert source["label_key"].startswith("migration.source.")
            assert source["kind"] in ("file", "folder")


# ---------------------------------------------------------------------------
# POST /api/migration/analyze
# ---------------------------------------------------------------------------

class TestAnalyze:
    def test_reports_items_attachments_collections_and_tags(self, client, db, export):
        data = client.post("/api/migration/analyze",
                           json={"source": "bibtex", "path": export["bib"]}).json()
        assert data["total"] == 2
        assert data["new"] == 2 and data["matched"] == 0
        assert data["attachments_found"] == 1
        assert data["attachments_missing"] == 1
        assert data["collections"] == [["Diss", "Kapitel 2"]]
        assert data["tags"] == ["machine learning", "ethics"]

    def test_it_never_writes(self, client, db, export):
        client.post("/api/migration/analyze",
                    json={"source": "bibtex", "path": export["bib"]})
        conn = db._connect()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 0
            assert conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_an_item_the_library_already_has_is_reported_as_matched(self, client, db, export):
        db.add_paper({
            "file_hash": "already-here", "filename": "", "original_filename": "x",
            "title": "A Study of Migrated Things", "authors": "Smith, Jane",
            "year": 2020, "doi": "10.1234/migrated", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        data = client.post("/api/migration/analyze",
                           json={"source": "bibtex", "path": export["bib"]}).json()
        assert data["matched"] == 1
        matched = [i for i in data["items"] if i["matched_paper_id"]][0]
        assert matched["match_strategy"] == "doi"

    def test_an_unknown_source_is_an_error_code(self, client, db, export):
        resp = client.post("/api/migration/analyze",
                           json={"source": "citavi", "path": export["bib"]})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.migration_source_unknown"

    def test_a_path_that_does_not_exist_is_an_error_code(self, client, db, tmp_path):
        resp = client.post("/api/migration/analyze",
                           json={"source": "bibtex", "path": str(tmp_path / "nope.bib")})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.migration_path_not_found"


# ---------------------------------------------------------------------------
# POST /api/migration/commit
# ---------------------------------------------------------------------------

class TestCommit:
    def test_creates_papers_and_streams_the_smart_import_frames(self, client, db, export):
        resp = client.post("/api/migration/commit",
                           json={"source": "bibtex", "path": export["bib"]})
        assert "text/event-stream" in resp.headers.get("content-type", "")
        events = _parse_sse(resp)
        assert events[0]["type"] == "progress"
        done = _complete(events)
        assert done["created"] == 2
        assert done["matched"] == 0
        assert done["failed"] == 0
        assert done["run_id"]
        assert done["llm_failures"] == []

    def test_the_attached_pdf_gets_a_real_hash_a_file_and_an_index(self, client, db, export):
        done = _commit(client, export, attach_pdfs="all")
        assert done["attached"] == 1

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT * FROM papers WHERE cite_key = 'smith2020'").fetchone()
        finally:
            conn.close()
        assert row["filename"]
        assert os.path.exists(os.path.join(Config.ALL_DIR, row["filename"]))
        # The placeholder hash was replaced by the SHA256 of the real bytes.
        import hashlib
        assert row["file_hash"] == hashlib.sha256(FAKE_PDF).hexdigest()

    def test_tags_notes_and_date_added_land_in_their_columns(self, client, db, export):
        _commit(client, export)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT tags, notes, date_added, raw_metadata FROM papers "
                "WHERE cite_key = 'smith2020'").fetchone()
        finally:
            conn.close()
        assert json.loads(row["tags"]) == ["machine learning", "ethics"]
        assert row["notes"] == "Read before chapter 2"
        assert row["date_added"] == "2014-07-01"
        # Unknown source fields stay readable.
        assert json.loads(row["raw_metadata"])["groups"] == "Diss/Kapitel 2"

    def test_the_collection_becomes_a_category_path_assigned_by_import(self, client, db, export):
        _commit(client, export)
        conn = db._connect()
        try:
            parent = conn.execute(
                "SELECT id FROM categories WHERE name = 'Diss'").fetchone()
            child = conn.execute(
                "SELECT id, parent_id FROM categories WHERE name = 'Kapitel 2'").fetchone()
            assignment = conn.execute(
                "SELECT assigned_by FROM paper_categories WHERE category_id = ?",
                (child["id"],)).fetchone()
        finally:
            conn.close()
        assert child["parent_id"] == parent["id"]
        assert assignment["assigned_by"] == "import"

    def test_options_off_means_nothing_is_carried_over(self, client, db, export):
        _commit(client, export, import_tags=False, import_notes=False,
                import_date_added=False, import_collections=False,
                attach_pdfs="none")
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT tags, notes, date_added, filename FROM papers "
                "WHERE title LIKE 'A Study%'").fetchone()
            categories = conn.execute("SELECT COUNT(*) c FROM categories").fetchone()
        finally:
            conn.close()
        assert (row["tags"], row["notes"], row["date_added"]) == ("", "", "")
        assert row["filename"] == ""
        assert categories["c"] == 0

    def test_keep_cite_keys_off_generates_one(self, client, db, export):
        _commit(client, export, keep_cite_keys=False)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT cite_key FROM papers WHERE title LIKE 'A Study%'").fetchone()
        finally:
            conn.close()
        assert row["cite_key"] and row["cite_key"] != "smith2020"

    def test_selected_keys_limits_the_run(self, client, db, export):
        resp = client.post("/api/migration/commit", json={
            "source": "bibtex", "path": export["bib"],
            "selected_keys": ["mueller2018"]})
        done = _complete(_parse_sse(resp))
        assert done["created"] == 1
        conn = db._connect()
        try:
            titles = [r["title"] for r in conn.execute("SELECT title FROM papers")]
        finally:
            conn.close()
        assert titles == ["Ein Buch ueber Migration"]

    def test_an_existing_item_is_matched_not_duplicated(self, client, db, export):
        db.add_paper({
            "file_hash": "already-here", "filename": "", "original_filename": "x",
            "title": "A Study of Migrated Things", "authors": "Smith, Jane",
            "year": 2020, "doi": "10.1234/migrated", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        done = _commit(client, export)
        assert (done["created"], done["matched"]) == (1, 1)

    def test_duplicates_attach_puts_the_pdf_on_the_existing_item(self, client, db, export):
        paper_id = db.add_paper({
            "file_hash": "already-here", "filename": "", "original_filename": "x",
            "title": "A Study of Migrated Things", "authors": "Smith, Jane",
            "year": 2020, "doi": "10.1234/migrated", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        done = _commit(client, export, duplicates="attach")
        assert done["attached"] == 1
        conn = db._connect()
        try:
            row = conn.execute("SELECT filename FROM papers WHERE id = ?",
                               (paper_id,)).fetchone()
        finally:
            conn.close()
        assert row["filename"]

    def test_an_empty_selection_completes_without_creating_anything(self, client, db, export):
        resp = client.post("/api/migration/commit", json={
            "source": "bibtex", "path": export["bib"], "selected_keys": []})
        done = _complete(_parse_sse(resp))
        assert done["created"] == 0
        assert done["run_id"]


# ---------------------------------------------------------------------------
# POST /api/migration/undo
# ---------------------------------------------------------------------------

class TestUndo:
    def test_removes_the_created_papers_and_their_files(self, client, db, export):
        done = _commit(client, export)
        conn = db._connect()
        try:
            filename = conn.execute(
                "SELECT filename FROM papers WHERE cite_key = 'smith2020'"
            ).fetchone()["filename"]
        finally:
            conn.close()
        filepath = os.path.join(Config.ALL_DIR, filename)
        assert os.path.exists(filepath)

        undo = client.post("/api/migration/undo", json={"run_id": done["run_id"]}).json()
        assert undo["deleted"] == 2
        conn = db._connect()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 0
        finally:
            conn.close()
        assert not os.path.exists(filepath)

    def test_a_matched_paper_survives_the_undo(self, client, db, export):
        keeper = db.add_paper({
            "file_hash": "already-here", "filename": "", "original_filename": "x",
            "title": "A Study of Migrated Things", "authors": "Smith, Jane",
            "year": 2020, "doi": "10.1234/migrated", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        done = _commit(client, export)
        client.post("/api/migration/undo", json={"run_id": done["run_id"]})
        conn = db._connect()
        try:
            rows = [r["id"] for r in conn.execute("SELECT id FROM papers")]
        finally:
            conn.close()
        assert rows == [keeper]

    def test_an_unknown_run_is_an_error_code(self, client, db):
        resp = client.post("/api/migration/undo", json={"run_id": "does-not-exist"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "error.migration_run_not_found"

    def test_undoing_twice_is_refused_not_repeated(self, client, db, export):
        done = _commit(client, export)
        assert client.post("/api/migration/undo",
                           json={"run_id": done["run_id"]}).status_code == 200
        assert client.post("/api/migration/undo",
                           json={"run_id": done["run_id"]}).status_code == 404

    def test_undo_detaches_a_pdf_attached_to_an_already_existing_paper(
            self, client, db, export):
        """Regression: with duplicates="attach", the PDF this run hung onto an
        already-existing (matched) paper must be booked under the run too —
        otherwise undo can never take it back (module docstring's promise)."""
        paper_id = db.add_paper({
            "file_hash": "already-here", "filename": "", "original_filename": "x",
            "title": "A Study of Migrated Things", "authors": "Smith, Jane",
            "year": 2020, "doi": "10.1234/migrated", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        done = _commit(client, export, duplicates="attach")
        assert done["attached"] == 1
        assert done["created"] == 1     # mueller2018 has no match, is still created

        conn = db._connect()
        try:
            filename = conn.execute(
                "SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()["filename"]
        finally:
            conn.close()
        assert filename
        filepath = os.path.join(Config.ALL_DIR, filename)
        assert os.path.exists(filepath)

        undo = client.post("/api/migration/undo", json={"run_id": done["run_id"]}).json()
        assert undo["detached"] == 1
        assert undo["deleted"] == 1

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        finally:
            conn.close()
        assert row is not None          # the pre-existing paper itself survives
        assert row["filename"] == ""
        assert not os.path.exists(filepath)

    def test_undo_rebuilds_symlinks_when_it_only_deletes_nothing_detached(
            self, client, db, export, monkeypatch):
        """Regression: undo used to rebuild category symlinks only when it
        detached a PDF. A run that merely created papers (no attachment)
        leaves category symlinks stale after undo deletes those papers."""
        import routers.migration as migration_router

        calls = []
        monkeypatch.setattr(migration_router, "_rebuild_all_symlinks",
                            lambda database: calls.append(database))

        done = _commit(client, export, attach_pdfs="none")
        assert done["attached"] == 0
        assert done["created"] == 2

        client.post("/api/migration/undo", json={"run_id": done["run_id"]})
        assert len(calls) == 1

    def test_a_disconnect_mid_commit_still_leaves_an_undoable_run(
            self, client, db, export):
        """Regression: the run used to be recorded only after the whole loop
        finished. A client that disconnects mid-stream closes the commit
        generator (``GeneratorExit``) between two items — the papers created
        before that point must still be booked under ``run_id``, or they are
        orphaned: created, but with no way to undo them."""
        import asyncio

        import routers.migration as migration_router

        adapter = migration_router.ADAPTERS["bibtex"]
        items = migration_router._read_items(adapter, export["bib"])
        options = migration_router.MigrationOptionsIn().to_options()

        async def run():
            gen = migration_router._commit_events(
                items, options, "library.bib", set(), adapter.id, export["bib"])
            seen = []
            try:
                # start frame, item-1 progress frame, item-1 attach frame —
                # item 1 (smith2020, has a real attachment) is fully created
                # and attached by the third yield; item 2 (mueller2018) is
                # never even started.
                for _ in range(3):
                    seen.append(await gen.__anext__())
            finally:
                await gen.aclose()
            return seen

        seen = asyncio.run(run())
        assert any('"step": "attach"' in s for s in seen)

        conn = db._connect()
        try:
            papers = conn.execute("SELECT id, cite_key FROM papers").fetchall()
            # migration_runs is not truncated between tests (unlike papers),
            # so pick the run this call just inserted rather than assume it
            # is the table's only row.
            run_row = conn.execute(
                "SELECT run_id, created_paper_ids FROM migration_runs "
                "ORDER BY rowid DESC LIMIT 1").fetchone()
        finally:
            conn.close()

        # Only item 1 was created before the disconnect — item 2 never ran.
        assert [r["cite_key"] for r in papers] == ["smith2020"]
        created_ids = json.loads(run_row["created_paper_ids"])
        assert created_ids == [papers[0]["id"]]

        # And that recorded run can actually be undone.
        undo = client.post("/api/migration/undo",
                           json={"run_id": run_row["run_id"]}).json()
        assert undo["deleted"] == 1
        conn = db._connect()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The Zotero RDF source end to end (#175)
# ---------------------------------------------------------------------------

ZOTERO_EXPORT = os.path.join(os.path.dirname(__file__), "fixtures", "zotero_export")
ZOTERO_RDF = os.path.join(ZOTERO_EXPORT, "Zotero-Export.rdf")


class TestZoteroRdfSource:
    def _commit(self, client, path=ZOTERO_RDF, **options):
        resp = client.post("/api/migration/commit", json={
            "source": "zotero_rdf", "path": path, "options": options})
        assert resp.status_code == 200
        return _complete(_parse_sse(resp))

    def test_analyze_reports_the_whole_export(self, client, db):
        data = client.post("/api/migration/analyze",
                           json={"source": "zotero_rdf", "path": ZOTERO_RDF}).json()
        assert data["total"] == 4
        assert data["new"] == 4
        assert data["attachments_found"] == 2
        assert data["attachments_missing"] == 0
        assert data["collections"] == [["Diss", "Kapitel 2"], ["Diss"]]
        assert data["tags"] == ["machine learning", "ethics", "migration"]

    def test_the_export_folder_is_accepted_like_the_rdf_file(self, client, db):
        data = client.post("/api/migration/analyze",
                           json={"source": "zotero_rdf", "path": ZOTERO_EXPORT}).json()
        assert data["total"] == 4

    def test_commit_creates_the_items_with_their_pdfs(self, client, db):
        done = self._commit(client)
        assert (done["created"], done["failed"]) == (4, 0)
        assert done["attached"] == 2

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT file_hash, filename FROM papers "
                "WHERE cite_key = 'smith2020'").fetchone()
        finally:
            conn.close()
        assert os.path.exists(os.path.join(Config.ALL_DIR, row["filename"]))
        import hashlib
        with open(os.path.join(ZOTERO_EXPORT, "files", "12",
                               "Smith - 2020 - A Study of Migrated Things.pdf"), "rb") as fh:
            assert row["file_hash"] == hashlib.sha256(fh.read()).hexdigest()

    def test_tags_notes_and_date_added_land_in_their_columns(self, client, db):
        self._commit(client)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT tags, notes, date_added FROM papers "
                "WHERE cite_key = 'smith2020'").fetchone()
        finally:
            conn.close()
        assert json.loads(row["tags"]) == ["machine learning", "ethics"]
        assert row["notes"].startswith("Read before chapter 2.")
        assert row["date_added"] == "2014-07-01 09:15:00"

    def test_the_collection_tree_is_created_nested(self, client, db):
        self._commit(client)
        conn = db._connect()
        try:
            parent = conn.execute(
                "SELECT id FROM categories WHERE name = 'Diss'").fetchone()
            child = conn.execute(
                "SELECT id, parent_id FROM categories WHERE name = 'Kapitel 2'").fetchone()
            assigned = conn.execute(
                "SELECT assigned_by FROM paper_categories WHERE category_id = ?",
                (child["id"],)).fetchone()
        finally:
            conn.close()
        assert child["parent_id"] == parent["id"]
        assert assigned["assigned_by"] == "import"

    def test_undo_takes_the_whole_run_back(self, client, db):
        done = self._commit(client)
        undo = client.post("/api/migration/undo",
                           json={"run_id": done["run_id"]}).json()
        assert undo["deleted"] == 4
        conn = db._connect()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_a_malformed_rdf_is_a_422_error_code_not_a_500(self, client, db, tmp_path):
        broken = tmp_path / "broken.rdf"
        broken.write_text("<rdf:RDF><bib:Article></rdf:RDF>", encoding="utf-8")
        resp = client.post("/api/migration/analyze",
                           json={"source": "zotero_rdf", "path": str(broken)})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "error.migration_unreadable"

    def test_a_bib_file_read_as_zotero_rdf_is_refused_readably(self, client, db, export):
        resp = client.post("/api/migration/analyze",
                           json={"source": "zotero_rdf", "path": export["bib"]})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "error.migration_unreadable"


# ---------------------------------------------------------------------------
# POST /api/papers/bulk-extract-references — the new optional body
# ---------------------------------------------------------------------------

class TestBulkExtractSelection:
    def test_without_a_body_it_still_runs_over_the_library(self, client, db):
        resp = client.post("/api/papers/bulk-extract-references")
        assert resp.status_code == 200

    def test_paper_ids_limits_the_run(self, client, db, seed_paper, monkeypatch):
        import services.reference_extraction as ref_svc
        monkeypatch.setattr(ref_svc, "extract_all_pdf_pages", lambda p: [])

        events = _parse_sse(client.post("/api/papers/bulk-extract-references",
                                        json={"paper_ids": [seed_paper]}))
        assert _complete(events)["processed"] >= 0

        empty = _parse_sse(client.post("/api/papers/bulk-extract-references",
                                        json={"paper_ids": []}))
        assert _complete(empty)["processed"] == 0


# ---------------------------------------------------------------------------
# The pdf_folder source end to end (#176) — the Mendeley path
# ---------------------------------------------------------------------------

def _folder_pdf(path, lines) -> str:
    """A real one-page PDF with real text — the abgleich reads what is in it."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    padded = list(lines) + [
        "This page carries enough running text that the extraction step",
        "stays well above the OCR threshold while the suite runs offline.",
    ]
    for line in padded:
        page.insert_text((72, y), line, fontsize=11)
        y += 18
    doc.save(str(path))
    doc.close()
    return str(path)


def _metadata_only_paper(db, title: str, doi: str = "") -> int:
    return db.add_paper({
        "file_hash": f"placeholder-{title[:20]}", "filename": "",
        "original_filename": f"Migration: {title[:40]}", "title": title,
        "authors": "", "year": 2019, "doi": doi, "isbn": "", "abstract": "",
        "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
    })


class TestPdfFolderSource:
    """A folder of PDFs and three items without files (the Mendeley path):
    analyze proposes, the reader confirms, commit attaches only that."""

    @pytest.fixture
    def library(self, db, tmp_path):
        folder = tmp_path / "mendeley"
        folder.mkdir()
        _folder_pdf(folder / "a.pdf", ["Deep Learning for Coffee Roasting",
                                       "doi:10.5555/coffee-roast"])
        _folder_pdf(folder / "b.pdf", ["The Secret Life of Sourdough Starters"])
        _folder_pdf(folder / "c.pdf", ["Quarterly Numbers of a Company Nobody Cited"])
        return {
            "folder": str(folder),
            "doi_paper": _metadata_only_paper(db, "Roasting Beans At Scale",
                                              "10.5555/coffee-roast"),
            "title_paper": _metadata_only_paper(
                db, "The Secret Life of Sourdough Starters"),
            "other_paper": _metadata_only_paper(
                db, "Plate Tectonics Of The Lower Rhine Embayment"),
        }

    def _analyze(self, client, library):
        resp = client.post("/api/migration/analyze",
                           json={"source": "pdf_folder", "path": library["folder"]})
        assert resp.status_code == 200
        return resp.json()

    def _commit(self, client, library, matches):
        resp = client.post("/api/migration/commit", json={
            "source": "pdf_folder", "path": library["folder"], "matches": matches})
        assert resp.status_code == 200
        return _complete(_parse_sse(resp))

    def test_the_source_is_registered_as_a_folder_source(self, client):
        sources = {s["id"]: s for s in
                   client.get("/api/migration/sources").json()["sources"]}
        assert sources["pdf_folder"]["kind"] == "folder"
        assert sources["pdf_folder"]["result_kind"] == "pdf_folder"
        assert sources["pdf_folder"]["label_key"] == "migration.source.pdf_folder"

    def test_analyze_returns_the_match_table(self, client, db, library):
        data = self._analyze(client, library)
        assert data["kind"] == "pdf_folder"
        assert data["scanned"] == 3
        by_name = {m["pdf_name"]: m for m in data["matches"]}
        assert by_name["a.pdf"]["paper_id"] == library["doi_paper"]
        assert by_name["a.pdf"]["strategy"] == "doi"
        assert by_name["b.pdf"]["paper_id"] == library["title_paper"]
        assert by_name["b.pdf"]["strategy"] == "title"
        assert [u["pdf_name"] for u in data["unmatched"]] == ["c.pdf"]

    def test_analyze_never_attaches_anything(self, client, db, library):
        self._analyze(client, library)
        conn = db._connect()
        try:
            names = [r["filename"] for r in conn.execute("SELECT filename FROM papers")]
        finally:
            conn.close()
        assert names == ["", "", ""]

    def test_a_folder_that_does_not_exist_is_an_error_code(self, client, db, tmp_path):
        resp = client.post("/api/migration/analyze",
                           json={"source": "pdf_folder",
                                 "path": str(tmp_path / "nope")})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.migration_path_not_found"

    def test_a_pdf_the_library_already_owns_is_reported_as_already_owned(
            self, client, db, library):
        import hashlib as _hashlib
        with open(os.path.join(library["folder"], "a.pdf"), "rb") as fh:
            data = fh.read()
        db.add_paper({
            "file_hash": _hashlib.sha256(data).hexdigest(), "filename": "owned.pdf",
            "original_filename": "owned.pdf", "title": "Already Here",
            "authors": "", "year": 2020, "doi": "", "isbn": "", "abstract": "",
            "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        report = self._analyze(client, library)
        assert [o["pdf_name"] for o in report["already_owned"]] == ["a.pdf"]
        assert "a.pdf" not in {m["pdf_name"] for m in report["matches"]}

    def test_commit_attaches_only_the_matches_the_client_sent_back(
            self, client, db, library):
        table = self._analyze(client, library)
        confirmed = [m for m in table["matches"] if m["pdf_name"] == "b.pdf"]
        done = self._commit(client, library, [
            {"pdf_path": m["pdf_path"], "paper_id": m["paper_id"]} for m in confirmed])

        assert done["attached"] == 1
        assert done["failed"] == 0
        conn = db._connect()
        try:
            rows = {r["id"]: r["filename"] for r in
                    conn.execute("SELECT id, filename FROM papers")}
        finally:
            conn.close()
        assert rows[library["title_paper"]]
        assert rows[library["doi_paper"]] == ""
        assert os.path.exists(os.path.join(Config.ALL_DIR, rows[library["title_paper"]]))

    def test_the_attached_pdf_gets_the_real_hash_and_its_text(self, client, db, library):
        import hashlib as _hashlib
        table = self._analyze(client, library)
        pair = next(m for m in table["matches"] if m["pdf_name"] == "b.pdf")
        self._commit(client, library, [{"pdf_path": pair["pdf_path"],
                                        "paper_id": pair["paper_id"]}])
        with open(pair["pdf_path"], "rb") as fh:
            expected = _hashlib.sha256(fh.read()).hexdigest()
        conn = db._connect()
        try:
            row = conn.execute("SELECT file_hash, ocr_text FROM papers WHERE id = ?",
                               (pair["paper_id"],)).fetchone()
        finally:
            conn.close()
        assert row["file_hash"] == expected
        assert "Sourdough" in row["ocr_text"]

    def test_a_pdf_outside_the_analysed_folder_is_refused(self, client, db, library,
                                                          tmp_path):
        outside = _folder_pdf(tmp_path / "elsewhere.pdf", ["Somewhere else entirely"])
        done = self._commit(client, library, [
            {"pdf_path": outside, "paper_id": library["title_paper"]}])
        assert done["failed"] == 1
        assert done["attached"] == 0

    def test_an_empty_confirmation_attaches_nothing(self, client, db, library):
        done = self._commit(client, library, [])
        assert done["attached"] == 0
        assert done["run_id"]

    def test_undo_detaches_the_file_and_keeps_the_item(self, client, db, library):
        table = self._analyze(client, library)
        pair = next(m for m in table["matches"] if m["pdf_name"] == "b.pdf")
        done = self._commit(client, library, [{"pdf_path": pair["pdf_path"],
                                               "paper_id": pair["paper_id"]}])
        conn = db._connect()
        try:
            filename = conn.execute("SELECT filename FROM papers WHERE id = ?",
                                    (pair["paper_id"],)).fetchone()["filename"]
        finally:
            conn.close()
        filepath = os.path.join(Config.ALL_DIR, filename)
        assert os.path.exists(filepath)

        undo = client.post("/api/migration/undo",
                           json={"run_id": done["run_id"]}).json()
        assert undo["detached"] == 1
        assert undo["deleted"] == 0
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT filename, ocr_text FROM papers WHERE id = ?",
                (pair["paper_id"],)).fetchone()
            chunks = conn.execute(
                "SELECT COUNT(*) c FROM paper_chunks WHERE paper_id = ?",
                (pair["paper_id"],)).fetchone()["c"]
        finally:
            conn.close()
        assert row["filename"] == ""
        assert row["ocr_text"] == ""
        assert chunks == 0
        assert not os.path.exists(filepath)
        # The reader's own folder is never touched by a migration.
        assert os.path.exists(pair["pdf_path"])


class TestImportUnmatched:
    """The one-click handover of an unmatched PDF to the smart import."""

    def test_unmatched_pdfs_land_in_the_smart_imports_input_folder(
            self, client, db, tmp_path):
        pdf = _folder_pdf(tmp_path / "loose.pdf", ["A Paper Nobody Catalogued"])
        resp = client.post("/api/migration/pdf-folder/import-unmatched",
                           json={"paths": [pdf]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        name = data["copied"][0]["filename"]
        assert os.path.exists(os.path.join(Config.INPUT_DIR, name))
        assert os.path.exists(pdf)          # the original stays where it was
        pending = client.get("/api/import/pending").json()
        assert name in {f["filename"] for f in pending["files"]}
        os.remove(os.path.join(Config.INPUT_DIR, name))

    def test_a_path_that_is_not_a_pdf_is_reported_not_raised(self, client, db, tmp_path):
        junk = tmp_path / "notes.txt"
        junk.write_text("no pdf here", encoding="utf-8")
        data = client.post("/api/migration/pdf-folder/import-unmatched",
                           json={"paths": [str(junk)]}).json()
        assert data["count"] == 0
        assert data["failed"][0]["error"] == "error.migration_path_not_found"
