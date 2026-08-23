"""Cite-key HTTP API: PUT edit + stored keys in bibtex endpoints."""
from __future__ import annotations


def _insert(db, file_hash, authors, year, cite_key):
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


class TestCiteKeyPut:
    def test_updates_key(self, client, db):
        pid = _insert(db, "h1", "Smith, John", 2020, "Smith2020")
        resp = client.put(f"/api/papers/{pid}/cite-key", json={"cite_key": "MeinKey42"})
        assert resp.status_code == 200
        assert resp.json()["cite_key"] == "MeinKey42"
        assert client.get(f"/api/papers/{pid}/bibtex").json()["key"] == "MeinKey42"

    def test_duplicate_rejected_with_409(self, client, db):
        _insert(db, "h1", "Smith, John", 2020, "Smith2020")
        pid = _insert(db, "h2", "Doe, Jane", 2021, "Doe2021")
        resp = client.put(f"/api/papers/{pid}/cite-key", json={"cite_key": "Smith2020"})
        assert resp.status_code == 409

    def test_setting_own_key_again_is_ok(self, client, db):
        pid = _insert(db, "h1", "Smith, John", 2020, "Smith2020")
        resp = client.put(f"/api/papers/{pid}/cite-key", json={"cite_key": "Smith2020"})
        assert resp.status_code == 200

    def test_invalid_key_rejected_with_422(self, client, db):
        pid = _insert(db, "h1", "Smith, John", 2020, "Smith2020")
        for bad in ["", "  ", "has space", "br{ace}", "comma,key"]:
            resp = client.put(f"/api/papers/{pid}/cite-key", json={"cite_key": bad})
            assert resp.status_code == 422, bad

    def test_unknown_paper_404(self, client, db):
        resp = client.put("/api/papers/99999/cite-key", json={"cite_key": "X1"})
        assert resp.status_code == 404


class TestBibtexUsesStoredKey:
    def test_single_paper_endpoint(self, client, db):
        pid = _insert(db, "h1", "Smith, John", 2020, "CustomKey")
        data = client.get(f"/api/papers/{pid}/bibtex").json()
        assert data["key"] == "CustomKey"
        assert "@" in data["bibtex"] and "{CustomKey," in data["bibtex"]

    def test_legacy_row_without_key_falls_back(self, client, db):
        pid = _insert(db, "h1", "Smith, John", 2020, "")
        assert client.get(f"/api/papers/{pid}/bibtex").json()["key"] == "Smith2020"

    def test_export_uses_stored_keys(self, client, db):
        _insert(db, "h1", "Smith, John", 2020, "Alpha1")
        _insert(db, "h2", "Smith, Jane", 2020, "Beta2")
        content = client.get("/api/export/bibtex").text
        assert "{Alpha1," in content
        assert "{Beta2," in content
