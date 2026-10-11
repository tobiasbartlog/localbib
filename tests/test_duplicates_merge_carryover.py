"""Regression tests for POST /api/duplicates/merge (LIB-B4).

A merge used to carry categories, custom values, references and collections
only: the dropped Item's notes, tags and date_added vanished, its PDF was
deleted even when the keeper had none, and the file was removed *before* the
commit. These tests pin the repaired behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from literature_manager import Config


def _seed(db, name: str, *, with_file: bool = False, filename: str | None = None,
          notes: str = "", tags: str = "", date_added: str = "") -> int:
    fn = name if filename is None else filename
    if with_file and fn:
        (Path(Config.ALL_DIR) / fn).write_bytes(b"%PDF-1.4 merge carryover " + name.encode())
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, "
            "year, notes, tags, date_added) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"hash-{name}", fn, fn, f"Title {name}", "Auth, A.", 2021,
             notes, tags, date_added),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row(db, pid: int):
    conn = db._connect()
    try:
        r = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _merge(client, keep, drop):
    return client.post("/api/duplicates/merge",
                       json={"keep_id": keep, "delete_ids": [drop]})


class TestMergeCarriesOver:
    def test_notes_of_both_are_kept(self, client, db):
        keep = _seed(db, "n_keep", filename="", notes="keeper note")
        drop = _seed(db, "n_drop", filename="", notes="dropped note")
        assert _merge(client, keep, drop).status_code == 200
        notes = _row(db, keep)["notes"]
        assert "keeper note" in notes and "dropped note" in notes

    def test_equal_notes_are_not_doubled(self, client, db):
        # Three-way merge: the equal note appears once, the other one is kept
        # too (on the old code the "other" note was lost, so this bites).
        keep = _seed(db, "e_keep", filename="", notes="same")
        drop = _seed(db, "e_drop", filename="", notes="same")
        other = _seed(db, "e_other", filename="", notes="other")
        resp = client.post("/api/duplicates/merge",
                           json={"keep_id": keep, "delete_ids": [drop, other]})
        assert resp.status_code == 200
        notes = _row(db, keep)["notes"]
        assert notes.count("same") == 1
        assert "other" in notes

    def test_note_only_on_the_dropped_item_moves_over(self, client, db):
        keep = _seed(db, "o_keep", filename="")
        drop = _seed(db, "o_drop", filename="", notes="only here")
        _merge(client, keep, drop)
        assert _row(db, keep)["notes"] == "only here"

    def test_tags_are_united_without_repeats(self, client, db):
        keep = _seed(db, "t_keep", filename="", tags=json.dumps(["a", "b"]))
        drop = _seed(db, "t_drop", filename="", tags=json.dumps(["b", "c"]))
        _merge(client, keep, drop)
        assert json.loads(_row(db, keep)["tags"]) == ["a", "b", "c"]

    def test_the_earlier_date_added_wins(self, client, db):
        keep = _seed(db, "d_keep", filename="", date_added="2024-05-01 10:00:00")
        drop = _seed(db, "d_drop", filename="", date_added="2022-01-02 08:00:00")
        _merge(client, keep, drop)
        assert _row(db, keep)["date_added"] == "2022-01-02 08:00:00"


class TestMergeFiles:
    def test_a_fileless_keeper_takes_over_the_dropped_pdf(self, client, db):
        keep = _seed(db, "f_keep", filename="")
        drop = _seed(db, "f_drop", with_file=True, filename="f_drop.pdf")
        path = Path(Config.ALL_DIR) / "f_drop.pdf"
        assert _merge(client, keep, drop).status_code == 200
        row = _row(db, keep)
        assert row["filename"] == "f_drop.pdf"
        assert row["file_hash"] == "hash-f_drop"
        assert path.exists()
        assert _row(db, drop) is None

    def test_a_keeper_with_a_file_still_loses_the_dropped_pdf(self, client, db):
        keep = _seed(db, "g_keep", with_file=True, filename="g_keep.pdf")
        drop = _seed(db, "g_drop", with_file=True, filename="g_drop.pdf")
        assert _merge(client, keep, drop).status_code == 200
        assert (Path(Config.ALL_DIR) / "g_keep.pdf").exists()
        assert not (Path(Config.ALL_DIR) / "g_drop.pdf").exists()

    def test_the_file_is_deleted_only_after_a_successful_commit(
            self, client, db, monkeypatch):
        keep = _seed(db, "c_keep", with_file=True, filename="c_keep.pdf")
        drop = _seed(db, "c_drop", with_file=True, filename="c_drop.pdf")
        path = Path(Config.ALL_DIR) / "c_drop.pdf"

        import routers.duplicates as dup_router
        real_get_conn = dup_router._get_conn

        class FailingCommit:
            def __init__(self, conn):
                self._conn = conn

            def commit(self):
                raise RuntimeError("disk full")

            def __getattr__(self, name):
                return getattr(self._conn, name)

        monkeypatch.setattr(dup_router, "_get_conn",
                            lambda: FailingCommit(real_get_conn()))
        with pytest.raises(RuntimeError):
            _merge(client, keep, drop)
        monkeypatch.undo()

        assert path.exists(), "file must survive a merge that did not commit"
        assert _row(db, drop) is not None


class TestMergeKeeperWithMissingFile:
    """LIB-B4 residual: a keeper whose ``filename`` is set but whose file is
    gone from disk (a ``missing_pdf`` Item) has no file. Judging by the
    non-empty name sent the dropped Item's real PDF to deletion."""

    def test_the_dropped_pdf_survives_and_moves_to_the_keeper(self, client, db):
        keep = _seed(db, "m_keep", with_file=False, filename="m_keep.pdf")
        drop = _seed(db, "m_drop", with_file=True, filename="m_drop.pdf")
        path = Path(Config.ALL_DIR) / "m_drop.pdf"
        assert _merge(client, keep, drop).status_code == 200
        assert path.exists(), "the only real PDF must not be deleted"
        row = _row(db, keep)
        assert row["filename"] == "m_drop.pdf"
        assert row["file_hash"] == "hash-m_drop"
        assert _row(db, drop) is None

    def test_two_missing_files_delete_nothing(self, client, db):
        keep = _seed(db, "mm_keep", with_file=False, filename="mm_keep.pdf")
        drop = _seed(db, "mm_drop", with_file=False, filename="mm_drop.pdf")
        assert _merge(client, keep, drop).status_code == 200
        assert _row(db, keep)["filename"] == "mm_keep.pdf"

    def test_a_keeper_with_a_real_file_still_drops_a_dropped_pdf(self, client, db):
        keep = _seed(db, "r_keep", with_file=True, filename="r_keep.pdf")
        drop = _seed(db, "r_drop", with_file=True, filename="r_drop.pdf")
        _merge(client, keep, drop)
        assert (Path(Config.ALL_DIR) / "r_keep.pdf").exists()
        assert not (Path(Config.ALL_DIR) / "r_drop.pdf").exists()
        assert _row(db, keep)["filename"] == "r_keep.pdf"
