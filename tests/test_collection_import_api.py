"""Import into a Collection (#203): BibTeX/.tex commit and the smart upload.

Seam 1 of PRD #200. The BibTeX commit takes a target Collection (existing or
new by name) and puts every Item of the import into it — created and already
present alike; a re-import reports the members it no longer carries as a
suggestion and never removes them. The smart upload takes a target too and
assigns duplicates as well. Without a target both behave exactly as before.
"""
from __future__ import annotations

import importlib
import json
from unittest.mock import patch

import pytest

import paper_ingest
import services.smart_import_pipeline as smart_import_pipeline

import_router = importlib.import_module("routers.import")

FAKE_PDF = b"%PDF-1.4 fake content for the collection upload test"
SKIP_ALL = "do_doi=false&do_categories=false&do_abstract=false&do_validate=false"


@pytest.fixture(autouse=True)
def _offline():
    """The commit enriches created Items via CrossRef/OpenAlex — never here."""
    with patch.object(paper_ingest, "enrich_imported_papers", return_value=0):
        yield


def _seed(db, title, doi="", cite_key="") -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, "
            "year, doi, cite_key) VALUES (?, '', ?, ?, 'Doe, J.', 2020, ?, ?)",
            (f"h-{title}", title, title, doi, cite_key),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _entry(key, title, doi="", matched=None):
    return {"key": key, "title": title, "authors": "Neu, Nina", "year": 2021,
            "doi": doi, "matched_paper_id": matched}


def _commit(client, entries, **target):
    resp = client.post("/api/import/bibtex/commit",
                       json={"entries": entries, "source_name": "paper.bib", **target})
    return resp


def _members(client, cid):
    return sorted(p["id"] for p in client.get(f"/api/papers?collection_id={cid}").json())


def _count(db, sql):
    conn = db._connect()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# BibTeX / .tex commit
# ---------------------------------------------------------------------------

