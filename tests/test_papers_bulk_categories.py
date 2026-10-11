"""Bulk category assignment for drag-and-drop onto the sidebar (#199).

``POST /api/papers/categories`` adds one category to many Items and must
never rewrite an existing row: an LLM assignment keeps its ``assigned_by``
and ``confidence``. ``POST /api/papers/categories/remove`` is the undo
counterpart and rebuilds the symlinks once per call, not once per pair.
"""
from __future__ import annotations

import routers.papers as papers_router


def _paper(db, n):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title) "
            "VALUES (?, '', '', ?)",
            (f"h{n}", f"Item {n}"),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _category(db, name="Methods"):
    conn = db._connect()
    try:
        cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rows(db, category_id):
    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT paper_id, confidence, assigned_by FROM paper_categories "
            "WHERE category_id = ? ORDER BY paper_id",
            (category_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def test_assigns_the_category_to_every_item(client, db):
    a, b = _paper(db, 1), _paper(db, 2)
    cat = _category(db)

    resp = client.post("/api/papers/categories", json={"paper_ids": [a, b], "category_id": cat})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"added_ids": [a, b], "already_ids": []}
    assert [r["paper_id"] for r in _rows(db, cat)] == [a, b]
    assert all(r["assigned_by"] == "manual" for r in _rows(db, cat))


def test_an_existing_llm_assignment_is_left_untouched(client, db):
    a, b = _paper(db, 1), _paper(db, 2)
    cat = _category(db)
    db.assign_category(a, cat, confidence=0.87, assigned_by="llm")

    resp = client.post("/api/papers/categories", json={"paper_ids": [a, b], "category_id": cat})

    assert resp.json() == {"added_ids": [b], "already_ids": [a]}
    rows = {r["paper_id"]: r for r in _rows(db, cat)}
    assert rows[a]["assigned_by"] == "llm"
    assert rows[a]["confidence"] == 0.87


def test_duplicate_ids_in_one_request_count_once(client, db):
    a = _paper(db, 1)
    cat = _category(db)

    resp = client.post("/api/papers/categories", json={"paper_ids": [a, a], "category_id": cat})

    assert resp.json() == {"added_ids": [a], "already_ids": []}


def test_other_categories_of_the_item_stay(client, db):
    a = _paper(db, 1)
    old, new = _category(db, "Old"), _category(db, "New")
    db.assign_category(a, old, confidence=0.0, assigned_by="manual")

    client.post("/api/papers/categories", json={"paper_ids": [a], "category_id": new})

    assert [r["paper_id"] for r in _rows(db, old)] == [a]
    assert [r["paper_id"] for r in _rows(db, new)] == [a]


def test_unknown_category_is_404_and_writes_nothing(client, db):
    a = _paper(db, 1)

    resp = client.post("/api/papers/categories", json={"paper_ids": [a], "category_id": 999999})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.category_not_found"


def test_unknown_item_is_404_and_writes_nothing(client, db):
    a = _paper(db, 1)
    cat = _category(db)

    resp = client.post(
        "/api/papers/categories", json={"paper_ids": [a, 999999], "category_id": cat}
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.item_not_found"
    assert _rows(db, cat) == []


def test_empty_selection_is_a_validation_error(client, db):
    cat = _category(db)

    resp = client.post("/api/papers/categories", json={"paper_ids": [], "category_id": cat})

    assert resp.status_code == 422


def test_remove_drops_exactly_the_named_pairs_and_rebuilds_once(client, db, monkeypatch):
    a, b, c = _paper(db, 1), _paper(db, 2), _paper(db, 3)
    cat = _category(db)
    for pid in (a, b, c):
        db.assign_category(pid, cat, confidence=0.0, assigned_by="manual")
    calls = []
    monkeypatch.setattr(papers_router, "_rebuild_all_symlinks", lambda d: calls.append(d))

    resp = client.post(
        "/api/papers/categories/remove", json={"paper_ids": [a, b], "category_id": cat}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"removed_ids": [a, b]}
    assert [r["paper_id"] for r in _rows(db, cat)] == [c]
    assert len(calls) == 1


def test_remove_of_unassigned_pairs_skips_the_rebuild(client, db, monkeypatch):
    a = _paper(db, 1)
    cat = _category(db)
    calls = []
    monkeypatch.setattr(papers_router, "_rebuild_all_symlinks", lambda d: calls.append(d))

    resp = client.post(
        "/api/papers/categories/remove", json={"paper_ids": [a], "category_id": cat}
    )

    assert resp.json() == {"removed_ids": []}
    assert calls == []


def test_remove_still_undoes_the_rest_when_an_item_was_deleted_meanwhile(client, db):
    a = _paper(db, 1)
    cat = _category(db)
    db.assign_category(a, cat, confidence=0.0, assigned_by="manual")

    resp = client.post(
        "/api/papers/categories/remove", json={"paper_ids": [999999, a], "category_id": cat}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"removed_ids": [a]}
    assert _rows(db, cat) == []


def test_remove_with_unknown_category_is_404(client, db):
    a = _paper(db, 1)

    resp = client.post(
        "/api/papers/categories/remove", json={"paper_ids": [a], "category_id": 999999}
    )

    assert resp.status_code == 404
