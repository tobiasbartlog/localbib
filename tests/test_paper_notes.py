"""Notes as a first-class field on a paper (#160).

Driven through the FastAPI TestClient (Seam 2 of the spec): what the dedicated
endpoint returns, what the paper payload carries afterwards, what the library
search finds, and the one rule that made the endpoint dedicated in the first
place -- saving a note never renames the PDF on disk.
"""
from __future__ import annotations

from pathlib import Path

import metadata_validation
from literature_manager import Config


def _insert(db, file_hash="n1", filename="", title="T", authors="Doe, John",
            year=2024, abstract="", notes=""):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, abstract, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_hash, filename, filename, title, authors, year, abstract, notes),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


class TestNotesEndpoint:
    def test_writes_and_reads_back(self, client, db):
        pid = _insert(db)
        resp = client.put(f"/api/papers/{pid}/notes", json={"notes": "## Kernidee\n- eins"})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "## Kernidee\n- eins"
        assert client.get(f"/api/papers/{pid}").json()["notes"] == "## Kernidee\n- eins"

    def test_clearing_persists_as_empty(self, client, db):
        pid = _insert(db, notes="etwas")
        assert client.put(f"/api/papers/{pid}/notes", json={"notes": ""}).status_code == 200
        assert client.get(f"/api/papers/{pid}").json()["notes"] == ""

    def test_unknown_paper_404(self, client, db):
        assert client.put("/api/papers/99999/notes", json={"notes": "x"}).status_code == 404

    def test_new_paper_starts_with_empty_notes(self, client, db):
        pid = _insert(db)
        assert client.get(f"/api/papers/{pid}").json()["notes"] == ""

    def test_list_payload_carries_notes(self, client, db):
        pid = _insert(db, notes="Randbemerkung")
        row = next(p for p in client.get("/api/papers").json() if p["id"] == pid)
        assert row["notes"] == "Randbemerkung"


class TestNotesDoNotTouchTheFile:
    def test_saving_notes_keeps_the_pdf_filename(self, client, db, seed_paper):
        before = client.get(f"/api/papers/{seed_paper}").json()["filename"]
        assert client.put(
            f"/api/papers/{seed_paper}/notes", json={"notes": "eine Notiz"}
        ).status_code == 200
        after = client.get(f"/api/papers/{seed_paper}").json()
        assert after["filename"] == before
        assert (Path(Config.ALL_DIR) / before).exists()


class TestNotesInSearch:
    def test_search_matches_text_that_lives_only_in_a_note(self, client, db):
        pid = _insert(db, file_hash="s1", title="Ganz anderer Titel",
                      authors="Roe, Jane", abstract="Nichts davon hier.")
        client.put(f"/api/papers/{pid}/notes", json={"notes": "Stichwort Kalibrierung"})
        hits = client.get("/api/papers", params={"search": "Kalibrierung"}).json()
        assert [p["id"] for p in hits] == [pid]

    def test_search_still_matches_title(self, client, db):
        pid = _insert(db, file_hash="s2", title="Kalibrierung im Feld")
        hits = client.get("/api/papers", params={"search": "Kalibrierung"}).json()
        assert [p["id"] for p in hits] == [pid]


class TestNotesAreNotAUserDefinedField:
    def test_no_field_is_seeded(self, client, db):
        pid = _insert(db)
        client.put(f"/api/papers/{pid}/notes", json={"notes": "x"})
        assert client.get("/api/custom-fields").json() == []
        assert client.get(f"/api/papers/{pid}").json()["custom_fields"] == []

    def test_own_text_fields_still_work(self, client, db):
        pid = _insert(db)
        created = client.post(
            "/api/custom-fields", json={"name": "Lesefortschritt", "field_type": "text"}
        ).json()
        client.put(
            f"/api/papers/{pid}/custom-values",
            json={"field_id": created["id"], "value": "halb"},
        )
        fields = client.get(f"/api/papers/{pid}").json()["custom_fields"]
        assert [(f["name"], f["value"]) for f in fields] == [("Lesefortschritt", "halb")]


def test_metadata_validation_never_proposes_notes():
    assert "notes" not in metadata_validation._TRACKED_FIELDS
