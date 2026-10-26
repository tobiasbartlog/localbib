"""Router: RIS/BibTeX/CSV/ZIP export and export preview.

Serves:
  GET  /api/export/ris
  GET  /api/export/bibtex
  GET  /api/export/csv       (#202)
  GET  /api/export/zip       (#202: PDFs as <citekey>.pdf + .bib + .ris + fehlende_pdfs.txt)
  GET  /api/export/preview
  POST /api/export/{ris|bibtex|csv|zip}   (#212: the Items named in the body)

The POST form takes ``{ids: [...]}`` (long lists do not fit a URL), ignores
every filter and exports exactly those Items in Library order; unknown ids are
skipped. The GET forms take one scope: ``collection_id`` (a Collection, #202),
else ``search``, else ``category_id``, else the whole Library. A Collection is
resolved first, so an unknown or tombstoned one is a 404
``error.collection_not_found``, never an empty file. The text/bytes come from
the pure ``services.collection_export``; this router reads the rows, checks
which PDFs are on disk and writes the ZIP. RIS files are imported through the
migration wizard.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

import bib_export
from context import get_conn, safe_pdf_path
from literature_manager import Config, Database
from services import collection_export as export_svc
from services import library_collections

router = APIRouter()

_ZIP_SPOOL = 64 * 1024 * 1024  # in memory up to 64 MB, then a temp file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


class ExportIds(BaseModel):
    """Body of the POST form: exactly these Items (#212)."""
    ids: list[int] = Field(default_factory=list)


def _by_ids(ids: list[int]) -> list[dict]:
    wanted = set(ids)
    return [p for p in _get_db().get_all_papers() if p["id"] in wanted]


def _scoped(category_id: Optional[int], search: Optional[str],
            collection_id: Optional[int]) -> tuple[list[dict], Optional[dict], Optional[dict]]:
    """``(papers, collection, notes)`` for the requested scope; ``collection``
    and ``notes`` are ``None`` unless a Collection is the scope."""
    db = _get_db()
    if collection_id is not None:
        conn = get_conn()
        try:
            collection = library_collections.get_collection(conn, collection_id)
            if not collection:
                raise HTTPException(status_code=404, detail="error.collection_not_found")
            notes = library_collections.member_notes(conn, collection_id)
        finally:
            conn.close()
        return [p for p in db.get_all_papers() if p["id"] in notes], collection, notes
    if search:
        return db.search_papers(search), None, None
    if category_id:
        return db.get_papers_by_category(category_id), None, None
    return db.get_all_papers(), None, None


def _download(content: bytes, media_type: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _basename(collection: Optional[dict]) -> str:
    if collection:
        return export_svc.safe_basename(collection["name"])
    return export_svc.DEFAULT_BASENAME


def _csv_lookups(paper_ids: set[int]) -> dict:
    """Categories and Custom Fields for the CSV, read in one connection."""
    categories: dict[int, list[str]] = {}
    values: dict[int, dict[int, str]] = {}
    conn = get_conn()
    try:
        for r in conn.execute(
            "SELECT pc.paper_id, c.name FROM paper_categories pc "
            "JOIN categories c ON c.id = pc.category_id ORDER BY c.name COLLATE NOCASE"
        ).fetchall():
            if r["paper_id"] in paper_ids:
                categories.setdefault(r["paper_id"], []).append(r["name"])
        fields = [dict(r) for r in conn.execute(
            "SELECT id, name FROM custom_fields ORDER BY position, id"
        ).fetchall()]
        for r in conn.execute(
            "SELECT paper_id, field_id, value FROM paper_custom_values"
        ).fetchall():
            if r["paper_id"] in paper_ids:
                values.setdefault(r["paper_id"], {})[r["field_id"]] = r["value"] or ""
    finally:
        conn.close()
    return {"categories": categories, "custom_fields": fields, "custom_values": values}


def _pdf_paths(papers: list[dict]) -> dict[int, str]:
    """``{paper_id: path}`` for the Items whose PDF is really on disk."""
    found: dict[int, str] = {}
    for p in papers:
        if not p.get("filename"):
            continue
        try:
            found[p["id"]] = safe_pdf_path(p["filename"])
        except HTTPException:
            continue
    return found


# ---------------------------------------------------------------------------
# Builders: one per format, shared by the GET (scope) and POST (ids) forms
# ---------------------------------------------------------------------------

def _build_ris(papers, collection, notes):
    content = export_svc.ris_text(papers, Config.ALL_DIR)
    return _download(content.encode("utf-8"), "application/x-research-info-systems",
                     f"{_basename(collection)}.ris")


def _build_bibtex(papers, collection, notes):
    # Keys are stored and unique -- no export-local dedup needed.
    content = export_svc.bibtex_text(papers, **bib_export.options())
    return _download(content.encode("utf-8"), "application/x-bibtex",
                     f"{_basename(collection)}.bib")


def _build_csv(papers, collection, notes):
    """CSV for Excel: core metadata, categories, Custom Fields, Collection note."""
    lookups = _csv_lookups({p["id"] for p in papers})
    content = export_svc.csv_bytes(papers, notes=notes, **lookups)
    return _download(content, "text/csv; charset=utf-8", f"{_basename(collection)}.csv")


def _build_zip(papers, collection, notes):
    """The literature package: PDFs as <citekey>.pdf, .bib, .ris, fehlende_pdfs.txt."""
    base = _basename(collection)
    paths = _pdf_paths(papers)
    plan = export_svc.zip_plan(papers, paths.keys())

    spool = tempfile.SpooledTemporaryFile(max_size=_ZIP_SPOOL)
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{base}.bib", export_svc.bibtex_text(papers, **bib_export.options()))
        zf.writestr(f"{base}.ris", export_svc.ris_text(papers, Config.ALL_DIR))
        zf.writestr(export_svc.MISSING_PDFS_NAME, plan.missing_text)
        for paper_id, member in plan.pdfs:
            # PDFs are compressed already; storing them saves time, not space.
            zf.write(paths[paper_id], arcname=member, compress_type=zipfile.ZIP_STORED)
    spool.seek(0)

    def _chunks():
        try:
            while True:
                chunk = spool.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        _chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={base}.zip"},
    )


_BUILDERS = {"ris": _build_ris, "bibtex": _build_bibtex, "csv": _build_csv, "zip": _build_zip}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# Plain ``def``: reading rows and PDFs is blocking I/O (threadpool).

@router.get("/api/export/ris")
def export_ris(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    """Exportiert Eintraege im RIS-Format (kompatibel mit Citavi, Zotero, Mendeley)."""
    return _build_ris(*_scoped(category_id, search, collection_id))


@router.get("/api/export/bibtex")
def export_bibtex(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    """Exportiert Eintraege im BibTeX-Format (.bib)."""
    return _build_bibtex(*_scoped(category_id, search, collection_id))


@router.get("/api/export/csv")
def export_csv(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    return _build_csv(*_scoped(category_id, search, collection_id))


@router.get("/api/export/zip")
def export_zip(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    return _build_zip(*_scoped(category_id, search, collection_id))

@router.get("/api/export/preview")
async def export_preview(
    category_id: Optional[int] = Query(None),
    collection_id: Optional[int] = Query(None),
):
    """Anzahl, PDF-Anzahl und Kurzuebersicht der zu exportierenden Eintraege."""
    papers, collection, _ = _scoped(category_id, None, collection_id)
    preview = [
        {"id": p["id"], "title": p.get("title", ""), "authors": p.get("authors", ""), "year": p.get("year")}
        for p in papers
    ]
    out = {
        "count": len(preview),
        "pdf_count": sum(1 for p in papers if p.get("filename")),
        "papers": preview,
    }
    if collection:
        out["collection"] = {"id": collection["id"], "name": collection["name"]}
    return out


@router.post("/api/export/{fmt}")
def export_ids(fmt: str, body: ExportIds):
    """Exactly the Items in ``body.ids``, same files as the GET forms (#212)."""
    builder = _BUILDERS.get(fmt)
    if builder is None:
        raise HTTPException(status_code=404, detail="error.export_format_unknown")
    return builder(_by_ids(body.ids), None, None)
