"""Router: categories CRUD and LLM-based category suggestion.

Serves:
  GET    /api/categories
  GET    /api/categories/tree
  POST   /api/categories
  POST   /api/categories/suggest
  PUT    /api/categories/{category_id}
  DELETE /api/categories/{category_id}
  GET    /api/categories/{category_id}/papers

Pure move from webapp.py (Backend-Modularisierung #81). No behaviour change.
Literal paths (/tree, /suggest) are registered BEFORE the parametric
/{category_id} route so they are never shadowed by the path parameter.
All shared state (Config, Database) comes from literature_manager; DB
connections come from context.get_conn().

#86: _llm_suggest_category moved to services.metadata_extraction as a pure
function; this router obtains the client via llm_for() and delegates.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context import get_conn
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    _rebuild_category_folders,
)
from llm_client import llm_for
from services.metadata_extraction import llm_suggest_category as _service_llm_suggest_category

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from webapp.py)
# ---------------------------------------------------------------------------

class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None
    description: str = ""
    keywords: str = ""


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = -1  # -1 = not changed, None = root, int = new parent
    description: Optional[str] = None
    keywords: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _llm_suggest_category(name: str, existing_categories: list) -> dict:
    """Nutzt das LLM um Keywords und Beschreibung fuer eine Kategorie vorzuschlagen.

    Thin wrapper: delegates to services.metadata_extraction.llm_suggest_category (#86).
    """
    if not Config.KICONNECT_API_KEY:
        return {}
    return _service_llm_suggest_category(llm_for("category_suggest"), name, existing_categories)


# ---------------------------------------------------------------------------
# Endpoints — literal paths first, then parametric (route-order safety)
# ---------------------------------------------------------------------------

@router.get("/api/categories")
async def list_categories():
    db = _get_db()
    return db.get_categories()


@router.get("/api/categories/tree")
async def get_category_tree():
    db = _get_db()
    categories = db.get_categories()

    conn = get_conn()
    try:
        count_rows = conn.execute(
            "SELECT category_id, COUNT(*) as cnt FROM paper_categories GROUP BY category_id"
        ).fetchall()
    finally:
        conn.close()

    counts = {r["category_id"]: r["cnt"] for r in count_rows}

    by_id = {}
    for c in categories:
        by_id[c["id"]] = {
            **c,
            "children": [],
            "paper_count": counts.get(c["id"], 0),
        }

    roots = []
    for c in categories:
        node = by_id[c["id"]]
        if c["parent_id"] is None:
            roots.append(node)
        elif c["parent_id"] in by_id:
            by_id[c["parent_id"]]["children"].append(node)

    return roots


@router.post("/api/categories")
async def create_category(data: CategoryCreate):
    db = _get_db()
    cat_id = db.add_category(data.name, data.parent_id, data.description, data.keywords)
    _rebuild_category_folders(db)
    return {"id": cat_id, "status": "ok"}


@router.post("/api/categories/suggest")
async def suggest_category_details(data: CategoryCreate):
    """LLM-Vorschlag fuer Keywords und Beschreibung einer Kategorie."""
    db = _get_db()
    categories = db.get_categories()
    suggestion = _llm_suggest_category(data.name, categories)
    return suggestion


@router.put("/api/categories/{category_id}")
async def update_category(category_id: int, data: CategoryUpdate):
    conn = get_conn()
    try:
        existing = conn.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")

        updates = []
        values = []
        for field in ["name", "description", "keywords"]:
            val = getattr(data, field, None)
            if val is not None:
                updates.append(f"{field} = ?")
                values.append(val)

        # parent_id: -1 means unchanged, None means root, int means new parent
        if data.parent_id != -1:
            # Prevent circular: cannot set parent to self or a descendant
            if data.parent_id == category_id:
                raise HTTPException(status_code=400, detail="Kategorie kann nicht eigene Oberkategorie sein")
            if data.parent_id is not None:
                # Check descendant loop
                check_id = data.parent_id
                while check_id is not None:
                    row = conn.execute("SELECT parent_id FROM categories WHERE id = ?", (check_id,)).fetchone()
                    if not row:
                        break
                    if row["parent_id"] == category_id:
                        raise HTTPException(status_code=400, detail="Zirkulaere Verschachtelung nicht erlaubt")
                    check_id = row["parent_id"]
            updates.append("parent_id = ?")
            values.append(data.parent_id)

        if updates:
            values.append(category_id)
            conn.execute(
                f"UPDATE categories SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()
    finally:
        conn.close()

    db = _get_db()
    _rebuild_category_folders(db)
    return {"status": "ok"}


@router.delete("/api/categories/{category_id}")
async def delete_category(category_id: int):
    conn = get_conn()
    try:
        existing = conn.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")

        # Kinder-Kategorien auch loeschen
        children = conn.execute(
            "SELECT id FROM categories WHERE parent_id = ?", (category_id,)
        ).fetchall()
        for child in children:
            conn.execute("DELETE FROM categories WHERE id = ?", (child["id"],))

        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
    finally:
        conn.close()

    db = _get_db()
    _rebuild_category_folders(db)
    _rebuild_all_symlinks(db)
    return {"status": "ok"}


@router.get("/api/categories/{category_id}/papers")
async def get_category_papers(category_id: int):
    db = _get_db()
    return db.get_papers_by_category(category_id)
