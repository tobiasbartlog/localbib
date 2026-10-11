"""The fakes in ``plugin_api.testing`` behave like the core's host services."""

from __future__ import annotations

import pytest

from plugin_api import CoreSettings, Permission
from plugin_api.testing import (
    DummyLlm,
    InMemoryLibrary,
    InMemorySettings,
    NullFiles,
    TempSqliteStorage,
    make_api,
)


def test_make_api_hands_over_only_declared_services():
    api = make_api([])
    assert (api.library, api.llm, api.files, api.storage) == (None, None, None, None)
    assert api.settings is not None and api.settings.core() is None
    assert api.api_version == 2

    full = make_api([p.value for p in Permission])
    assert isinstance(full.library, InMemoryLibrary)
    assert isinstance(full.llm, DummyLlm)
    # files/storage are reserved (2.2.0): never handed over, like the core.
    assert full.files is None and full.storage is None
    assert full.settings.core().ui_language == "en"


def test_reserved_permissions_grant_nothing_even_when_a_fake_is_passed():
    storage = TempSqliteStorage()
    try:
        api = make_api(["files", "storage"], files=NullFiles(), storage=storage)
        assert api.files is None and api.storage is None
        assert {"files", "storage"} <= {p.value for p in api.permissions}
    finally:
        storage.close()


def test_reserved_permissions_are_valid_but_not_gated():
    from plugin_api import GATED_SERVICES, PERMISSIONS

    for perm in (Permission.FILES, Permission.STORAGE):
        info = PERMISSIONS[perm]
        assert info.reserved and not info.enforced and info.service is None
    assert set(GATED_SERVICES) == {"library", "llm"}


def test_make_api_ignores_a_passed_service_without_its_permission():
    assert make_api(["network"], llm=DummyLlm("x")).llm is None


def test_make_api_rejects_unknown_permissions():
    with pytest.raises(ValueError):
        make_api(["camera"])


def test_make_api_records_registrations():
    api = make_api()
    api.routes.register_router("router")
    api.ui.notify("hi", "warn")
    assert api.ui.routers == ["router"] and api.ui.notifications == [("warn", "hi")]


def test_in_memory_library():
    lib = InMemoryLibrary([
        {"citekey": "a1", "title": "Graph methods", "abstract": "About graphs", "year": 2020},
        {"citekey": "b2", "title": "Other", "full_text": "Body"},
    ])
    assert lib.get_reference("a1")["title"] == "Graph methods"
    assert lib.get_reference("zz") is None
    assert [r["citekey"] for r in lib.search_references("graph")] == ["a1"]
    assert lib.get_abstract("a1") == "About graphs"
    assert lib.get_full_text("a1") is None and lib.get_full_text("b2") == "Body"
    assert "@article{a1," in lib.export_bibtex()
    events = []
    stop = lib.on_change(events.append)
    lib.add({"citekey": "c3"})
    stop()
    lib.add({"citekey": "d4"})
    assert events == [{"type": "changed", "citekey": "c3"}]


def test_in_memory_library_create_by_doi_dedups_by_doi():
    lib = InMemoryLibrary([{"citekey": "a1", "id": 7, "doi": "10.1/have"}], source="demo")
    have = lib.create_by_doi("https://doi.org/10.1/HAVE", title="ignored")
    assert have == {"doi": "10.1/have", "created": False, "paper_id": 7, "citekey": "a1", "pdf": "none"}
    new = lib.create_by_doi(" 10.1/New ", title="Fresh", authors=["Doe, J", "Roe, R"], year=2026)
    assert new["created"] is True and new["doi"] == "10.1/new" and new["pdf"] == "none"
    ref = lib.get_reference(new["citekey"])
    assert (ref["title"], ref["authors"], ref["import_source"]) == ("Fresh", "Doe, J; Roe, R", "demo")
    assert lib.create_by_doi("10.1/new")["paper_id"] == new["paper_id"]
    assert [c["doi"] for c in lib.created] == ["10.1/new"]
    with pytest.raises(ValueError):
        lib.create_by_doi("  ")


def test_make_api_gates_create_by_doi_on_library_write():
    read_only = make_api(["library.read"])
    with pytest.raises(PermissionError):
        read_only.library.create_by_doi("10.1/x")
    assert read_only.library.created == []

    lib = InMemoryLibrary()
    writer = make_api(["library.read", "library.write"], library=lib)
    assert writer.library is lib
    assert writer.library.create_by_doi("10.1/x")["created"] is True
    assert make_api(["library.write"], library=InMemoryLibrary()).library is None


