"""Router: duplicates domain.

Serves:
  GET  /api/duplicates         (detection — three strategies via service)
  POST /api/duplicates/merge   (destructive merge)

Pure move from ``webapp.py`` (Backend-Modularisierung #91). HTTP contract
(paths / methods / shapes / status codes) is bit-identical to the previous
webapp.py handlers.

Detection logic (title normalisation + duplicate grouping) lives in
``services.duplicate_detection`` (PURE, unit-tested). The router loads papers
from the DB, computes the ``has_file`` flag, and delegates grouping to the
service.

Merge stays fully in the handler: DB writes (re-point categories /
custom-values / references to survivor, delete losing row) and file deletion
are performed here EXACTLY as before — no behaviour change.

No import from ``webapp`` or any other router.
"""
from __future__ import annotations

import logging
import os
from typing import List

import services.duplicate_detection as _dup_svc
from context import get_conn
from fastapi import APIRouter, HTTPException
from literature_manager import Config, Database, _rebuild_all_symlinks
from pydantic import BaseModel

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing ``_get_conn()`` calls without modification.
_get_conn = get_conn


def _get_db() -> Database:
    return Database(Config.DB_PATH)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MergeRequest(BaseModel):
    keep_id: int
    delete_ids: List[int]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/duplicates")
async def find_duplicates():
    """Findet potentielle Duplikate in der Bibliothek.

    Strategien:
    1. Gleiche DOI
    2. Gleicher Titel (normalisiert)
    3. Sehr aehnlicher Titel (Levenshtein-aehnlich)
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, authors, year, doi, filename, file_hash FROM papers ORDER BY id"
        ).fetchall()
        papers = [dict(r) for r in rows]
    finally:
        conn.close()

    # Datei-Existenz pruefen (filesystem read stays in the router, not the pure service)
    for p in papers:
        fn = p.get("filename", "")
        if fn:
            p["has_file"] = os.path.exists(os.path.join(Config.ALL_DIR, fn))
        else:
            p["has_file"] = False

    groups = _dup_svc.find_duplicate_groups(papers)
    return {"groups": groups, "total_duplicates": sum(len(g["papers"]) for g in groups)}


@router.post("/api/duplicates/merge")
async def merge_duplicates(data: MergeRequest):
    """Merged Duplikate: Behaelt ein Paper, loescht die anderen.

    Kategorien der geloeschten Paper werden auf das behaltene Paper uebertragen.
    """
    conn = _get_conn()
    try:
        # Pruefen ob keep_id existiert
        keep = conn.execute(
            "SELECT id, filename FROM papers WHERE id = ?", (data.keep_id,)
        ).fetchone()
        if not keep:
            raise HTTPException(
                status_code=404, detail="Zu behaltendes Paper nicht gefunden"
            )

        for del_id in data.delete_ids:
            if del_id == data.keep_id:
                continue

            row = conn.execute(
                "SELECT id, filename FROM papers WHERE id = ?", (del_id,)
            ).fetchone()
            if not row:
                continue

            # Kategorien uebertragen
            cats = conn.execute(
                "SELECT category_id, confidence, assigned_by FROM paper_categories WHERE paper_id = ?",
                (del_id,),
            ).fetchall()
            for cat in cats:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_categories "
                        "(paper_id, category_id, confidence, assigned_by) "
                        "VALUES (?, ?, ?, ?)",
                        (data.keep_id, cat["category_id"], cat["confidence"], cat["assigned_by"]),
                    )
                except Exception:
                    pass

            # Custom-Fields uebertragen (nur wenn beim keep-Paper leer)
            custom_vals = conn.execute(
                "SELECT field_id, value FROM paper_custom_values "
                "WHERE paper_id = ? AND value != ''",
                (del_id,),
            ).fetchall()
            for cv in custom_vals:
                existing = conn.execute(
                    "SELECT value FROM paper_custom_values WHERE paper_id = ? AND field_id = ?",
                    (data.keep_id, cv["field_id"]),
                ).fetchone()
                if not existing or not existing["value"]:
                    conn.execute(
                        "INSERT OR REPLACE INTO paper_custom_values "
                        "(paper_id, field_id, value) VALUES (?, ?, ?)",
                        (data.keep_id, cv["field_id"], cv["value"]),
                    )

            # Referenzen umleiten
            conn.execute(
                "UPDATE paper_references SET matched_paper_id = ? WHERE matched_paper_id = ?",
                (data.keep_id, del_id),
            )

            # Paper loeschen
            conn.execute("DELETE FROM papers WHERE id = ?", (del_id,))

            # Datei loeschen
            filepath = os.path.join(Config.ALL_DIR, row["filename"])
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    logging.warning(f"Konnte Duplikat-Datei nicht loeschen: {e}")

        conn.commit()
    finally:
        conn.close()

    _rebuild_all_symlinks(_get_db())
    return {"status": "ok", "kept": data.keep_id, "deleted": data.delete_ids}