def test_commit_puts_created_and_matched_items_into_the_collection(client, db):
    existing = _seed(db, "Already in the Library", doi="10.1/old")
    cid = client.post("/api/collections", json={"name": "Paper X"}).json()["id"]

    resp = _commit(client, [_entry("old", "Already in the Library", matched=existing),
                            _entry("neu2021", "A New Work on Trains")],
                   collection_id=cid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    created = [r["paper_id"] for r in data["results"] if r["status"] == "created"]
    assert data["collection"]["id"] == cid
    assert data["collection"]["created"] is False
    assert sorted(data["collection"]["added_ids"]) == sorted([existing, *created])
    assert data["collection"]["not_cited"] == []
    assert _members(client, cid) == sorted([existing, *created])


def test_commit_can_create_the_collection_by_name(client, db):
    data = _commit(client, [_entry("neu2021", "A New Work on Trains")],
                   new_collection_name="  Manuscript   2026 ").json()
    target = data["collection"]
    assert target["created"] is True
    assert target["name"] == "Manuscript 2026"
    listed = client.get("/api/collections").json()
    assert [(c["id"], c["item_count"]) for c in listed] == [(target["id"], 1)]


def test_reimport_suggests_no_longer_cited_items_and_removes_nothing(client, db):
    kept = _seed(db, "Still Cited Work", cite_key="kept")
    dropped = _seed(db, "Dropped Citation", cite_key="dropped")
    cid = client.post("/api/collections",
                      json={"name": "Paper X", "paper_ids": [kept, dropped]}).json()["id"]

    data = _commit(client, [_entry("kept", "Still Cited Work", matched=kept),
                            _entry("fresh", "Freshly Cited Work")],
                   collection_id=cid).json()
    target = data["collection"]
    assert [i["paper_id"] for i in target["not_cited"]] == [dropped]
    assert target["not_cited"][0]["cite_key"] == "dropped"
    assert target["not_cited"][0]["title"] == "Dropped Citation"
    assert target["already_ids"] == [kept]
    # A suggestion only: the dropped Item is still a member ...
    fresh = next(r["paper_id"] for r in data["results"] if r["status"] == "created")
    assert _members(client, cid) == sorted([kept, dropped, fresh])

    # ... until the user removes it with the explicit follow-up call.
    resp = client.request("DELETE", f"/api/collections/{cid}/items",
                          json={"paper_ids": [dropped]})
    assert resp.json() == {"removed_ids": [dropped]}
    assert _members(client, cid) == sorted([kept, fresh])


def test_an_unknown_target_is_refused_before_anything_is_created(client, db):
    resp = _commit(client, [_entry("neu2021", "A New Work on Trains")], collection_id=999)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.collection_not_found"
    assert _count(db, "SELECT COUNT(*) FROM papers") == 0


def test_an_empty_new_name_is_an_error_code(client, db):
    resp = _commit(client, [_entry("neu2021", "A New Work on Trains")],
                   new_collection_name="   ")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "error.collection_name_empty"
    assert _count(db, "SELECT COUNT(*) FROM papers") == 0
    assert _count(db, "SELECT COUNT(*) FROM collections") == 0


def test_a_tombstoned_collection_is_not_a_target(client, db):
    cid = client.post("/api/collections", json={"name": "Gone"}).json()["id"]
    client.delete(f"/api/collections/{cid}")
    resp = _commit(client, [_entry("neu2021", "A New Work")], collection_id=cid)
    assert resp.status_code == 404


def test_without_a_target_the_commit_is_unchanged(client, db):
    data = _commit(client, [_entry("neu2021", "A New Work on Trains")]).json()
    assert data["collection"] is None
    assert data["created"] == 1
    assert _count(db, "SELECT COUNT(*) FROM collections") == 0
    assert _count(db, "SELECT COUNT(*) FROM collection_items") == 0


# ---------------------------------------------------------------------------
# Smart upload
# ---------------------------------------------------------------------------

def _sse(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.split("\n")
            if line.strip().startswith("data: ")]


def _upload(client, name, text, collection_id=None, data=FAKE_PDF):
    query = SKIP_ALL + (f"&collection_id={collection_id}" if collection_id is not None else "")
    with patch.object(smart_import_pipeline, "extract_text_from_pdf", return_value=text), \
         patch.object(smart_import_pipeline, "discover_doi", return_value=None), \
         patch.object(import_router, "create_symlinks", return_value=None), \
         patch.object(import_router, "index_paper_after_import",
                      return_value={"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}):
        return client.post(f"/api/import/upload-smart?{query}",
                           files={"file": (name, data, "application/pdf")})


def test_upload_assigns_the_new_item_to_the_target(client, db):
    cid = client.post("/api/collections", json={"name": "Paper X"}).json()["id"]
    resp = _upload(client, "a.pdf", "Upload Into A Collection\nBody.", collection_id=cid)
    done = next(e for e in _sse(resp) if e["type"] == "complete")
    assert done["collection"] == {"id": cid, "name": "Paper X", "added": True}
    assert _members(client, cid) == [done["paper_id"]]


def test_upload_assigns_a_duplicate_file_too(client, db):
    first = _upload(client, "a.pdf", "Duplicate Into A Collection\nBody.")
    paper_id = next(e for e in _sse(first) if e["type"] == "complete")["paper_id"]
    cid = client.post("/api/collections", json={"name": "Paper X"}).json()["id"]

    again = _upload(client, "a_again.pdf", "Whatever", collection_id=cid)
    err = next(e for e in _sse(again) if e["type"] == "error")
    assert err["message"] == "import.step.duplicateFile"
    assert err["duplicate_paper_id"] == paper_id
    assert err["collection"] == {"id": cid, "name": "Paper X", "added": True}
    assert _members(client, cid) == [paper_id]


def test_upload_assigns_a_content_duplicate_too(client, db):
    existing = _seed(db, "Der Klimawandel und seine Folgen fuer die Arktis")
    cid = client.post("/api/collections", json={"name": "Paper X"}).json()["id"]
    resp = _upload(client, "k.pdf", "Der Klimawandel und seine Folgen fuer die Arktis\nText.",
                   collection_id=cid, data=b"%PDF-1.4 other bytes")
    err = next(e for e in _sse(resp) if e["type"] == "error")
    assert err["message"] == "import.step.duplicateItem"
    assert err["collection"]["added"] is True
    assert _members(client, cid) == [existing]


def test_upload_to_an_unknown_collection_is_refused_up_front(client, db):
    resp = _upload(client, "a.pdf", "Never Imported Title", collection_id=999)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.collection_not_found"
    assert _count(db, "SELECT COUNT(*) FROM papers") == 0


def test_upload_without_a_target_is_unchanged(client, db):
    """The default path (and with it every caller that never names a target)
    carries no ``collection`` field and writes no membership."""
    resp = _upload(client, "a.pdf", "Plain Upload Without Target\nBody.")
    events = _sse(resp)
    assert all("collection" not in e for e in events)
    assert any(e["type"] == "complete" for e in events)
    assert _count(db, "SELECT COUNT(*) FROM collection_items") == 0


def test_watch_folder_and_cli_pipeline_take_no_collection():
    """Out of scope (PRD #200): the watcher and the CLI import go through
    ``pipeline.process_paper``, which knows no target Collection."""
    import inspect

    import pipeline

    assert "collection_id" not in inspect.signature(pipeline.process_paper).parameters
