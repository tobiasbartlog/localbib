"""Router: Collections (Sammlungen, PRD #200 / #201).

Serves:
  GET    /api/collections                      list (?archived=false|true|all)
  POST   /api/collections                      create (optionally with paper_ids)
  GET    /api/collections/{id}                 detail (count, PDF coverage)
  PATCH  /api/collections/{id}                 name, description, archived
  DELETE /api/collections/{id}                 tombstone — never deletes Items/PDFs
  POST   /api/collections/{id}/items           add {paper_ids} (idempotent)
  DELETE /api/collections/{id}/items           remove {paper_ids}
  PATCH  /api/collections/{id}/items/{paper_id}  the note of one Item there
  POST   /api/collections/{id}/items/by-doi    take a DOI into it (#202): the
                                               DOI intake of ADR-0016, then assign

The rules live in ``services/library_collections``; this module persists and
shapes responses (ADR-0006). Errors are codes (ADR-0018). A tombstoned
Collection answers 404 ``error.collection_not_found`` everywhere here — its
membership rows stay for the Add-on Contract (#204).

Every write announces itself on the library change feed (``library_events``,
``LibraryApi.on_change``) after its commit, and only when something changed.
"""

from __future__ import annotations

from typing import List, Optional, Union

import collection_membership as membership
import library_events as events
import paper_ingest
from context import get_conn
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import library_collections as svc

router = APIRouter()


class CollectionCreate(BaseModel):
    name: str = ""
    description: str = ""
    paper_ids: List[int] = Field(default_factory=list)


class CollectionPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None


class PaperIdsBody(BaseModel):
    paper_ids: List[int] = Field(min_length=1)


class ItemNoteBody(BaseModel):
    note: str = ""


class ItemByDoiBody(BaseModel):
    """An external neighbour from the citation network: the DOI is identity,
    the rest is what the graph already knows (enrichment fills the gaps)."""

    doi: str
    title: str = ""
    authors: Union[List[str], str, None] = None
    year: Optional[int] = None
    journal: str = ""
    abstract: str = ""


# The writes are shared with the import routers (#203) and live in the neutral
# ``collection_membership`` module; these aliases keep the handlers readable.
_fail = membership.fail
_require = membership.require
_add_items = membership.add_items


@router.get("/api/collections")
async def list_collections(archived: str = Query(svc.ARCHIVED_ACTIVE)):
    conn = get_conn()
    try:
        try:
            return svc.list_collections(conn, archived=archived)
        except svc.CollectionError as err:
            _fail(err)
    finally:
        conn.close()


@router.post("/api/collections", status_code=201)
async def create_collection(body: CollectionCreate):
    conn = get_conn()
    try:
        cid = membership.create(conn, body.name, body.description)
        result = _add_items(conn, cid, body.paper_ids) if body.paper_ids else {
            "added_ids": [], "already_ids": []}
        conn.commit()
        out = svc.get_collection(conn, cid)
    finally:
        conn.close()
    events.collection_changed(events.CREATED, cid)
    if result["added_ids"]:
        events.collection_changed(events.MEMBERS_CHANGED, cid)
    out.update(result)
    return out


@router.get("/api/collections/{collection_id}")
async def get_collection(collection_id: int):
    conn = get_conn()
    try:
        return _require(conn, collection_id)
    finally:
        conn.close()


@router.patch("/api/collections/{collection_id}")
async def update_collection(collection_id: int, body: CollectionPatch):
    conn = get_conn()
    try:
        before = _require(conn, collection_id)
        sets, values, changed = [], [], []
        try:
            if body.name is not None:
                sets.append("name = ?")
                values.append(svc.clean_name(body.name))
                if values[-1] != before["name"]:
                    changed.append(events.RENAMED)
        except svc.CollectionError as err:
            _fail(err)
        if body.description is not None:
            sets.append("description = ?")
            values.append(svc.clean_description(body.description))
            if values[-1] != before["description"]:
                changed.append(events.UPDATED)
        if body.archived is True:
            # Archiving twice keeps the first date.
            sets.append("archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP)")
            if not before["archived"]:
                changed.append(events.ARCHIVED)
        elif body.archived is False:
            sets.append("archived_at = NULL")
            if before["archived"]:
                changed.append(events.RESTORED)
        if sets:
            conn.execute(
                f"UPDATE collections SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (*values, collection_id),
            )
            conn.commit()
        out = svc.get_collection(conn, collection_id)
    finally:
        conn.close()
    for action in changed:
        events.collection_changed(action, collection_id)
    return out


