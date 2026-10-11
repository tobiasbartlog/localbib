"""Collections (Sammlungen, PRD #200): the rules, transport-free.

A Collection is a hand-curated list of Items for one purpose ("literature for
paper X") — distinct from a Category, which is thematic and often set by the
LLM (CONTEXT.md). This module owns what a Collection *means*; the router
``routers/collections.py`` persists and shapes responses (ADR-0006).

Two halves:

* **Pure rules** — name validation, id normalisation, the add/remove split and
  the membership diff later slices need for the re-import ("newly cited" /
  "no longer cited", #203).
* **Reads on a handed connection** — like ``services/pdf_folder_match``: the
  caller passes a ``sqlite3.Connection`` (``row_factory = sqlite3.Row``), this
  module only SELECTs. It never opens a connection and never writes.

Tombstone: deleting a Collection sets ``deleted_at``. Every user-facing read
(``get_collection``, ``list_collections``, ``collections_of_paper``) hides it
unless ``include_deleted=True`` is asked for explicitly; ``member_ids`` reads
the frozen membership of a tombstoned Collection too, so an Add-on that was
not running at deletion time can still read the last state (#204).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

# Archive filter values for ``list_collections``.
ARCHIVED_ACTIVE = "false"
ARCHIVED_ONLY = "true"
ARCHIVED_ALL = "all"
ARCHIVED_FILTERS = (ARCHIVED_ACTIVE, ARCHIVED_ONLY, ARCHIVED_ALL)

NAME_MAX = 200


class CollectionError(ValueError):
    """A rule violation, carrying the error code the SPA translates (ADR-0018)."""

    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def clean_name(name: Optional[str]) -> str:
    """Trimmed name; empty (or whitespace) is forbidden. Names need not be unique."""
    text = " ".join((name or "").split())
    if not text:
        raise CollectionError("error.collection_name_empty")
    return text[:NAME_MAX]


def clean_description(description: Optional[str]) -> str:
    return (description or "").strip()


def unique_ids(ids: Iterable) -> list[int]:
    """Request ids in first-seen order, duplicates dropped."""
    return list(dict.fromkeys(int(i) for i in ids or []))


def split_add(existing: Iterable[int], requested: Iterable[int]) -> tuple[list[int], list[int]]:
    """Adding is idempotent: ``(to_add, already)`` in request order."""
    have = set(existing)
    to_add, already = [], []
    for pid in unique_ids(requested):
        (already if pid in have else to_add).append(pid)
    return to_add, already


def membership_diff(current: Iterable[int], wanted: Iterable[int]) -> dict:
    """What a re-import would change: ``new`` (wanted, not yet in) and ``gone``
    (in, no longer wanted). A proposal only — nothing is ever removed from it
    automatically (PRD #200, user stories 23/24)."""
    cur = unique_ids(current)
    want = unique_ids(wanted)
    cur_set, want_set = set(cur), set(want)
    return {
        "new": [p for p in want if p not in cur_set],
        "gone": [p for p in cur if p not in want_set],
    }


# ---------------------------------------------------------------------------
# Reads (handed connection, SELECT only)
# ---------------------------------------------------------------------------

_SUMMARY_SQL = """
    SELECT c.id, c.name, c.description, c.archived_at, c.created_at,
           c.updated_at, c.deleted_at,
           COUNT(ci.paper_id) AS item_count,
           COALESCE(SUM(CASE WHEN p.filename IS NOT NULL AND p.filename != ''
                             THEN 1 ELSE 0 END), 0) AS pdf_count
    FROM collections c
    LEFT JOIN collection_items ci ON ci.collection_id = c.id
    LEFT JOIN papers p ON p.id = ci.paper_id
"""


def _summary(row: sqlite3.Row, include_deleted: bool) -> dict:
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "archived": row["archived_at"] is not None,
        "archived_at": row["archived_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "item_count": row["item_count"],
        "pdf_count": row["pdf_count"],
    }
    if include_deleted:
        out["deleted"] = row["deleted_at"] is not None
        out["deleted_at"] = row["deleted_at"]
    return out


def get_collection(conn: sqlite3.Connection, collection_id: int, *,
                   include_deleted: bool = False) -> Optional[dict]:
    """One Collection with item count and PDF coverage; ``None`` if missing
    (or tombstoned, unless ``include_deleted``)."""
    where = "WHERE c.id = ?" + ("" if include_deleted else " AND c.deleted_at IS NULL")
    row = conn.execute(_SUMMARY_SQL + where + " GROUP BY c.id", (collection_id,)).fetchone()
    return _summary(row, include_deleted) if row else None


def list_collections(conn: sqlite3.Connection, *, archived: str = ARCHIVED_ACTIVE,
                     include_deleted: bool = False) -> list[dict]:
    """Collections by name. ``archived``: ``"false"`` active only (default),
    ``"true"`` archived only, ``"all"`` both."""
    if archived not in ARCHIVED_FILTERS:
        raise CollectionError("error.collection_archived_filter_invalid")
    clauses = []
    if not include_deleted:
        clauses.append("c.deleted_at IS NULL")
    if archived == ARCHIVED_ACTIVE:
        clauses.append("c.archived_at IS NULL")
    elif archived == ARCHIVED_ONLY:
        clauses.append("c.archived_at IS NOT NULL")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        _SUMMARY_SQL + where + " GROUP BY c.id ORDER BY c.name COLLATE NOCASE, c.id"
    ).fetchall()
    return [_summary(r, include_deleted) for r in rows]


def member_ids(conn: sqlite3.Connection, collection_id: int) -> list[int]:
    """Membership resolution: Collection -> paper ids (oldest addition first).

    Reads tombstoned Collections too (their frozen state); callers that serve
    the user check ``get_collection`` first."""
    rows = conn.execute(
        "SELECT paper_id FROM collection_items WHERE collection_id = ? "
        "ORDER BY added_at, paper_id",
        (collection_id,),
    ).fetchall()
    return [r["paper_id"] for r in rows]


def member_notes(conn: sqlite3.Connection, collection_id: int) -> dict[int, str]:
    """``{paper_id: note}`` for one Collection."""
    rows = conn.execute(
        "SELECT paper_id, note FROM collection_items WHERE collection_id = ?",
        (collection_id,),
    ).fetchall()
    return {r["paper_id"]: r["note"] or "" for r in rows}


def collections_of_paper(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    """The (not deleted) Collections an Item belongs to, with its note there."""
    rows = conn.execute(
        "SELECT c.id, c.name, c.archived_at, ci.note, ci.added_at "
        "FROM collection_items ci JOIN collections c ON c.id = ci.collection_id "
        "WHERE ci.paper_id = ? AND c.deleted_at IS NULL "
        "ORDER BY c.name COLLATE NOCASE, c.id",
        (paper_id,),
    ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "archived": r["archived_at"] is not None,
         "note": r["note"] or "", "added_at": r["added_at"]}
        for r in rows
    ]


def existing_paper_ids(conn: sqlite3.Connection, paper_ids: list[int]) -> set[int]:
    if not paper_ids:
        return set()
    marks = ",".join("?" * len(paper_ids))
    rows = conn.execute(f"SELECT id FROM papers WHERE id IN ({marks})", paper_ids).fetchall()
    return {r["id"] for r in rows}
