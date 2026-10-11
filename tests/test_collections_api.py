"""Collections (Sammlungen, #201): the HTTP API end to end.

Seam 1 of PRD #200 — CRUD, archive/restore, delete as tombstone, idempotent
adding, notes, the ``collection_id`` list filter, the Item detail and the
cascade when an Item leaves the Library. The pure rules get a few narrow unit
tests where the HTTP seam would be clumsy.
"""
from __future__ import annotations

import pytest

from services import library_collections as svc


def _seed(db, title, filename="", year=2020, doi="") -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"h-{title}", filename, filename or title, title, "Doe, J.", year, doi),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _create(client, name="Paper X", **extra):
    resp = client.post("/api/collections", json={"name": name, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rows(db, sql, args=()):
    conn = db._connect()
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_create_list_get_and_update(client, db):
    a = _seed(db, "Alpha", filename="a.pdf")
    b = _seed(db, "Beta")
    created = _create(client, "  Literature   for X ", description="Why does Y?", paper_ids=[a, b])
    assert created["name"] == "Literature for X"
    assert created["description"] == "Why does Y?"
    assert created["item_count"] == 2
    assert created["pdf_count"] == 1
    assert created["archived"] is False
    assert created["added_ids"] == [a, b]

    listed = client.get("/api/collections").json()
    assert [c["id"] for c in listed] == [created["id"]]

    detail = client.get(f"/api/collections/{created['id']}").json()
    assert detail["item_count"] == 2 and detail["pdf_count"] == 1

    patched = client.patch(f"/api/collections/{created['id']}",
                           json={"name": "Renamed", "description": ""}).json()
    assert patched["name"] == "Renamed"
    assert patched["description"] == ""


def test_names_are_not_unique_but_never_empty(client, db):
    _create(client, "Same")
    _create(client, "Same")
    assert len(client.get("/api/collections").json()) == 2

    resp = client.post("/api/collections", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "error.collection_name_empty"

    cid = _create(client, "Ok")["id"]
    resp = client.patch(f"/api/collections/{cid}", json={"name": ""})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "error.collection_name_empty"


def test_archive_and_restore(client, db):
    keep = _create(client, "Active")
    old = _create(client, "Done")
    resp = client.patch(f"/api/collections/{old['id']}", json={"archived": True})
    assert resp.json()["archived"] is True

    assert [c["id"] for c in client.get("/api/collections").json()] == [keep["id"]]
    assert [c["id"] for c in client.get("/api/collections?archived=true").json()] == [old["id"]]
    assert {c["id"] for c in client.get("/api/collections?archived=all").json()} == {
        keep["id"], old["id"]}
    assert client.get("/api/collections?archived=maybe").json()["detail"] == \
        "error.collection_archived_filter_invalid"

    client.patch(f"/api/collections/{old['id']}", json={"archived": False})
    assert {c["id"] for c in client.get("/api/collections").json()} == {keep["id"], old["id"]}


def test_delete_is_a_tombstone_that_keeps_items_and_membership(client, db):
    a = _seed(db, "Alpha", filename="a.pdf")
    cid = _create(client, "Gone soon", paper_ids=[a])["id"]

    resp = client.delete(f"/api/collections/{cid}")
    assert resp.status_code == 200
    assert resp.json()["item_count"] == 1

    # Gone from every user-facing surface ...
    assert client.get(f"/api/collections/{cid}").status_code == 404
    assert client.get(f"/api/collections/{cid}").json()["detail"] == "error.collection_not_found"
    assert client.get("/api/collections?archived=all").json() == []
    assert client.get(f"/api/papers?collection_id={cid}").status_code == 404
    assert client.get(f"/api/papers/{a}").json()["collections"] == []
    for method, url, body in [
        ("PATCH", f"/api/collections/{cid}", {"name": "x"}),
        ("DELETE", f"/api/collections/{cid}", None),
        ("POST", f"/api/collections/{cid}/items", {"paper_ids": [a]}),
        ("DELETE", f"/api/collections/{cid}/items", {"paper_ids": [a]}),
        ("PATCH", f"/api/collections/{cid}/items/{a}", {"note": "n"}),
    ]:
        assert client.request(method, url, json=body).status_code == 404, (method, url)

    # ... but the Item, and the frozen membership, survive.
    assert client.get(f"/api/papers/{a}").status_code == 200
    assert _rows(db, "SELECT collection_id, paper_id FROM collection_items") == [(cid, a)]
    conn = db._connect()
    try:
        assert svc.member_ids(conn, cid) == [a]
        tomb = svc.get_collection(conn, cid, include_deleted=True)
        assert tomb["deleted"] is True and tomb["deleted_at"]
        assert svc.get_collection(conn, cid) is None
    finally:
        conn.close()


def test_unknown_collection_is_404(client, db):
    assert client.get("/api/collections/999").json()["detail"] == "error.collection_not_found"
    assert client.get("/api/papers?collection_id=999").status_code == 404


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def test_adding_is_idempotent_and_removing_keeps_the_library(client, db):
    a, b, c = _seed(db, "Alpha"), _seed(db, "Beta"), _seed(db, "Gamma")
    cid = _create(client)["id"]

    first = client.post(f"/api/collections/{cid}/items", json={"paper_ids": [a, b, a]}).json()
    assert first == {"added_ids": [a, b], "already_ids": []}
    again = client.post(f"/api/collections/{cid}/items", json={"paper_ids": [b, c]}).json()
    assert again == {"added_ids": [c], "already_ids": [b]}
    assert client.get(f"/api/collections/{cid}").json()["item_count"] == 3

    removed = client.request("DELETE", f"/api/collections/{cid}/items",
                             json={"paper_ids": [a, c, 12345]}).json()
    assert removed == {"removed_ids": [a, c]}
    assert client.get(f"/api/collections/{cid}").json()["item_count"] == 1
    # Removal from a Collection never touches the Library.
    assert client.get(f"/api/papers/{a}").status_code == 200


def test_adding_an_unknown_item_writes_nothing(client, db):
    a = _seed(db, "Alpha")
    cid = _create(client)["id"]
    resp = client.post(f"/api/collections/{cid}/items", json={"paper_ids": [a, 9999]})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.item_not_found"
    assert _rows(db, "SELECT * FROM collection_items") == []


def test_note_per_item(client, db):
    a = _seed(db, "Alpha")
    b = _seed(db, "Beta")
    cid = _create(client, "With notes", paper_ids=[a])["id"]

    resp = client.patch(f"/api/collections/{cid}/items/{a}", json={"note": " key method paper "})
    assert resp.json() == {"collection_id": cid, "paper_id": a, "note": "key method paper"}

    resp = client.patch(f"/api/collections/{cid}/items/{b}", json={"note": "x"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.collection_item_not_found"

    detail = client.get(f"/api/papers/{a}").json()
    assert detail["collections"] == [
        {"id": cid, "name": "With notes", "archived": False, "note": "key method paper",
         "added_at": detail["collections"][0]["added_at"]},
    ]


# ---------------------------------------------------------------------------
# List filter
# ---------------------------------------------------------------------------

def test_list_filter_combines_with_other_filters(client, db):
    a = _seed(db, "Neural alpha", year=2020)
    b = _seed(db, "Neural beta", year=2021)
    _seed(db, "Neural gamma", year=2022)
    cid = _create(client, paper_ids=[a, b])["id"]
    client.patch(f"/api/collections/{cid}/items/{b}", json={"note": "why"})

    papers = client.get(f"/api/papers?collection_id={cid}").json()
    assert {p["id"] for p in papers} == {a, b}
    assert {p["id"]: p["collection_note"] for p in papers} == {a: "", b: "why"}

    searched = client.get(f"/api/papers?collection_id={cid}&search=beta").json()
    assert [p["id"] for p in searched] == [b]

    cat_id = client.post("/api/categories", json={"name": "Methods"}).json()["id"]
    client.post("/api/papers/categories", json={"paper_ids": [a], "category_id": cat_id})
    by_cat = client.get(f"/api/papers?collection_id={cid}&category_id={cat_id}").json()
    assert [p["id"] for p in by_cat] == [a]

    empty = _create(client, "Empty")["id"]
    assert client.get(f"/api/papers?collection_id={empty}").json() == []


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------

def test_deleting_an_item_removes_it_from_every_collection(client, db):
    a = _seed(db, "Alpha")
    b = _seed(db, "Beta")
    one = _create(client, "One", paper_ids=[a, b])["id"]
    two = _create(client, "Two", paper_ids=[a])["id"]
    gone = _create(client, "Tomb", paper_ids=[a])["id"]
    client.delete(f"/api/collections/{gone}")

    assert client.delete(f"/api/papers/{a}").status_code == 200
    assert _rows(db, "SELECT collection_id, paper_id FROM collection_items ORDER BY collection_id") == [
        (one, b)]
    assert client.get(f"/api/collections/{one}").json()["item_count"] == 1
    assert client.get(f"/api/collections/{two}").json()["item_count"] == 0

    client.post("/api/papers/bulk-delete", json={"paper_ids": [b]})
    assert _rows(db, "SELECT * FROM collection_items") == []


def test_old_project_tables_are_untouched(db):
    names = {r[0] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "paper_projects", "collections", "collection_items"} <= names


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def test_clean_name():
    assert svc.clean_name("  a \n b ") == "a b"
    with pytest.raises(svc.CollectionError) as err:
        svc.clean_name(None)
    assert err.value.code == "error.collection_name_empty"


def test_split_add_and_membership_diff():
    assert svc.split_add([1, 2], [2, 3, 3, 4]) == ([3, 4], [2])
    assert svc.membership_diff([1, 2, 3], [3, 4]) == {"new": [4], "gone": [1, 2]}
    assert svc.membership_diff([], []) == {"new": [], "gone": []}