@router.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: int):
    """Tombstone: the Collection leaves every list; Items, PDFs and the frozen
    membership stay."""
    conn = get_conn()
    try:
        found = _require(conn, collection_id)
        membership.tombstone(conn, collection_id)
        conn.commit()
    finally:
        conn.close()
    events.collection_changed(events.DELETED, collection_id)
    return {"status": "ok", "id": collection_id, "item_count": found["item_count"]}


@router.post("/api/collections/{collection_id}/items")
async def add_items(collection_id: int, body: PaperIdsBody):
    conn = get_conn()
    try:
        _require(conn, collection_id)
        result = _add_items(conn, collection_id, body.paper_ids)
        conn.commit()
    finally:
        conn.close()
    if result["added_ids"]:
        events.collection_changed(events.MEMBERS_CHANGED, collection_id)
    return result


@router.delete("/api/collections/{collection_id}/items")
async def remove_items(collection_id: int, body: PaperIdsBody):
    """Removes the Items from the Collection only — never from the Library."""
    conn = get_conn()
    try:
        _require(conn, collection_id)
        removed = membership.remove_items(conn, collection_id, body.paper_ids)
        conn.commit()
    finally:
        conn.close()
    if removed:
        events.collection_changed(events.MEMBERS_CHANGED, collection_id)
    return {"removed_ids": removed}


@router.patch("/api/collections/{collection_id}/items/{paper_id}")
async def update_item_note(collection_id: int, paper_id: int, body: ItemNoteBody):
    note = body.note.strip()
    conn = get_conn()
    try:
        _require(conn, collection_id)
        cur = conn.execute(
            "UPDATE collection_items SET note = ? WHERE collection_id = ? AND paper_id = ?",
            (note, collection_id, paper_id),
        )
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="error.collection_item_not_found")
        conn.commit()
    finally:
        conn.close()
    return {"collection_id": collection_id, "paper_id": paper_id, "note": note}


@router.post("/api/collections/{collection_id}/items/by-doi")
def add_item_by_doi(collection_id: int, body: ItemByDoiBody):
    """One click from the network: create the Item (or find it) through the
    DOI intake (``paper_ingest``, ADR-0016 — enrichment and best-effort OA
    PDF included), then assign it. Answers what happened: ``created`` (new in
    the Library) vs. existing, ``added`` vs. ``already`` in the Collection,
    and ``has_pdf``. Plain ``def``: the intake is blocking network I/O."""
    if not paper_ingest.normalize_doi(body.doi):
        raise HTTPException(status_code=422, detail="error.doi_empty")
    conn = get_conn()
    try:
        _require(conn, collection_id)
    finally:
        conn.close()
    item = body.model_dump()
    item["source"] = paper_ingest.DOI_SOURCE
    result = paper_ingest.intake_by_doi([item])[0]
    if not result.get("paper_id"):
        raise HTTPException(status_code=502, detail="error.doi_intake_failed")
    pid = result["paper_id"]
    conn = get_conn()
    try:
        added = _add_items(conn, collection_id, [pid])
        conn.commit()
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if added["added_ids"]:
        events.collection_changed(events.MEMBERS_CHANGED, collection_id)
    return {
        "paper_id": pid,
        "doi": result["doi"],
        "citekey": result.get("citekey") or "",
        "created": bool(result["created"]),
        "added": bool(added["added_ids"]),
        "has_pdf": bool(row and row["filename"]),
    }