def test_dummy_llm_answers_in_order_and_repeats_the_last():
    llm = DummyLlm(["one", "two"])
    answers = [llm.complete({"messages": []})["content"] for _ in range(3)]
    assert answers == ["one", "two", "two"]
    echo = DummyLlm(lambda r: r["messages"][0]["content"].upper())
    assert echo.complete({"messages": [{"role": "user", "content": "hi"}]}) == {"content": "HI"}


def test_dummy_llm_embeddings_are_off_by_default_and_deterministic_when_on():
    with pytest.raises(NotImplementedError):
        DummyLlm().embed(["x"])
    llm = DummyLlm(embeddings=True, dimensions=4)
    a, b, a2 = llm.embed(["alpha", "beta", "alpha"])
    assert len(a) == 4 and a == a2 and a != b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9
    assert llm.embed_model() == "dummy-embed"


def test_settings_keep_own_keys_and_gate_the_core_set():
    s = InMemorySettings({"k": 1})
    s.set("j", 2)
    # Numbers are stored as str, like the core keeps them on disk (2.2.0).
    assert (s.get("k"), s.get("j"), s.get("missing")) == ("1", "2", None)
    core = CoreSettings("me@example.org", "key", "de", "/lib")
    assert make_api(["settings.core"], core_settings=core).settings.core() == core
    assert make_api([], core_settings=core).settings.core() is None


def test_null_files_records_and_fires():
    files = NullFiles()
    seen = []
    stop = files.watch("notes", seen.append)
    files.fire("notes", {"path": "a.md"})
    stop()
    files.fire("notes", {"path": "b.md"})
    assert seen == [{"path": "a.md"}]


def test_temp_sqlite_storage_roundtrip(tmp_path):
    storage = TempSqliteStorage(tmp_path)
    db = storage.open_plugin_db("demo")
    db.execute("CREATE TABLE t (v TEXT)")
    db.execute("INSERT INTO t VALUES ('x')")
    db.commit()
    assert (tmp_path / "demo.db").is_file()
    with pytest.raises(ValueError):
        storage.open_plugin_db("../escape")
    storage.close()


def test_temp_sqlite_storage_cleans_its_own_folder():
    storage = TempSqliteStorage()
    storage.open_plugin_db("demo").execute("CREATE TABLE t (v TEXT)")
    folder = storage.directory
    storage.close()
    assert not folder.exists()


def test_in_memory_library_collections_seeded_with_tombstones():
    lib = InMemoryLibrary(
        [{"citekey": "a1", "title": "A"}, {"citekey": "b2", "title": "B"}],
        collections=[
            {"id": 3, "name": "paper x", "refs": ["b2", "a1", "gone"]},
            {"id": 5, "name": "Archive", "archived": True},
            {"id": 9, "name": "Old", "refs": ["a1"], "deleted_at": "2026-01-02 03:04:05"},
        ],
    )
    assert [c["id"] for c in lib.list_collections()] == [5, 9, 3]
    assert lib.get_collection(5)["archived"] is True
    old = lib.get_collection(9)
    assert (old["deleted"], old["deleted_at"], old["item_count"]) == (True, "2026-01-02 03:04:05", 1)
    # As in the core, a Collection only holds Items of the library.
    assert lib.get_collection_refs(3) == ["b2", "a1"]
    assert lib.get_collection(3)["item_count"] == 2
    bib = lib.export_collection_bibtex(3)
    assert bib.index("@article{b2,") < bib.index("@article{a1,")
    assert lib.export_collection_bibtex(5) == "\n"
    assert (lib.get_collection(42), lib.get_collection_refs(42)) == (None, [])


def test_in_memory_library_collection_helpers_fire_core_shaped_events():
    from plugin_api import COLLECTION_EVENT_ACTIONS

    lib = InMemoryLibrary([{"citekey": "a1"}])
    events = []
    lib.on_change(events.append)
    cid = lib.add_collection("X", refs=["a1"])
    lib.rename_collection(cid, "Y")
    lib.set_collection_description(cid, "why")
    lib.archive_collection(cid)
    lib.archive_collection(cid, archived=False)
    lib.set_collection_refs(cid, [])
    lib.delete_collection(cid)
    assert [e["action"] for e in events] == [
        "created", "members_changed", "renamed", "updated", "archived", "restored",
        "members_changed", "deleted"]
    assert all(e == {"type": "collection", "action": e["action"], "collection_id": cid} for e in events)
    assert set(e["action"] for e in events) == set(COLLECTION_EVENT_ACTIONS)
    with pytest.raises(ValueError):
        lib.emit_collection("exploded", cid)
