"""Router: custom-fields CRUD and paper custom-value assignment.

Serves:
  GET    /api/custom-fields
  POST   /api/custom-fields
  PUT    /api/custom-fields/{field_id}
  DELETE /api/custom-fields/{field_id}
  PUT    /api/papers/{paper_id}/custom-values

Pure move from webapp.py (Backend-Modularisierung #83). No behaviour change.
All shared state (Config) comes from literature_manager; DB connections come
from context.get_conn().
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context import get_conn

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from webapp.py)
# ---------------------------------------------------------------------------

class CustomFieldCreate(BaseModel):
    name: str
    field_type: str = "text"  # text, number, progress, select
    options: str = ""


class CustomFieldUpdate(BaseModel):
    name: Optional[str] = None
    field_type: Optional[str] = None
    options: Optional[str] = None
    position: Optional[int] = None


class CustomValueSet(BaseModel):
    field_id: int
    value: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/custom-fields")
async def list_custom_fields():
    """Gibt alle benutzerdefinierten Felder zurueck."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM custom_fields ORDER BY position, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/custom-fields")
async def create_custom_field(data: CustomFieldCreate):
    """Erstellt ein neues benutzerdefiniertes Feld."""
    conn = get_conn()
    try:
        # Position = max + 1
        max_pos = conn.execute("SELECT COALESCE(MAX(position), 0) as m FROM custom_fields").fetchone()["m"]
        cursor = conn.execute(
            "INSERT INTO custom_fields (name, field_type, options, position) VALUES (?, ?, ?, ?)",
            (data.name, data.field_type, data.options, max_pos + 1),
        )
        conn.commit()
        field_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM custom_fields WHERE id = ?", (field_id,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Feld mit diesem Namen existiert bereits")
    finally:
        conn.close()


@router.put("/api/custom-fields/{field_id}")
async def update_custom_field(field_id: int, data: CustomFieldUpdate):
    """Aktualisiert ein benutzerdefiniertes Feld."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM custom_fields WHERE id = ?", (field_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Feld nicht gefunden")

        updates, vals = [], []
        for f in ["name", "field_type", "options", "position"]:
            v = getattr(data, f, None)
            if v is not None:
                updates.append(f"{f} = ?")
                vals.append(v)
        if updates:
            vals.append(field_id)
            conn.execute(f"UPDATE custom_fields SET {', '.join(updates)} WHERE id = ?", vals)
            conn.commit()

        row = conn.execute("SELECT * FROM custom_fields WHERE id = ?", (field_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/api/custom-fields/{field_id}")
async def delete_custom_field(field_id: int):
    """Loescht ein benutzerdefiniertes Feld und alle zugehoerigen Werte."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM paper_custom_values WHERE field_id = ?", (field_id,))
        conn.execute("DELETE FROM custom_fields WHERE id = ?", (field_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.put("/api/papers/{paper_id}/custom-values")
async def set_custom_value(paper_id: int, data: CustomValueSet):
    """Setzt den Wert eines benutzerdefinierten Feldes fuer ein Paper."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO paper_custom_values (paper_id, field_id, value) VALUES (?, ?, ?)",
            (paper_id, data.field_id, data.value),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}
