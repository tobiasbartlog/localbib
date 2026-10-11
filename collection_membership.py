"""Collection membership writes, shared by every router that fills a Collection.

Neutral helper like ``paper_ingest`` (ADR-0006: a router never imports another
router). The Collections router adds Items by hand, the BibTeX import and the
smart upload assign their Items to a target Collection (#203), and the
migration turns an export's collections into Collections — all four write the
same rows, so the writes live here once.

Every function works on a **handed connection** and never commits: the caller
owns the transaction (and therefore the persistence, ADR-0006). The rules —
name validation, the idempotent add split, the re-import diff — stay in the
pure ``services.library_collections``; this module only applies them.

Errors are ``HTTPException`` with an error code (ADR-0018), the same contract
``paper_ingest`` keeps, because every caller is a router handler.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

from fastapi import HTTPException

from services import library_collections as svc


@dataclass
class Target:
    """Where an import puts its Items: an existing Collection (``id`` set) or
    a new one by ``name`` that ``open_target`` creates when the first Item is
    assigned — so a request that fails validation never leaves an empty
    Collection behind."""

    id: Optional[int]
    name: str
    created: bool = False


def fail(err: svc.CollectionError):
    raise HTTPException(status_code=err.status, detail=err.code)


def require(conn: sqlite3.Connection, collection_id: int) -> dict:
    """The (not tombstoned) Collection, or 404 ``error.collection_not_found``."""
    found = svc.get_collection(conn, collection_id)
    if not found:
        raise HTTPException(status_code=404, detail="error.collection_not_found")
    return found


def touch(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute(
        "UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (collection_id,),
    )


def create(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    """Insert a Collection; ``name`` is validated (422 when empty)."""
    try:
        clean = svc.clean_name(name)
    except svc.CollectionError as err:
        fail(err)
    cur = conn.execute(
        "INSERT INTO collections (name, description) VALUES (?, ?)",
        (clean, svc.clean_description(description)),
    )
    return cur.lastrowid


def add_items(conn: sqlite3.Connection, collection_id: int,
              paper_ids: Iterable[int]) -> dict:
    """Insert the missing pairs (idempotent); unknown Items are a 404 before
    any write. Returns ``{added_ids, already_ids}`` in request order."""
    wanted = svc.unique_ids(paper_ids)
    if len(svc.existing_paper_ids(conn, wanted)) != len(wanted):
        raise HTTPException(status_code=404, detail="error.item_not_found")
    to_add, already = svc.split_add(svc.member_ids(conn, collection_id), wanted)
    for pid in to_add:
        conn.execute(
            "INSERT OR IGNORE INTO collection_items (collection_id, paper_id) VALUES (?, ?)",
            (collection_id, pid),
        )
    if to_add:
        touch(conn, collection_id)
    return {"added_ids": to_add, "already_ids": already}


def remove_items(conn: sqlite3.Connection, collection_id: int,
                 paper_ids: Iterable[int]) -> list[int]:
    """Drop the pairs — the Items stay in the Library. Returns what went."""
    removed = []
    for pid in svc.unique_ids(paper_ids):
        cur = conn.execute(
            "DELETE FROM collection_items WHERE collection_id = ? AND paper_id = ?",
            (collection_id, pid),
        )
        if cur.rowcount:
            removed.append(pid)
    if removed:
        touch(conn, collection_id)
    return removed


def check_target(conn: sqlite3.Connection, collection_id: Optional[int],
                 new_name: Optional[str]) -> Optional[Target]:
    """Validate an import's target before anything is written.

    ``collection_id`` wins when both are given; ``None`` for both means "no
    Collection" — the import behaves exactly as before #203."""
    if collection_id is not None:
        found = require(conn, collection_id)
        return Target(id=found["id"], name=found["name"])
    if new_name is not None:
        try:
            return Target(id=None, name=svc.clean_name(new_name))
        except svc.CollectionError as err:
            fail(err)
    return None


def open_target(conn: sqlite3.Connection, target: Target) -> Target:
    """Create the new Collection of a checked target (no-op for an existing one)."""
    if target.id is None:
        target.id = create(conn, target.name)
        target.created = True
    return target


def tombstone(conn: sqlite3.Connection, collection_id: int) -> bool:
    """Delete a Collection the way the user does: a tombstone, membership
    frozen for the Add-on Contract. ``False`` if it was gone already."""
    cur = conn.execute(
        "UPDATE collections SET deleted_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (collection_id,),
    )
    return bool(cur.rowcount)


def holding(conn: sqlite3.Connection, paper_ids: Iterable[int]) -> list[int]:
    """Every Collection (tombstones included) that holds one of these Items.

    Read it *before* deleting Items: the cascade then changes these
    Collections' membership, which the change feed (``library_events``) must
    announce after the commit."""
    ids = svc.unique_ids(paper_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT DISTINCT collection_id FROM collection_items WHERE paper_id IN ({marks}) "
        "ORDER BY collection_id",
        ids,
    ).fetchall()
    return [r["collection_id"] for r in rows]


def adopt(conn: sqlite3.Connection, keep_id: int, from_id: int) -> list[int]:
    """A duplicate merge: ``keep_id`` joins every Collection ``from_id`` was
    in (note and date kept; where both were in, ``keep_id``'s row wins).
    Returns the Collections ``keep_id`` newly joined."""
    rows = conn.execute(
        "SELECT collection_id FROM collection_items ci WHERE paper_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM collection_items k WHERE k.collection_id = ci.collection_id "
        "AND k.paper_id = ?)",
        (from_id, keep_id),
    ).fetchall()
    joined = [r["collection_id"] for r in rows]
    conn.execute(
        "INSERT OR IGNORE INTO collection_items (collection_id, paper_id, note, added_at) "
        "SELECT collection_id, ?, note, added_at FROM collection_items WHERE paper_id = ?",
        (keep_id, from_id),
    )
    return joined


def item_briefs(conn: sqlite3.Connection, paper_ids: list[int]) -> list[dict]:
    """``{paper_id, title, cite_key, year}`` per id, in the given order — what
    a suggestion list shows (e.g. the re-import's "no longer cited")."""
    if not paper_ids:
        return []
    marks = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT id, title, cite_key, year FROM papers WHERE id IN ({marks})",
        list(paper_ids),
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [
        {"paper_id": pid, "title": by_id[pid]["title"] or "",
         "cite_key": by_id[pid]["cite_key"] or "", "year": by_id[pid]["year"]}
        for pid in paper_ids if pid in by_id
    ]
