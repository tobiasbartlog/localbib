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

import json
import logging
import os
from typing import List

import collection_membership as membership
import library_events as events
import services.duplicate_detection as _dup_svc
from context import get_conn
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from database import normalize_tags
from filenames import stored_pdf_path
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


def _union_tags(a, b) -> str:
    """Both tag arrays, keep first, no repeats (``papers.tags`` is a JSON array)."""
    def _load(value):
        text = normalize_tags(value)
        return json.loads(text) if text else []
    return normalize_tags(_load(a) + _load(b))


def _merge_notes(keep_note, del_note) -> str:
    """Keep both notes; nothing is added when they are equal or one is empty."""
    keep_note = (keep_note or "").strip()
    del_note = (del_note or "").strip()
    if not del_note or del_note == keep_note:
        return keep_note
    if not keep_note:
        return del_note
    return keep_note + "\n\n" + del_note


def _earlier_date(a, b) -> str:
    present = [d for d in (a, b) if d]
    return min(present) if present else ""


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

    Kategorien der geloeschten Paper werden auf das behaltene Paper uebertragen,
    ebenso ihre Sammlungen (samt Notiz).
    """
    conn = _get_conn()
    touched: list = []
    doomed_files: list = []   # removed only after the commit succeeded
    try:
        # Pruefen ob keep_id existiert
        keep = conn.execute(
            "SELECT id, filename, notes, tags, date_added FROM papers WHERE id = ?",
            (data.keep_id,),
        ).fetchone()
        if not keep:
            raise HTTPException(
                status_code=404, detail="error.keep_item_not_found"
            )

        keep_state = dict(keep)
        for del_id in data.delete_ids:
            if del_id == data.keep_id:
                continue

            row = conn.execute(
                "SELECT id, filename, file_hash, original_filename, ocr_text, "
                "page_count, notes, tags, date_added FROM papers WHERE id = ?",
                (del_id,),
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

            # Sammlungen uebernehmen; jede Sammlung des Duplikats aendert sich
            membership.adopt(conn, data.keep_id, del_id)
            touched += membership.holding(conn, [del_id])

            # Paper loeschen
            conn.execute("DELETE FROM papers WHERE id = ?", (del_id,))

            # Notizen, Tags und das fruehere Hinzufuegedatum gehen nicht verloren
            keep_state["notes"] = _merge_notes(keep_state["notes"], row["notes"])
            keep_state["tags"] = _union_tags(keep_state["tags"], row["tags"])
            keep_state["date_added"] = _earlier_date(
                keep_state["date_added"], row["date_added"])
            conn.execute(
                "UPDATE papers SET notes = ?, tags = ?, date_added = ? WHERE id = ?",
                (keep_state["notes"], keep_state["tags"],
                 keep_state["date_added"], data.keep_id),
            )

            # Datei: hat das behaltene Paper keine (leerer Name *oder* Datei
            # fehlt auf der Platte), uebernimmt es die des Duplikats (die Datei
            # bleibt in ALL_DIR, nur die Zeile wechselt). Geloescht wird die
            # Datei des Duplikats nach dem Commit nur, wenn die eigene da ist.
            keeper_has_file = stored_pdf_path(keep_state["filename"]) is not None
            dropped_has_file = stored_pdf_path(row["filename"]) is not None
            if row["filename"] and not keeper_has_file and (
                    dropped_has_file or not keep_state["filename"]):
                conn.execute(
                    "UPDATE papers SET file_hash = ?, filename = ?, "
                    "original_filename = ?, ocr_text = ?, page_count = ? WHERE id = ?",
                    (row["file_hash"], row["filename"], row["original_filename"],
                     row["ocr_text"], row["page_count"], data.keep_id),
                )
                keep_state["filename"] = row["filename"]
            elif (keeper_has_file and row["filename"]
                    and row["filename"] != keep_state["filename"]):
                doomed_files.append(os.path.join(Config.ALL_DIR, row["filename"]))

        conn.commit()
    finally:
        conn.close()

    for filepath in doomed_files:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logging.warning(f"Konnte Duplikat-Datei nicht loeschen: {e}")

    events.collection_changed(events.MEMBERS_CHANGED, *touched)
    await run_in_threadpool(_rebuild_all_symlinks, _get_db())
    return {"status": "ok", "kept": data.keep_id, "deleted": data.delete_ids}
