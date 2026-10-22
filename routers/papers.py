"""Router: papers core — CRUD, category assignment, PDF access, BibTeX, cite-key.

Serves:
  GET    /api/papers
  GET    /api/papers/lookup
  POST   /api/papers/bulk-delete
  POST   /api/papers/categories
  POST   /api/papers/categories/remove
  GET    /api/papers/{paper_id}
  PUT    /api/papers/{paper_id}
  DELETE /api/papers/{paper_id}
  POST   /api/papers/{paper_id}/categories
  DELETE /api/papers/{paper_id}/categories/{category_id}
  GET    /api/papers/{paper_id}/pdf
  POST   /api/papers/{paper_id}/open
  GET    /api/papers/{paper_id}/download
  GET    /api/papers/{paper_id}/bibtex
  PUT    /api/papers/{paper_id}/cite-key
  PUT    /api/papers/{paper_id}/notes

``GET /api/papers`` takes ``collection_id`` (#201) as one more filter, and
``GET /api/papers/{id}`` lists the Item's Collections with its note there,
and its PDF's Stand as ``pdf_modified`` (#229).

Moved from webapp.py (Backend-Modularisierung #85) without a behaviour change;
the additions above came later.

Route-order safety: literal/collection routes (bulk-delete) are registered
BEFORE the parametric /{paper_id} route so they are never shadowed by the
path parameter.

Shared resources come from context.get_conn() / context.safe_pdf_path();
cite-key and BibTeX logic come from the neutral helpers cite_key_generator /
bibtex_builder.  No import from webapp or any other router.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import List, Optional

import bibtex_builder
from cite_key_generator import base_key as _legacy_cite_key_base
import collection_membership as membership
import library_events as events
import pdf_annotations
from context import get_conn, safe_pdf_path
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    create_symlinks,
    generate_filename,
)
from pydantic import BaseModel, Field

from services import library_collections

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing `_get_conn()` calls without modification.
_get_conn = get_conn
_safe_pdf_path = safe_pdf_path


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from webapp.py)
# ---------------------------------------------------------------------------

class PaperCategoryAssign(BaseModel):
    category_id: int


class PaperUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    isbn: Optional[str] = None
    abstract: Optional[str] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None


class CiteKeyUpdate(BaseModel):
    cite_key: str


class NotesUpdate(BaseModel):
    notes: str


class BulkDeleteRequest(BaseModel):
    paper_ids: List[int]


class BulkCategoryRequest(BaseModel):
    paper_ids: List[int] = Field(min_length=1)
    category_id: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _cite_key(paper: dict) -> str:
    """Gespeicherter Cite Key; Legacy-Fallback fuer Zeilen vor dem Backfill."""
    return paper.get("cite_key") or _legacy_cite_key_base(
        paper.get("authors") or "", paper.get("year")
    )


# ---------------------------------------------------------------------------
# Endpoints
# NOTE: literal/collection routes FIRST, then parametric /{paper_id}.
# ---------------------------------------------------------------------------

@router.get("/api/papers")
async def list_papers(
    search: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    cite_keys: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    # Collection scope (#201): resolved first so a tombstoned or unknown
    # Collection is a 404, not an empty list that looks like a real result.
    members = None
    if collection_id is not None:
        conn = _get_conn()
        try:
            if not library_collections.get_collection(conn, collection_id):
                raise HTTPException(status_code=404, detail="error.collection_not_found")
            members = library_collections.member_notes(conn, collection_id)
        finally:
            conn.close()

    db = _get_db()
    if search:
        papers = db.search_papers(search)
    elif category_id:
        papers = db.get_papers_by_category(category_id)
    else:
        papers = db.get_all_papers()

    # Generic Cite-Key filter (comma-separated). The SPA uses this for the
    # Backs a plugin-side project filter (ADR-0004): the plugin fetches the
    # refs of a research project and passes them here — the core stays
    # plugin-agnostic.
    if cite_keys is not None:
        wanted = {k.strip() for k in cite_keys.split(",") if k.strip()}
        papers = [p for p in papers if _cite_key(p) in wanted]

    # Combinable with every other filter: it narrows whatever set they left.
    if members is not None:
        papers = [p for p in papers if p["id"] in members]
        for p in papers:
            p["collection_note"] = members[p["id"]]

    conn = _get_conn()
    try:
        for p in papers:
            cats = conn.execute(
                "SELECT c.id, c.name FROM paper_categories pc "
                "JOIN categories c ON pc.category_id = c.id "
                "WHERE pc.paper_id = ?",
                (p["id"],),
            ).fetchall()
            p["categories"] = [dict(c) for c in cats]

            # Custom field values
            cvs = conn.execute(
                "SELECT cf.id as field_id, cf.name, cf.field_type, pcv.value "
                "FROM custom_fields cf "
                "LEFT JOIN paper_custom_values pcv ON pcv.field_id = cf.id AND pcv.paper_id = ? "
                "ORDER BY cf.position",
                (p["id"],),
            ).fetchall()
            p["custom_values"] = {str(r["field_id"]): r["value"] or "" for r in cvs}
    finally:
        conn.close()

    return papers


LOOKUP_MAX = 50


@router.get("/api/papers/lookup")
async def lookup_papers(
    q: str = Query(""),
    keys: Optional[List[str]] = Query(None),
    limit: int = Query(20, ge=1, le=LOOKUP_MAX),
):
    """Light lookup for citations in notes: ``q`` searches title, authors,
    citekey and DOI; ``keys`` (repeatable, or comma-separated) resolves
    citekeys. Never the heavy ``/api/papers`` row (categories, fields)."""
    db = _get_db()
    if keys is not None:
        wanted = [k for part in keys for k in part.split(",")]
        return {"papers": db.lookup_papers(keys=wanted[:500])}
    return {"papers": db.lookup_papers(query=q, limit=limit)}


def _remove_pdf_file_if_any(filename: str) -> None:
    """Loescht die PDF-Datei eines Papers, falls vorhanden.

    Importierte Eintraege (BibTeX/DOI/Migration) haben ``filename == ""`` --
    ``os.path.join(Config.ALL_DIR, "")`` waere dann ``ALL_DIR`` selbst, ein
    Verzeichnis, an dem ``os.remove`` mit einem OSError scheitert. Daher hier
    zuerst auf eine nicht-leere Datei pruefen (wie der Bulk-Delete es schon
    tut), statt das ALL_DIR anzufassen."""
    if not filename:
        return
    fp = os.path.join(Config.ALL_DIR, filename)
    if os.path.exists(fp):
        try:
            os.remove(fp)
        except OSError as e:
            logging.warning("PDF-Datei beim Loeschen nicht entfernbar: %s", e)


@router.post("/api/papers/bulk-delete")
async def bulk_delete_papers(payload: BulkDeleteRequest):
    """Loescht mehrere Papers auf einmal (z. B. einen ganzen Import wieder
    entfernen oder eine Mehrfachauswahl). PDF-Dateien werden mit entfernt;
    Symlinks werden einmal am Ende neu aufgebaut."""
    deleted = 0
    conn = _get_conn()
    try:
        touched = membership.holding(conn, payload.paper_ids)
        for pid in payload.paper_ids:
            row = conn.execute("SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()
            if not row:
                continue
            conn.execute("DELETE FROM papers WHERE id = ?", (pid,))
            _remove_pdf_file_if_any(row["filename"])
            deleted += 1
        conn.commit()
    finally:
        conn.close()
    events.collection_changed(events.MEMBERS_CHANGED, *touched)
    if deleted:
        db = _get_db()
        await run_in_threadpool(_rebuild_all_symlinks, db)
    return {"deleted": deleted, "requested": len(payload.paper_ids)}


def _require_category(conn, category_id: int) -> None:
    if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone():
        raise HTTPException(status_code=404, detail="error.category_not_found")


def _require_category_and_papers(conn, category_id: int, paper_ids: List[int]) -> dict:
    """404 vor jedem Schreiben; liefert ``{paper_id: filename}``."""
    _require_category(conn, category_id)
    marks = ",".join("?" * len(paper_ids))
    filenames = {
        r["id"]: r["filename"]
        for r in conn.execute(f"SELECT id, filename FROM papers WHERE id IN ({marks})", paper_ids)
    }
    if len(filenames) != len(paper_ids):
        raise HTTPException(status_code=404, detail="error.item_not_found")
    return filenames


@router.post("/api/papers/categories")
async def bulk_assign_category(payload: BulkCategoryRequest):
    """Fuegt eine Kategorie zu mehreren Eintraegen hinzu (Drag & Drop, #199).

    INSERT OR IGNORE statt ``Database.assign_category`` (INSERT OR REPLACE):
    eine bestehende Zuordnung -- auch eine vom LLM -- bleibt unveraendert und
    wird als ``already_ids`` gemeldet, damit das Undo sie nie anfasst."""
    paper_ids = list(dict.fromkeys(payload.paper_ids))
    added, already = [], []
    conn = _get_conn()
    try:
        filenames = _require_category_and_papers(conn, payload.category_id, paper_ids)
        for pid in paper_ids:
            cur = conn.execute(
                "INSERT OR IGNORE INTO paper_categories "
                "(paper_id, category_id, confidence, assigned_by) VALUES (?, ?, 0.0, 'manual')",
                (pid, payload.category_id),
            )
            (added if cur.rowcount else already).append(pid)
        conn.commit()
    finally:
        conn.close()

    if added:
        db = _get_db()
        for pid in added:
            create_symlinks(db, pid, filenames[pid])
    return {"added_ids": added, "already_ids": already}


@router.post("/api/papers/categories/remove")
async def bulk_remove_category(payload: BulkCategoryRequest):
    """Undo-Gegenstueck zu ``bulk_assign_category``: entfernt genau die
    genannten Paare und baut die Symlinks einmal am Ende neu auf. Ein
    zwischenzeitlich geloeschter Eintrag ist kein Fehler: sein Paar ist per
    CASCADE schon weg, das Undo der uebrigen soll trotzdem greifen."""
    paper_ids = list(dict.fromkeys(payload.paper_ids))
    removed = []
    conn = _get_conn()
    try:
        _require_category(conn, payload.category_id)
        for pid in paper_ids:
            cur = conn.execute(
                "DELETE FROM paper_categories WHERE paper_id = ? AND category_id = ?",
                (pid, payload.category_id),
            )
            if cur.rowcount:
                removed.append(pid)
        conn.commit()
    finally:
        conn.close()

    if removed:
        await run_in_threadpool(_rebuild_all_symlinks, _get_db())
    return {"removed_ids": removed}


@router.get("/api/papers/{paper_id}")
async def get_paper(paper_id: int):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        paper = dict(row)

        cats = conn.execute(
            "SELECT c.id, c.name, c.parent_id, pc.confidence, pc.assigned_by "
            "FROM paper_categories pc "
            "JOIN categories c ON pc.category_id = c.id "
            "WHERE pc.paper_id = ?",
            (paper_id,),
        ).fetchall()
        paper["categories"] = [dict(c) for c in cats]

        # Custom field values
        cvs = conn.execute(
            "SELECT cf.id as field_id, cf.name, cf.field_type, cf.options, pcv.value "
            "FROM custom_fields cf "
            "LEFT JOIN paper_custom_values pcv ON pcv.field_id = cf.id AND pcv.paper_id = ? "
            "ORDER BY cf.position",
            (paper_id,),
        ).fetchall()
        paper["custom_fields"] = [
            {"field_id": r["field_id"], "name": r["name"], "field_type": r["field_type"],
             "options": r["options"], "value": r["value"] or ""}
            for r in cvs
        ]

        # Collections (#201) with this Item's note there; tombstones hidden.
        paper["collections"] = library_collections.collections_of_paper(conn, paper_id)
    finally:
        conn.close()

    # The file's Stand (#229), the one the annotation reading reports: the
    # preview URL carries it from the first load on.
    paper["pdf_modified"] = _pdf_stand(paper.get("filename"))
    return paper


def _pdf_stand(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    try:
        return pdf_annotations.file_state(_safe_pdf_path(filename))[2]
    # The file is gone or its stat unusable (odd name, mtime out of range):
    # the Item is still there, and PUT answers with this after its commit.
    except (HTTPException, OSError, ValueError, OverflowError):
        return None


@router.put("/api/papers/{paper_id}")
async def update_paper(paper_id: int, data: PaperUpdate):
    """Aktualisiert Paper-Metadaten manuell."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        paper = dict(row)

        update_fields = []
        update_values = []
        doi_changed = False
        for field in ["title", "authors", "year", "doi", "isbn", "abstract", "journal", "publisher"]:
            val = getattr(data, field, None)
            if val is not None:
                # Leere Strings -> NULL (Feld loeschen)
                if isinstance(val, str) and val.strip() == '':
                    val = None
                update_fields.append(f"{field} = ?")
                update_values.append(val)
                if field == "doi":
                    doi_changed = True

        # DOI manuell geaendert -> als verifiziert markieren
        if doi_changed:
            update_fields.append("doi_manually_verified = ?")
            update_values.append(1)

        if update_fields:
            update_values.append(paper_id)
            conn.execute(
                f"UPDATE papers SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                update_values,
            )
            conn.commit()
    finally:
        conn.close()

    # Dateiname aktualisieren wenn sich Titel/Autor/Jahr aendert
    if data.title or data.authors or data.year:
        merged = {
            "title": data.title or paper["title"],
            "authors": data.authors or paper["authors"],
            "year": data.year or paper["year"],
        }
        candidate = generate_filename(merged)
        old_filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
        if candidate != paper["filename"] and os.path.exists(old_filepath):
            target = os.path.join(Config.ALL_DIR, candidate)
            counter = 1
            base_candidate = candidate
            while os.path.exists(target) and target != old_filepath:
                name, ext = os.path.splitext(base_candidate)
                candidate = f"{name}_{counter}{ext}"
                target = os.path.join(Config.ALL_DIR, candidate)
                counter += 1
            try:
                os.rename(old_filepath, target)
                conn2 = _get_conn()
                try:
                    conn2.execute("UPDATE papers SET filename = ? WHERE id = ?",
                                  (candidate, paper_id))
                    conn2.commit()
                finally:
                    conn2.close()
                db = _get_db()
                await run_in_threadpool(_rebuild_all_symlinks, db)
            except OSError as e:
                logging.warning(f"Rename failed: {e}")

    # Aktualisiertes Paper zurueckgeben
    return await get_paper(paper_id)


@router.delete("/api/papers/{paper_id}")
async def delete_paper(paper_id: int):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")

        touched = membership.holding(conn, [paper_id])
        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        conn.commit()
        events.collection_changed(events.MEMBERS_CHANGED, *touched)

        # Datei optional loeschen
        _remove_pdf_file_if_any(row["filename"])
    finally:
        conn.close()

    db = _get_db()
    await run_in_threadpool(_rebuild_all_symlinks, db)
    return {"status": "ok"}


@router.post("/api/papers/{paper_id}/categories")
async def assign_paper_category(paper_id: int, data: PaperCategoryAssign):
    conn = _get_conn()
    try:
        paper = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        cat = conn.execute("SELECT id FROM categories WHERE id = ?", (data.category_id,)).fetchone()
        if not cat:
            raise HTTPException(status_code=404, detail="error.category_not_found")
    finally:
        conn.close()

    db = _get_db()
    db.assign_category(paper_id, data.category_id, confidence=0.0, assigned_by="manual")
    create_symlinks(db, paper_id, paper["filename"])
    return {"status": "ok"}


@router.delete("/api/papers/{paper_id}/categories/{category_id}")
async def remove_paper_category(paper_id: int, category_id: int):
    conn = _get_conn()
    try:
        paper = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="error.item_not_found")

        conn.execute(
            "DELETE FROM paper_categories WHERE paper_id = ? AND category_id = ?",
            (paper_id, category_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Symlinks neu erstellen
    db = _get_db()
    await run_in_threadpool(_rebuild_all_symlinks, db)
    return {"status": "ok"}


@router.get("/api/papers/{paper_id}/pdf")
async def get_paper_pdf(paper_id: int):
    """PDF fuer Browser-Vorschau (inline im iframe)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    # The reader may save annotations into this file at any time (#229): the
    # browser asks again every time (a 304 when nothing changed) instead of
    # showing the preview a cached copy.
    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=row["filename"],
        headers={"Content-Disposition": "inline", "Cache-Control": "no-cache"},
    )


@router.post("/api/papers/{paper_id}/open")
async def open_paper_pdf(paper_id: int):
    """Oeffnet PDF mit dem Standard-Programm (z.B. PDF-XChange) auf dem Server-Rechner."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    subprocess.Popen(["cmd", "/c", "start", "", filepath], shell=False)
    return {"status": "ok"}


@router.get("/api/papers/{paper_id}/download")
async def download_paper_pdf(paper_id: int):
    """PDF herunterladen."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=row["filename"],
    )


@router.get("/api/papers/{paper_id}/bibtex")
async def get_paper_bibtex(paper_id: int):
    """Gibt BibTeX-Eintrag fuer ein einzelnes Paper zurueck."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        paper = dict(row)
    finally:
        conn.close()

    key = _cite_key(paper)
    bibtex = bibtex_builder.format_entry(paper, key)
    return {"bibtex": bibtex, "key": key}


@router.put("/api/papers/{paper_id}/cite-key")
async def update_cite_key(paper_id: int, data: CiteKeyUpdate):
    """Setzt den Cite Key eines Papers. 409 bei Duplikat, 422 bei ungueltigem Key."""
    key = data.cite_key.strip()
    if not key or not re.fullmatch(r"[^\s{},%#\\\"]+", key):
        raise HTTPException(
            status_code=422,
            detail="error.invalid_cite_key",
        )

    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        duplicate = conn.execute(
            "SELECT id FROM papers WHERE cite_key = ? AND id != ?", (key, paper_id)
        ).fetchone()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={"code": "error.cite_key_taken",
                    "params": {"key": key, "id": duplicate["id"]}},
            )
        conn.execute(
            "UPDATE papers SET cite_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (key, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"id": paper_id, "cite_key": key}


@router.put("/api/papers/{paper_id}/notes")
async def update_notes(paper_id: int, data: NotesUpdate):
    """Speichert die Notizen eines Papers (Markdown, wie geschrieben).

    Bewusst ein eigener Endpunkt statt eines Feldes in PUT /api/papers/{id}:
    der generische Update benennt bei vorhandenem Titel/Autor/Jahr die
    PDF-Datei um und macht aus einem leeren String NULL. Beides will man beim
    Autosave nach jeder Tipppause nicht -- und das Leeren der Notizen muss als
    leer ankommen, nicht als "nicht gesetzt".
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.item_not_found")
        conn.execute(
            "UPDATE papers SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (data.notes, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"id": paper_id, "notes": data.notes}
