"""DELETE /api/papers/{id} for an Item without a PDF (filename == "").

Regression for a bug where ``os.path.join(Config.ALL_DIR, "")`` resolves to
the directory itself: ``os.path.exists`` is true (it's a directory) and
``os.remove`` then raises, after the DB row was already committed. The Item
ended up deleted but the request 500'd and the symlink rebuild never ran.
The bulk-delete endpoint (``POST /api/papers/bulk-delete``) already guards
this with ``if fn:``; this file pins the single-delete endpoint to the same
behaviour.
"""
from __future__ import annotations

import routers.papers as papers_router


def _insert(db, file_hash="d1", filename="", title="T", authors="Doe, John", year=2024):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_hash, filename, filename, title, authors, year),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_deleting_a_pdf_less_item_succeeds_and_rebuilds_symlinks(client, db, monkeypatch):
    pid = _insert(db)

    calls = []
    monkeypatch.setattr(
        papers_router, "_rebuild_all_symlinks", lambda database: calls.append(database)
    )

    resp = client.delete(f"/api/papers/{pid}")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok"}
    assert len(calls) == 1  # the rebuild must still run after a PDF-less delete

    conn = db._connect()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    assert row is None


def test_deleting_an_item_with_a_pdf_still_removes_the_file(client, db, seed_paper, monkeypatch):
    from pathlib import Path

    from literature_manager import Config

    filepath = Path(Config.ALL_DIR) / "test_paper.pdf"
    assert filepath.exists()

    calls = []
    monkeypatch.setattr(
        papers_router, "_rebuild_all_symlinks", lambda database: calls.append(database)
    )

    resp = client.delete(f"/api/papers/{seed_paper}")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert not filepath.exists()


def test_deleting_an_unknown_item_404s(client, db):
    resp = client.delete("/api/papers/999999")
    assert resp.status_code == 404
