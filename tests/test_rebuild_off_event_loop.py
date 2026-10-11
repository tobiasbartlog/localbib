"""LIB-B9: the category-folder rebuild never runs on the event loop.

With ``LINK_MODE=copy`` a full rebuild is O(library bytes); called straight
from an ``async def`` handler it froze every other request. The handlers now
hand it to the threadpool. A static guard covers every handler; a few live
requests show the rebuild really runs on a worker thread.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REBUILDS = {"_rebuild_all_symlinks", "_rebuild_category_folders"}
MODULES = sorted(REPO.glob("routers/*.py")) + [REPO / "validation_policy.py"]


def _direct_calls_in_async_defs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in REBUILDS):
                found.append(f"{path.name}:{node.lineno} {fn.name} calls {node.func.id}()")
    return found


def test_no_async_handler_calls_a_rebuild_directly():
    offenders = [hit for path in MODULES for hit in _direct_calls_in_async_defs(path)]
    assert offenders == [], "use `await run_in_threadpool(...)`:\n" + "\n".join(offenders)


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def rebuild_threads(monkeypatch):
    import routers.categories as categories_router
    import routers.papers as papers_router

    seen: list[bool] = []
    for mod in (papers_router, categories_router):
        for name in REBUILDS:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, lambda *_a, **_k: seen.append(_on_event_loop()))
    return seen


def _seed_paper(db) -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title) VALUES (?, ?, ?, ?)",
            ("hash-offloop", "", "", "Off the loop"))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_creating_and_deleting_a_category_rebuilds_off_the_loop(client, db, rebuild_threads):
    resp = client.post("/api/categories", json={"name": "OffLoopCat"})
    assert resp.status_code in (200, 201), resp.text
    cid = resp.json().get("id") or next(
        c["id"] for c in db.get_categories() if c["name"] == "OffLoopCat")
    assert client.delete(f"/api/categories/{cid}").status_code == 200
    assert rebuild_threads and not any(rebuild_threads)


def test_deleting_an_item_rebuilds_off_the_loop(client, db, rebuild_threads):
    pid = _seed_paper(db)
    assert client.delete(f"/api/papers/{pid}").status_code == 200
    assert rebuild_threads and not any(rebuild_threads)
