"""Router: papers core — CRUD, category assignment, PDF access, BibTeX, cite-key.

Serves:
  GET    /api/papers
  POST   /api/papers/bulk-delete
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

Pure move from webapp.py (Backend-Modularisierung #85). No behaviour change.
HTTP contract (paths / methods / shapes / status codes) is bit-identical to the
previous webapp.py handlers.

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
from context import get_conn, safe_pdf_path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    create_symlinks,
    generate_filename,
)
from pydantic import BaseModel

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


class BulkDeleteRequest(BaseModel):
    paper_ids: List[int]


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
):
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


@router.post("/api/papers/bulk-delete")
async def bulk_delete_papers(payload: BulkDeleteRequest):
    """Loescht mehrere Papers auf einmal (z. B. einen ganzen Import wieder
    entfernen oder eine Mehrfachauswahl). PDF-Dateien werden mit entfernt;
    Symlinks werden einmal am Ende neu aufgebaut."""
    deleted = 0
    conn = _get_conn()
    try:
        for pid in payload.paper_ids:
            row = conn.execute("SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()
            if not row:
                continue
            conn.execute("DELETE FROM papers WHERE id = ?", (pid,))
            fn = row["filename"]
            if fn:  # importierte Eintraege haben keine Datei -> ALL_DIR nicht anfassen
                fp = os.path.join(Config.ALL_DIR, fn)
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError as e:
                        logging.warning("PDF-Datei beim Bulk-Delete nicht entfernbar: %s", e)
            deleted += 1
        conn.commit()
    finally:
        conn.close()
    if deleted:
        db = _get_db()
        _rebuild_all_symlinks(db)
    return {"deleted": deleted, "requested": len(payload.paper_ids)}


@router.get("/api/papers/{paper_id}")
async def get_paper(paper_id: int):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
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
    finally:
        conn.close()

    return paper


@router.put("/api/papers/{paper_id}")
async def update_paper(paper_id: int, data: PaperUpdate):
    """Aktualisiert Paper-Metadaten manuell."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
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
                _rebuild_all_symlinks(db)
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
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")

        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        conn.commit()

        # Datei optional loeschen
        filepath = os.path.join(Config.ALL_DIR, row["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)
    finally:
        conn.close()

    db = _get_db()
    _rebuild_all_symlinks(db)
    return {"status": "ok"}


@router.post("/api/papers/{paper_id}/categories")
async def assign_paper_category(paper_id: int, data: PaperCategoryAssign):
    conn = _get_conn()
    try:
        paper = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        cat = conn.execute("SELECT id FROM categories WHERE id = ?", (data.category_id,)).fetchone()
        if not cat:
            raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
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
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")

        conn.execute(
            "DELETE FROM paper_categories WHERE paper_id = ? AND category_id = ?",
            (paper_id, category_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Symlinks neu erstellen
    db = _get_db()
    _rebuild_all_symlinks(db)
    return {"status": "ok"}


@router.get("/api/papers/{paper_id}/pdf")
async def get_paper_pdf(paper_id: int):
    """PDF fuer Browser-Vorschau (inline im iframe)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Kein PDF fuer dieses Paper")
    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=row["filename"],
        headers={"Content-Disposition": "inline"},
    )


@router.post("/api/papers/{paper_id}/open")
async def open_paper_pdf(paper_id: int):
    """Oeffnet PDF mit dem Standard-Programm (z.B. PDF-XChange) auf dem Server-Rechner."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Kein PDF fuer dieses Paper")
    subprocess.Popen(["cmd", "/c", "start", "", filepath], shell=False)
    return {"status": "ok"}


@router.get("/api/papers/{paper_id}/download")
async def download_paper_pdf(paper_id: int):
    """PDF herunterladen."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
    finally:
        conn.close()

    filepath = _safe_pdf_path(row["filename"])
    if not row["filename"] or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Kein PDF fuer dieses Paper")
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
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
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
            detail="Ungueltiger Cite Key (keine Leerzeichen, Klammern oder Kommas)",
        )

    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        duplicate = conn.execute(
            "SELECT id FROM papers WHERE cite_key = ? AND id != ?", (key, paper_id)
        ).fetchone()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail=f"Cite Key '{key}' wird bereits von Paper {duplicate['id']} verwendet",
            )
        conn.execute(
            "UPDATE papers SET cite_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (key, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"id": paper_id, "cite_key": key}
