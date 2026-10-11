"""Collections through the Add-on Contract (#204) — Seam 2 of PRD #200.

One scenario runs against two libraries: the core's real host service
(``host_services._CoreLibraryApi``, driven through the HTTP API the user
drives) and the fake an Add-on tests against
(``plugin_api.testing.InMemoryLibrary``, driven through its helpers). The same
assertions hold for both, so the fake cannot drift from the core.

The rest is core-only: every writer outside the Collections router (BibTeX
commit, upload, migration and its undo, Item deletion, duplicate merge)
announces its change, and an Add-on's forgotten subscription dies with it.
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import host_services
import library_events
import paper_ingest
import plugin_loader
from plugin_api import COLLECTION_EVENT_ACTIONS, PluginManifest
from plugin_api.testing import InMemoryLibrary

migration_router = importlib.import_module("routers.migration")

WIRE_KEYS = {"id", "name", "description", "archived", "archived_at", "deleted",
             "deleted_at", "created_at", "updated_at", "item_count"}


# ---------------------------------------------------------------------------
# Two drivers, one scenario
# ---------------------------------------------------------------------------

class FakeDriver:
    def __init__(self):
        self.library = InMemoryLibrary()

    def item(self, key, title):
        self.library.add({"citekey": key, "title": title, "year": 2020})
        return key

    def create(self, name, keys=()):
        return self.library.add_collection(name, refs=keys)

    def rename(self, cid, name):
        self.library.rename_collection(cid, name)

    def describe(self, cid, text):
        self.library.set_collection_description(cid, text)

    def archive(self, cid, archived):
        self.library.archive_collection(cid, archived)

    def add(self, cid, keys):
        refs = self.library.get_collection_refs(cid)
        self.library.set_collection_refs(cid, refs + [k for k in keys if k not in refs])

    def remove(self, cid, keys):
        self.library.set_collection_refs(
            cid, [k for k in self.library.get_collection_refs(cid) if k not in keys])

    def delete(self, cid):
        self.library.delete_collection(cid)


class CoreDriver:
    def __init__(self, client, db):
        self.client, self.db = client, db
        self.library = host_services._CoreLibraryApi()
        self.ids = {}

    def item(self, key, title):
        conn = self.db._connect()
        try:
            cur = conn.execute(
                "INSERT INTO papers (file_hash, filename, original_filename, title, authors, "
                "year, cite_key) VALUES (?, '', ?, ?, 'Doe, J.', 2020, ?)",
                (f"h-{key}", title, title, key),
            )
            conn.commit()
            self.ids[key] = cur.lastrowid
        finally:
            conn.close()
        return key

    def _ok(self, resp):
        assert resp.status_code < 300, resp.text
        return resp.json()

    def create(self, name, keys=()):
        return self._ok(self.client.post(
            "/api/collections", json={"name": name, "paper_ids": [self.ids[k] for k in keys]}))["id"]

    def rename(self, cid, name):
        self._ok(self.client.patch(f"/api/collections/{cid}", json={"name": name}))

    def describe(self, cid, text):
        self._ok(self.client.patch(f"/api/collections/{cid}", json={"description": text}))

    def archive(self, cid, archived):
        self._ok(self.client.patch(f"/api/collections/{cid}", json={"archived": archived}))

    def add(self, cid, keys):
        self._ok(self.client.post(f"/api/collections/{cid}/items",
                                  json={"paper_ids": [self.ids[k] for k in keys]}))

    def remove(self, cid, keys):
        self._ok(self.client.request("DELETE", f"/api/collections/{cid}/items",
                                     json={"paper_ids": [self.ids[k] for k in keys]}))

    def delete(self, cid):
        self._ok(self.client.delete(f"/api/collections/{cid}"))


@pytest.fixture(params=["fake", "core"])
def driver(request):
    if request.param == "fake":
        return FakeDriver()
    return CoreDriver(request.getfixturevalue("client"), request.getfixturevalue("db"))


def _bib_keys(bib: str) -> list[str]:
    return [line.split("{", 1)[1].rstrip(",") for line in bib.splitlines() if line.startswith("@")]


def test_collections_read_and_change_alike_in_core_and_fake(driver):
    lib = driver.library
    a = driver.item("doe2020a", "Alpha Paper")
    b = driver.item("doe2020b", "Beta Paper")
    c = driver.item("doe2020c", "Gamma Paper")
    events = []
    stop = lib.on_change(events.append)
    try:
        main = driver.create("Paper X", [a, b])
        empty = driver.create("another list")

        listed = lib.list_collections()
        assert [x["name"] for x in listed] == ["another list", "Paper X"]
        for x in listed:
            assert set(x) == WIRE_KEYS
            assert (x["archived"], x["deleted"], x["deleted_at"]) == (False, False, None)
        assert lib.get_collection(main)["item_count"] == 2
        assert lib.get_collection_refs(main) == [a, b]
        assert _bib_keys(lib.export_collection_bibtex(main)) == [a, b]
        assert lib.get_collection_refs(empty) == []
        assert lib.export_collection_bibtex(empty).strip() == ""

        driver.rename(main, "Paper Y")
        driver.describe(main, "Why does Y?")
        driver.archive(main, True)
        archived = lib.get_collection(main)
        assert archived["archived"] is True and archived["archived_at"]
        assert (archived["name"], archived["description"]) == ("Paper Y", "Why does Y?")
        driver.archive(main, False)
        assert lib.get_collection(main)["archived"] is False
        driver.add(main, [c])
        driver.remove(main, [a])
        assert lib.get_collection_refs(main) == [b, c]

        driver.delete(main)
        # The tombstone: still listed and readable here, membership frozen.
        tomb = lib.get_collection(main)
        assert tomb["deleted"] is True and tomb["deleted_at"]
        assert tomb["item_count"] == 2
        assert main in [x["id"] for x in lib.list_collections()]
        assert lib.get_collection_refs(main) == [b, c]
        assert _bib_keys(lib.export_collection_bibtex(main)) == [b, c]

        assert lib.get_collection(999_999) is None
        assert lib.get_collection_refs(999_999) == []
        assert lib.export_collection_bibtex(999_999).strip() == ""
    finally:
        stop()

    collection_events = [e for e in events if e.get("type") == "collection"]
    assert [(e["action"], e["collection_id"]) for e in collection_events] == [
        ("created", main), ("members_changed", main), ("created", empty),
        ("renamed", main), ("updated", main), ("archived", main), ("restored", main),
        ("members_changed", main), ("members_changed", main), ("deleted", main),
    ]
    assert all(set(e) == {"type", "action", "collection_id"} for e in collection_events)
    assert {e["action"] for e in collection_events} <= set(COLLECTION_EVENT_ACTIONS)


# ---------------------------------------------------------------------------
# Core only: the feed itself, no-op writes, the other writers
# ---------------------------------------------------------------------------

@pytest.fixture
def feed():
    events = []
    stop = host_services._CoreLibraryApi().on_change(events.append)
    yield SimpleNamespace(events=events, actions=lambda: [(e["action"], e["collection_id"]) for e in events])
    stop()


def _seed(db, title, cite_key="", filename=""):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, "
            "cite_key) VALUES (?, ?, ?, ?, 'Doe, J.', 2020, ?)",
            (f"h-{title}", filename, title, title, cite_key or title.lower().replace(" ", "")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_a_failing_listener_never_breaks_the_writer(client, db, feed):
    def boom(_event):
        raise RuntimeError("listener bug")

    stop = library_events.subscribe(boom)
    try:
        resp = client.post("/api/collections", json={"name": "Paper X"})
    finally:
        stop()
    assert resp.status_code == 201
    assert feed.actions() == [("created", resp.json()["id"])]


def test_writes_that_change_nothing_stay_silent(client, db, feed):
    pid = _seed(db, "Alpha")
    cid = client.post("/api/collections", json={"name": "X", "paper_ids": [pid]}).json()["id"]
    feed.events.clear()
    client.post(f"/api/collections/{cid}/items", json={"paper_ids": [pid]})        # already in
    client.request("DELETE", f"/api/collections/{cid}/items", json={"paper_ids": [999]})
    client.patch(f"/api/collections/{cid}", json={"name": "X", "archived": False})
    client.patch(f"/api/collections/{cid}/items/{pid}", json={"note": "why"})     # a note
    assert feed.events == []


def test_cite_keys_match_the_keys_get_reference_uses(client, db):
    pid = _seed(db, "Legacy Row", cite_key="")
    conn = db._connect()
    try:
        conn.execute("UPDATE papers SET cite_key = NULL WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()
    cid = client.post("/api/collections", json={"name": "X", "paper_ids": [pid]}).json()["id"]
    lib = host_services._CoreLibraryApi()
    (key,) = lib.get_collection_refs(cid)
    assert key and lib.get_reference(key)["id"] == pid
    assert _bib_keys(lib.export_collection_bibtex(cid)) == [key]


def test_bibtex_commit_into_a_new_collection_announces_it(client, db, feed):
    with patch.object(paper_ingest, "enrich_imported_papers", return_value=0):
        data = client.post("/api/import/bibtex/commit", json={
            "entries": [{"key": "neu2021", "title": "A New Work", "authors": "Neu, Nina",
                         "year": 2021, "doi": "", "matched_paper_id": None}],
            "source_name": "paper.bib", "new_collection_name": "Manuscript"}).json()
    cid = data["collection"]["id"]
    assert feed.actions() == [("created", cid), ("members_changed", cid)]


def test_upload_assignment_announces_the_membership(db, feed):
    import_router = importlib.import_module("routers.import")
    pid = _seed(db, "Alpha")
    conn = db._connect()
    try:
        cid = conn.execute("INSERT INTO collections (name) VALUES ('X')").lastrowid
        conn.commit()
    finally:
        conn.close()
    assert import_router._assign_to_collection(cid, pid)["added"] is True
    assert import_router._assign_to_collection(cid, pid)["added"] is False
    assert feed.actions() == [("members_changed", cid)]


def test_migration_run_and_undo_announce_their_collections(client, db, feed):
    pid = _seed(db, "Alpha")
    run = migration_router._RunCollections()
    run.assign(pid, [["Diss", "Kapitel 2"]])
    (cid,) = run.created
    assert feed.actions() == [("created", cid), ("members_changed", cid)]

    feed.events.clear()
    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO migration_runs (run_id, created_paper_ids, attached_paper_ids, "
            "collections_json) VALUES ('r1', '[]', '[]', ?)",
            (json.dumps({"created": [cid]}),))
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/migration/undo", json={"run_id": "r1"}).status_code == 200
    assert feed.actions() == [("deleted", cid)]


@pytest.mark.parametrize("how", ["single", "bulk", "diagnosis"])
def test_deleting_an_item_announces_every_collection_it_left(client, db, feed, how):
    pid = _seed(db, "Alpha")
    other = _seed(db, "Beta")
    one = client.post("/api/collections", json={"name": "A", "paper_ids": [pid]}).json()["id"]
    two = client.post("/api/collections", json={"name": "B", "paper_ids": [pid, other]}).json()["id"]
    client.delete(f"/api/collections/{two}")    # a tombstone's frozen membership changes too
    client.post("/api/collections", json={"name": "C", "paper_ids": [other]})
    feed.events.clear()
    if how == "single":
        assert client.delete(f"/api/papers/{pid}").status_code == 200
    elif how == "bulk":
        assert client.post("/api/papers/bulk-delete", json={"paper_ids": [pid]}).status_code == 200
    else:
        resp = client.post("/api/diagnosis/fix", json={"action": "delete_item", "paper_id": pid})
        assert resp.status_code == 200, resp.text
    assert sorted(feed.actions()) == [("members_changed", one), ("members_changed", two)]


def test_duplicate_merge_keeps_the_collections_and_announces_them(client, db, feed):
    keep = _seed(db, "Alpha")
    twin = _seed(db, "Alpha Twin")
    both = client.post("/api/collections", json={"name": "Both", "paper_ids": [keep, twin]}).json()["id"]
    only = client.post("/api/collections", json={"name": "Only twin", "paper_ids": [twin]}).json()["id"]
    client.patch(f"/api/collections/{only}/items/{twin}", json={"note": "key source"})
    feed.events.clear()

    resp = client.post("/api/duplicates/merge", json={"keep_id": keep, "delete_ids": [twin]})
    assert resp.status_code == 200, resp.text
    lib = host_services._CoreLibraryApi()
    assert lib.get_collection_refs(only) == ["alpha"]
    assert lib.get_collection_refs(both) == ["alpha"]
    notes = {p["id"]: p.get("collection_note") for p in
             client.get(f"/api/papers?collection_id={only}").json()}
    assert notes == {keep: "key source"}
    assert sorted(feed.actions()) == sorted([("members_changed", both), ("members_changed", only)])


# ---------------------------------------------------------------------------
# The loader drops what an Add-on forgot
# ---------------------------------------------------------------------------

def test_unload_drops_a_forgotten_subscription():
    host = host_services._CoreLibraryApi()
    manifest = PluginManifest(id="forgetful", name="Forgetful", version="0.1.0")
    library = plugin_loader.AddonLibrary(host, "forgetful", manifest)
    before = library_events.listener_count()
    library.on_change(lambda e: None)
    stop = library.on_change(lambda e: None)
    stop()                                   # one it did clean up itself
    assert library_events.listener_count() == before + 1
    plugin_loader._drop_subscriptions("forgetful")
    assert library_events.listener_count() == before
    plugin_loader._drop_subscriptions("forgetful")    # idempotent
