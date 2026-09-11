"""One-time migration of a self-made notes field into `papers.notes` (#162).

Seam 1 of the spec: the real schema initialisation over a temporary database.
The tests state what a reader sees afterwards -- where their text ended up,
which user-defined fields are gone and which survived -- never which helper
ran.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from database import NOTES_MIGRATION_KEY
from literature_manager import Database


@pytest.fixture(autouse=True)
def legacy_install(db):
    """Undo the done-marker so each test starts from a pre-#160 database.

    Importing webapp already constructed the Database once, which marks the
    migration as done for a library that never had a notes field.
    """
    _forget_migration(db)
    yield


def _forget_migration(database):
    conn = database._connect()
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (NOTES_MIGRATION_KEY,))
        conn.commit()
    finally:
        conn.close()


def _migration_done(database):
    conn = database._connect()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (NOTES_MIGRATION_KEY,)
        ).fetchone()
        return bool(row and row["value"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# helpers -- raw SQL so the fixtures describe a *legacy* database, i.e. one
# written before the notes column existed.
# ---------------------------------------------------------------------------

def _insert_paper(conn, file_hash, notes=""):
    cur = conn.execute(
        """INSERT INTO papers (file_hash, filename, original_filename,
           title, authors, year, notes)
           VALUES (?, 'f.pdf', 'f.pdf', 'T', 'Doe, John', 2024, ?)""",
        (file_hash, notes),
    )
    return cur.lastrowid


def _insert_field(conn, name, field_type="text"):
    cur = conn.execute(
        "INSERT INTO custom_fields (name, field_type, position) VALUES (?, ?, 1)",
        (name, field_type),
    )
    return cur.lastrowid


def _set_value(conn, paper_id, field_id, value):
    conn.execute(
        "INSERT OR REPLACE INTO paper_custom_values (paper_id, field_id, value) "
        "VALUES (?, ?, ?)",
        (paper_id, field_id, value),
    )


def _notes(db, paper_id):
    conn = db._connect()
    try:
        return conn.execute(
            "SELECT notes FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()["notes"]
    finally:
        conn.close()


def _field_names(db):
    conn = db._connect()
    try:
        return [r["name"] for r in conn.execute(
            "SELECT name FROM custom_fields ORDER BY id"
        )]
    finally:
        conn.close()


def _values(db):
    conn = db._connect()
    try:
        return [
            (r["paper_id"], r["field_id"], r["value"])
            for r in conn.execute(
                "SELECT paper_id, field_id, value FROM paper_custom_values "
                "ORDER BY paper_id, field_id"
            )
        ]
    finally:
        conn.close()


class TestSingleNotesLikeField:
    def test_values_land_on_the_right_papers(self, db):
        conn = db._connect()
        try:
            p1 = _insert_paper(conn, "m1")
            p2 = _insert_paper(conn, "m2")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, p1, fid, "Kernidee: Kalibrierung")
            _set_value(conn, p2, fid, "spaeter nochmal lesen")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _notes(db, p1) == "Kernidee: Kalibrierung"
        assert _notes(db, p2) == "spaeter nochmal lesen"

    def test_field_and_its_values_are_gone_afterwards(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "m1")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, pid, fid, "etwas")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _field_names(db) == []
        assert _values(db) == []

    def test_unrelated_fields_and_values_survive(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "m1")
            notiz = _insert_field(conn, "Notiz")
            fortschritt = _insert_field(conn, "Lesefortschritt")
            bewertung = _insert_field(conn, "Bewertung", field_type="number")
            _set_value(conn, pid, notiz, "meine Notiz")
            _set_value(conn, pid, fortschritt, "halb")
            _set_value(conn, pid, bewertung, "4")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _field_names(db) == ["Lesefortschritt", "Bewertung"]
        assert _values(db) == [
            (pid, fortschritt, "halb"),
            (pid, bewertung, "4"),
        ]

    def test_matching_ignores_case_and_spelling_variant(self, db):
        for i, name in enumerate(["notizen", "NOTE", " Notes "]):
            _forget_migration(db)
            conn = db._connect()
            try:
                conn.execute("DELETE FROM paper_custom_values")
                conn.execute("DELETE FROM custom_fields")
                conn.execute("DELETE FROM papers")
                pid = _insert_paper(conn, f"c{i}")
                fid = _insert_field(conn, name)
                _set_value(conn, pid, fid, f"Text {i}")
                conn.commit()
            finally:
                conn.close()

            db.init_db()

            assert _notes(db, pid) == f"Text {i}", name
            assert _field_names(db) == [], name


class TestAmbiguousAndAbsent:
    def test_several_matches_migrate_nothing(self, db, caplog):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "a1")
            notiz = _insert_field(conn, "Notiz")
            notes = _insert_field(conn, "Notes")
            _set_value(conn, pid, notiz, "eins")
            _set_value(conn, pid, notes, "zwei")
            conn.commit()
        finally:
            conn.close()

        with caplog.at_level(logging.WARNING):
            db.init_db()

        assert _notes(db, pid) == ""
        assert _field_names(db) == ["Notiz", "Notes"]
        assert _values(db) == [(pid, notiz, "eins"), (pid, notes, "zwei")]
        assert any(
            "Notiz" in r.message and "Notes" in r.message
            for r in caplog.records if r.levelno >= logging.WARNING
        ), caplog.text

    def test_no_match_changes_nothing(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "n1")
            fid = _insert_field(conn, "Lesefortschritt")
            _set_value(conn, pid, fid, "halb")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _notes(db, pid) == ""
        assert _field_names(db) == ["Lesefortschritt"]
        assert _values(db) == [(pid, fid, "halb")]


class TestMergingAndRepetition:
    def test_existing_notes_are_kept_and_the_old_text_appended(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "e1", notes="schon da")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, pid, fid, "aus dem alten Feld")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _notes(db, pid) == "schon da\n\naus dem alten Feld"

    def test_empty_old_value_leaves_no_stray_whitespace(self, db):
        conn = db._connect()
        try:
            blank = _insert_paper(conn, "w1")
            spaces = _insert_paper(conn, "w2")
            kept = _insert_paper(conn, "w3", notes="schon da")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, blank, fid, "")
            _set_value(conn, spaces, fid, "   \n  ")
            _set_value(conn, kept, fid, "")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _notes(db, blank) == ""
        assert _notes(db, spaces) == ""
        assert _notes(db, kept) == "schon da"

    def test_running_initialisation_twice_appends_nothing(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "r1", notes="schon da")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, pid, fid, "alt")
            conn.commit()
        finally:
            conn.close()

        db.init_db()
        after_first = _notes(db, pid)
        db.init_db()

        assert _notes(db, pid) == after_first == "schon da\n\nalt"
        assert _field_names(db) == []


class TestTheRuleIsNotPermanent:
    """`init_db()` runs on every ``Database(...)`` construction, i.e. once per
    request. The name rule must therefore apply to the upgrade only -- a field
    the reader creates *afterwards* is theirs, whatever it is called."""

    def test_a_field_created_after_the_migration_survives(self, db):
        db.init_db()  # the upgrade: nothing to migrate here

        conn = db._connect()
        try:
            pid = _insert_paper(conn, "l1")
            fid = _insert_field(conn, "Notes")
            _set_value(conn, pid, fid, "meins, nicht deins")
            conn.commit()
        finally:
            conn.close()

        db.init_db()  # any later request

        assert _field_names(db) == ["Notes"]
        assert _values(db) == [(pid, fid, "meins, nicht deins")]
        assert _notes(db, pid) == ""

    def test_an_ambiguous_situation_is_retried_once_resolved(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "l2")
            notiz = _insert_field(conn, "Notiz")
            notes = _insert_field(conn, "Notes")
            _set_value(conn, pid, notiz, "eins")
            _set_value(conn, pid, notes, "zwei")
            conn.commit()
        finally:
            conn.close()

        db.init_db()
        assert not _migration_done(db)

        # The reader resolves it by hand, as the warning asked them to.
        conn = db._connect()
        try:
            conn.execute("DELETE FROM paper_custom_values WHERE field_id = ?", (notes,))
            conn.execute("DELETE FROM custom_fields WHERE id = ?", (notes,))
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _notes(db, pid) == "eins"
        assert _field_names(db) == []

    def test_a_non_text_field_with_a_notes_name_is_left_alone(self, db):
        conn = db._connect()
        try:
            pid = _insert_paper(conn, "t1")
            progress = _insert_field(conn, "Notes", field_type="progress")
            notiz = _insert_field(conn, "Notiz")
            _set_value(conn, pid, progress, "60")
            _set_value(conn, pid, notiz, "die echte Notiz")
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        assert _field_names(db) == ["Notes"]
        assert _values(db) == [(pid, progress, "60")]
        assert _notes(db, pid) == "die echte Notiz"


class TestOverAFreshDatabaseFile:
    def test_construction_runs_the_migration(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        schema_only = Database(path)  # creates the schema

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            pid = _insert_paper(conn, "f1")
            fid = _insert_field(conn, "Notiz")
            _set_value(conn, pid, fid, "handgeschrieben")
            conn.commit()
        finally:
            conn.close()
        _forget_migration(schema_only)  # make it a genuine pre-#160 database

        migrated = Database(path)

        assert _notes(migrated, pid) == "handgeschrieben"
        assert _field_names(migrated) == []
        assert _values(migrated) == []
