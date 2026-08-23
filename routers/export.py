"""Router: RIS/BibTeX export, export preview, and RIS import.

Serves:
  GET  /api/export/ris
  GET  /api/export/bibtex
  GET  /api/export/preview
  POST /api/import/ris

Pure move from webapp.py (Backend-Modularisierung #82). No behaviour change.
Output is byte-identical to the previous webapp.py handlers.
Shared state (Config, Database) comes from literature_manager; the cite-key
helper is imported from cite_key_generator (its existing neutral home).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, File, Query, UploadFile
from starlette.responses import StreamingResponse

import bibtex_builder
from cite_key_generator import base_key as _legacy_cite_key_base
from literature_manager import Config, Database

router = APIRouter()


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
# ---------------------------------------------------------------------------

@router.get("/api/export/ris")
async def export_ris(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
):
    """Exportiert Paper im RIS-Format (kompatibel mit Citavi, Zotero, Mendeley)."""
    db = _get_db()
    if search:
        papers = db.search_papers(search)
    elif category_id:
        papers = db.get_papers_by_category(category_id)
    else:
        papers = db.get_all_papers()

    lines = []
    for p in papers:
        lines.append("TY  - JOUR")
        if p.get("title"):
            lines.append(f"T1  - {p['title']}")
        if p.get("authors"):
            for author in re.split(r"[;]", p["authors"]):
                author = author.strip()
                if author:
                    lines.append(f"AU  - {author}")
        if p.get("year"):
            lines.append(f"PY  - {p['year']}///")
        if p.get("doi"):
            lines.append(f"DO  - {p['doi']}")
        if p.get("journal"):
            lines.append(f"JO  - {p['journal']}")
        if p.get("abstract"):
            lines.append(f"AB  - {p['abstract']}")
        if p.get("publisher"):
            lines.append(f"PB  - {p['publisher']}")
        if p.get("isbn"):
            lines.append(f"SN  - {p['isbn']}")
        if p.get("filename"):
            filepath = os.path.join(Config.ALL_DIR, p["filename"])
            lines.append(f"L1  - file://{filepath}")
        lines.append("ER  - ")
        lines.append("")

    content = "\n".join(lines)
    buffer = io.BytesIO(content.encode("utf-8"))

    return StreamingResponse(
        buffer,
        media_type="application/x-research-info-systems",
        headers={"Content-Disposition": "attachment; filename=literatur_export.ris"},
    )


@router.get("/api/export/bibtex")
async def export_bibtex(
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
):
    """Exportiert Paper im BibTeX-Format (.bib)."""
    db = _get_db()
    if search:
        papers = db.search_papers(search)
    elif category_id:
        papers = db.get_papers_by_category(category_id)
    else:
        papers = db.get_all_papers()

    # Keys sind gespeichert und eindeutig — kein Export-lokales Dedup mehr noetig.
    entries = [bibtex_builder.format_entry(p, _cite_key(p)) for p in papers]
    content = "\n\n".join(entries) + "\n"
    buffer = io.BytesIO(content.encode("utf-8"))

    return StreamingResponse(
        buffer,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": "attachment; filename=literatur_export.bib"},
    )


@router.get("/api/export/preview")
async def export_preview(
    category_id: Optional[int] = Query(None),
):
    """Gibt Anzahl und Kurzuebersicht der zu exportierenden Paper zurueck."""
    db = _get_db()
    if category_id:
        papers = db.get_papers_by_category(category_id)
    else:
        papers = db.get_all_papers()

    preview = [
        {"id": p["id"], "title": p.get("title", ""), "authors": p.get("authors", ""), "year": p.get("year")}
        for p in papers
    ]
    return {"count": len(preview), "papers": preview}


@router.post("/api/import/ris")
async def import_ris(file: UploadFile = File(...)):
    """Importiert Paper aus einer RIS-Datei (Citavi, Zotero, Mendeley)."""
    db = _get_db()
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    entries = []
    current = {}

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue

        if len(line) >= 6 and line[2:6] == "  - ":
            tag = line[:2].strip()
            value = line[6:].strip()

            if tag == "TY":
                current = {"type": value}
            elif tag == "ER":
                if current and current.get("title"):
                    entries.append(current)
                current = {}
            elif tag in ("T1", "TI"):
                current["title"] = value
            elif tag in ("AU", "A1"):
                current.setdefault("authors", []).append(value)
            elif tag in ("PY", "Y1"):
                year_match = re.search(r"(\d{4})", value)
                if year_match:
                    current["year"] = int(year_match.group(1))
            elif tag == "DO":
                current["doi"] = value
            elif tag in ("JO", "JF", "T2"):
                current.setdefault("journal", value)
            elif tag in ("AB", "N2"):
                current["abstract"] = value
            elif tag == "PB":
                current["publisher"] = value
            elif tag == "SN":
                current["isbn"] = value

    if current and current.get("title"):
        entries.append(current)

    results = []
    for entry in entries:
        title = entry.get("title", "")
        authors = "; ".join(entry.get("authors", []))

        hash_input = f"{title}_{authors}".encode("utf-8")
        file_hash = hashlib.sha256(hash_input).hexdigest()

        if db.paper_exists(file_hash):
            results.append({"title": title, "status": "skipped", "reason": "Bereits vorhanden"})
            continue

        paper_data = {
            "file_hash": file_hash,
            "filename": "",
            "original_filename": f"RIS-Import: {title[:80]}",
            "import_source": file.filename or "",
            "title": title,
            "authors": authors,
            "year": entry.get("year"),
            "doi": entry.get("doi", ""),
            "isbn": entry.get("isbn", ""),
            "abstract": entry.get("abstract", "")[:5000],
            "journal": entry.get("journal", ""),
            "publisher": entry.get("publisher", ""),
            "raw_metadata": json.dumps(entry, ensure_ascii=False, default=str),
            "ocr_text": "",
        }

        try:
            paper_id = db.add_paper(paper_data)
            results.append({"title": title, "status": "ok", "paper_id": paper_id})
        except Exception as e:
            results.append({"title": title, "status": "error", "error": str(e)})

    return {
        "total": len(entries),
        "imported": sum(1 for r in results if r["status"] == "ok"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
