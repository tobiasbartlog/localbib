"""Cite-key persistence: backfill migration + assignment on import."""
from __future__ import annotations


def _insert_raw(db, file_hash, authors, year, cite_key=""):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, cite_key)
               VALUES (?, 'f.pdf', 'f.pdf', 'T', ?, ?, ?)""",
            (file_hash, authors, year, cite_key),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _keys_by_id(db):
    conn = db._connect()
    try:
        return [
            r["cite_key"]
            for r in conn.execute("SELECT cite_key FROM papers ORDER BY id")
        ]
    finally:
        conn.close()


def _paper_data(file_hash, authors, year):
    return {
        "file_hash": file_hash,
        "filename": "f.pdf",
        "original_filename": "f.pdf",
        "title": "T",
        "authors": authors,
        "year": year,
        "doi": "",
        "isbn": "",
        "abstract": "",
        "journal": "",
        "publisher": "",
        "raw_metadata": "",
        "ocr_text": "",
        "page_count": 0,
    }


class TestBackfill:
    def test_assigns_unique_keys_deterministically(self, db):
        _insert_raw(db, "h1", "Smith, John", 2020)
        _insert_raw(db, "h2", "Smith, Jane", 2020)
        _insert_raw(db, "h3", "", None)
        db.init_db()
        assert _keys_by_id(db) == ["Smith2020", "Smith2020a", "UnknownXXXX"]

    def test_rerun_keeps_existing_keys(self, db):
        _insert_raw(db, "h1", "Smith, John", 2020)
        db.init_db()
        first = _keys_by_id(db)
        db.init_db()
        assert _keys_by_id(db) == first

    def test_manual_keys_untouched_and_respected(self, db):
        _insert_raw(db, "h1", "Smith, John", 2020, cite_key="Smith2020")
        _insert_raw(db, "h2", "Smith, Jane", 2020)
        db.init_db()
        assert _keys_by_id(db) == ["Smith2020", "Smith2020a"]


class TestAddPaper:
    def test_assigns_key_on_import(self, db):
        pid = db.add_paper(_paper_data("h1", "Doe, Jane", 2024))
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT cite_key FROM papers WHERE id = ?", (pid,)
            ).fetchone()
        finally:
            conn.close()
        assert row["cite_key"] == "Doe2024"

    def test_collision_on_import_gets_suffix(self, db):
        db.add_paper(_paper_data("h1", "Doe, Jane", 2024))
        db.add_paper(_paper_data("h2", "Doe, John", 2024))
        assert _keys_by_id(db) == ["Doe2024", "Doe2024a"]
