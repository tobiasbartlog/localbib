"""Router: references domain.

Serves:
  POST /api/papers/{paper_id}/extract-references   (SSE stream)
  GET  /api/papers/{paper_id}/references           (CRUD list)
  PUT  /api/papers/{paper_id}/references/{ref_id}  (update + rematch)
  DELETE /api/papers/{paper_id}/references/{ref_id} (delete)
  POST /api/papers/{paper_id}/references/add       (manual add)
  POST /api/papers/bulk-extract-references         (SSE stream)

Pure move from webapp.py (Backend-Modularisierung #88). No behaviour change.
HTTP contract (paths / methods / shapes / status codes / SSE events) is
bit-identical to the previous webapp.py handlers.

Route-order safety: ``POST /api/papers/bulk-extract-references`` (literal) is
registered BEFORE the parametric ``/{paper_id}/...`` routes so that
``bulk-extract-references`` is never swallowed by the path parameter.

Shared resources come from ``context.get_conn()``. Pure extraction logic
(PDF page reading, reference-section detection, LLM parsing, CrossRef lookup,
library matching) lives in ``services.reference_extraction``.
No import from webapp or any other router.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import services.reference_extraction as _ref_svc
from context import get_conn
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from literature_manager import Config
from pydantic import BaseModel

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing ``_get_conn()`` calls without modification.
_get_conn = get_conn


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from webapp.py)
# ---------------------------------------------------------------------------

class ReferenceUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    doi: Optional[str] = None


class ReferenceCreate(BaseModel):
    title: str
    authors: Optional[str] = ""
    year: Optional[int] = None
    journal: Optional[str] = ""
    doi: Optional[str] = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Endpoints — literal/collection routes FIRST, then parametric /{paper_id}.
# ---------------------------------------------------------------------------

@router.post("/api/papers/bulk-extract-references")
async def bulk_extract_references():
    """Extrahiert Referenzen fuer alle Paper die noch keine haben, mit SSE-Fortschritt."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT p.id, p.title, p.filename FROM papers p
               WHERE p.filename IS NOT NULL AND p.filename != ''
               AND p.id NOT IN (SELECT DISTINCT source_paper_id FROM paper_references)
               ORDER BY p.id"""
        ).fetchall()
        candidates = [dict(r) for r in rows]
    finally:
        conn.close()

    async def generate():
        total_papers = len(candidates)
        if total_papers == 0:
            yield _sse({"type": "complete", "processed": 0, "total_references": 0, "total_in_library": 0, "results": []})
            return

        yield _sse({"type": "progress", "step": "start", "message": f"{total_papers} Paper zu verarbeiten", "percent": 0, "current_paper": 0, "total_papers": total_papers})

        results = []
        for idx, paper in enumerate(candidates):
            paper_title = (paper.get("title") or "Unbekannt")[:60]
            base_pct = int((idx / total_papers) * 100)

            yield _sse({"type": "progress", "step": "paper_start", "message": f"Paper {idx+1}/{total_papers}: {paper_title}", "percent": base_pct, "current_paper": idx + 1, "total_papers": total_papers, "paper_title": paper_title})

            try:
                filename = paper.get("filename", "")
                filepath = os.path.join(Config.ALL_DIR, filename) if filename else ""
                if not filepath or not os.path.exists(filepath):
                    results.append({"paper_id": paper["id"], "title": paper_title, "status": "file_not_found", "total_extracted": 0, "in_library": 0})
                    yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "file_not_found"})
                    continue

                # PDF lesen
                pages = _ref_svc.extract_all_pdf_pages(filepath)
                if not pages:
                    results.append({"paper_id": paper["id"], "title": paper_title, "status": "no_text", "total_extracted": 0, "in_library": 0})
                    yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "no_text"})
                    continue

                yield _sse({"type": "progress", "step": "find_refs", "message": f"Literaturverzeichnis suchen: {paper_title}", "percent": base_pct + 5, "current_paper": idx + 1, "total_papers": total_papers})

                ref_text = _ref_svc.find_reference_section(pages)
                if not ref_text or len(ref_text.strip()) < 50:
                    results.append({"paper_id": paper["id"], "title": paper_title, "status": "no_ref_section", "total_extracted": 0, "in_library": 0})
                    yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "no_ref_section"})
                    continue

                yield _sse({"type": "progress", "step": "llm", "message": f"KI-Extraktion: {paper_title}", "percent": base_pct + 10, "current_paper": idx + 1, "total_papers": total_papers})

                extracted_refs = _ref_svc.llm_extract_references(ref_text)
                if not extracted_refs:
                    results.append({"paper_id": paper["id"], "title": paper_title, "status": "no_refs_extracted", "total_extracted": 0, "in_library": 0})
                    yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "no_refs_extracted"})
                    continue

                yield _sse({"type": "progress", "step": "matching", "message": f"DOI-Lookup fuer {len(extracted_refs)} Refs: {paper_title}", "percent": base_pct + 20, "current_paper": idx + 1, "total_papers": total_papers})

                # Speichern
                conn2 = _get_conn()
                try:
                    saved = 0
                    matched = 0
                    for ridx, ref in enumerate(extracted_refs, 1):
                        ref_title = (ref.get("title") or "").strip()
                        ref_authors = (ref.get("authors") or "").strip()
                        ref_year = ref.get("year")
                        ref_journal = (ref.get("journal") or "").strip()
                        ref_doi = (ref.get("doi") or "").strip()

                        if not ref_doi:
                            found = _ref_svc.crossref_doi_lookup(ref_title, ref_authors)
                            if found:
                                ref_doi = found
                            time.sleep(0.1)

                        m = _ref_svc.match_ref_to_library({"doi": ref_doi, "title": ref_title}, conn2)
                        conn2.execute(
                            """INSERT INTO paper_references
                               (source_paper_id, ref_index, title, authors, year, journal, doi,
                                matched_paper_id, match_confidence, source)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pdf_llm')""",
                            (paper["id"], ridx, ref_title, ref_authors, ref_year, ref_journal,
                             ref_doi, m["matched_paper_id"], m["match_confidence"]),
                        )
                        saved += 1
                        if m["matched_paper_id"]:
                            matched += 1
                    conn2.commit()
                finally:
                    conn2.close()

                results.append({"paper_id": paper["id"], "title": paper_title, "status": "ok", "total_extracted": saved, "in_library": matched})
                yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "ok", "total_extracted": saved, "in_library": matched})
                logging.info(f"  Referenzen: {paper_title} -> {saved} Refs, {matched} in Bib")

            except Exception as e:
                results.append({"paper_id": paper["id"], "title": paper_title, "status": "error", "error": str(e), "total_extracted": 0, "in_library": 0})
                yield _sse({"type": "paper_result", "paper_id": paper["id"], "title": paper_title, "status": "error", "error": str(e)})
                logging.error(f"Bulk-Extraktion fehlgeschlagen fuer Paper {paper['id']}: {e}")

            time.sleep(0.3)

        total_refs = sum(r.get("total_extracted", 0) for r in results)
        total_lib = sum(r.get("in_library", 0) for r in results)
        yield _sse({"type": "complete", "processed": len(results), "total_references": total_refs, "total_in_library": total_lib, "results": results})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/papers/{paper_id}/extract-references")
async def extract_references(paper_id: int, force: bool = Query(False)):
    """Extrahiert Referenzen aus dem Literaturverzeichnis eines Papers via SSE-Stream."""
    # Validierung vorab
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
        existing_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_references WHERE source_paper_id = ?", (paper_id,)
        ).fetchone()["cnt"]
    finally:
        conn.close()

    filename = paper.get("filename", "")
    if not filename:
        raise HTTPException(status_code=400, detail="Kein PDF vorhanden")
    filepath = os.path.join(Config.ALL_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="PDF-Datei nicht gefunden")

    async def generate():
        # Bereits extrahiert und kein force?
        if existing_count > 0 and not force:
            c2 = _get_conn()
            try:
                refs = c2.execute(
                    """SELECT pr.*, p.title as matched_title, p.authors as matched_authors
                       FROM paper_references pr
                       LEFT JOIN papers p ON pr.matched_paper_id = p.id
                       WHERE pr.source_paper_id = ? ORDER BY pr.ref_index""",
                    (paper_id,),
                ).fetchall()
                yield _sse({
                    "type": "complete", "status": "already_extracted",
                    "total_extracted": len(refs),
                    "in_library": sum(1 for r in refs if r["matched_paper_id"]),
                    "with_doi": sum(1 for r in refs if r["doi"]),
                    "references": [dict(r) for r in refs],
                })
            finally:
                c2.close()
            return

        if existing_count > 0 and force:
            yield _sse({"type": "progress", "step": "cleanup", "message": "Alte Referenzen werden geloescht...", "percent": 5})
            c2 = _get_conn()
            try:
                c2.execute("DELETE FROM paper_references WHERE source_paper_id = ?", (paper_id,))
                c2.commit()
            finally:
                c2.close()

        # 1. PDF lesen
        yield _sse({"type": "progress", "step": "pdf", "message": "PDF wird gelesen...", "percent": 10})
        pages = _ref_svc.extract_all_pdf_pages(filepath)
        if not pages:
            yield _sse({"type": "error", "message": "Kein Text aus PDF extrahierbar"})
            return
        yield _sse({"type": "progress", "step": "pdf", "message": f"{len(pages)} Seiten extrahiert", "percent": 20})

        # 2. Referenz-Abschnitt finden
        yield _sse({"type": "progress", "step": "find_refs", "message": "Literaturverzeichnis wird gesucht...", "percent": 25})
        ref_text = _ref_svc.find_reference_section(pages)
        if not ref_text or len(ref_text.strip()) < 50:
            yield _sse({"type": "error", "message": "Kein Literaturverzeichnis gefunden"})
            return
        yield _sse({"type": "progress", "step": "find_refs", "message": f"Literaturverzeichnis gefunden ({len(ref_text)} Zeichen)", "percent": 30})

        # 3. LLM-Extraktion — chunk-weise, damit der Fortschritt sichtbar ist.
        #    Chunking liegt im Router (Service bleibt pure, ADR-0006); die
        #    LLM-Phase belegt den Prozentbereich 35..60.
        yield _sse({"type": "progress", "step": "llm", "message": "KI extrahiert Referenzen... (kann 1-3 Min dauern)", "percent": 35})
        chunks = _ref_svc.split_reference_text(ref_text)
        total_chunks = len(chunks)
        extracted_refs: list[dict] = []
        loop = asyncio.get_running_loop()
        for ci, chunk in enumerate(chunks, 1):
            chunk_refs = await loop.run_in_executor(
                None, _ref_svc.extract_references_from_chunk, chunk
            )
            extracted_refs.extend(chunk_refs)
            pct = 35 + int((ci / total_chunks) * 25)  # 35 -> 60
            yield _sse({"type": "progress", "step": "llm",
                        "message": f"KI liest Referenzen... Abschnitt {ci}/{total_chunks} ({len(extracted_refs)} gefunden)",
                        "percent": pct, "current": ci, "total": total_chunks,
                        "found": len(extracted_refs)})
        if not extracted_refs:
            yield _sse({"type": "error", "message": "KI konnte keine Referenzen extrahieren"})
            return
        yield _sse({"type": "progress", "step": "llm", "message": f"{len(extracted_refs)} Referenzen extrahiert", "percent": 60})

        # 4. DOI-Lookup (parallel) & Library-Matching
        total = len(extracted_refs)
        # CrossRefs Polite Pool erlaubt ~50 req/s; 10 Worker mit
        # mehrsekuendiger Antwortlatenz bleiben weit darunter.
        lookup_indices = [
            i for i, ref in enumerate(extracted_refs)
            if not (ref.get("doi") or "").strip()
        ]
        found_dois: dict[int, str] = {}
        if lookup_indices:
            yield _sse({"type": "progress", "step": "matching",
                        "message": f"DOI-Suche fuer {len(lookup_indices)} Referenzen...",
                        "percent": 60})
            loop = asyncio.get_running_loop()

            def _lookup(i: int):
                ref = extracted_refs[i]
                doi = _ref_svc.crossref_doi_lookup(
                    (ref.get("title") or "").strip(),
                    (ref.get("authors") or "").strip(),
                )
                return i, doi

            with ThreadPoolExecutor(max_workers=10) as pool:
                tasks = [loop.run_in_executor(pool, _lookup, i) for i in lookup_indices]
                for done, fut in enumerate(asyncio.as_completed(tasks), 1):
                    i, doi = await fut
                    if doi:
                        found_dois[i] = doi
                    title = (extracted_refs[i].get("title") or "").strip()
                    short_title = title[:57] + "..." if len(title) > 60 else title
                    pct = 60 + int((done / len(tasks)) * 33)
                    yield _sse({"type": "progress", "step": "matching",
                                "message": f"DOI-Suche {done}/{len(tasks)}: {short_title}",
                                "percent": pct, "current": done, "total": len(tasks),
                                "found": len(found_dois)})

        yield _sse({"type": "progress", "step": "matching",
                    "message": "Referenzen werden mit Bibliothek abgeglichen...", "percent": 95})
        conn2 = _get_conn()
        try:
            saved_refs = []
            for idx, ref in enumerate(extracted_refs, 1):
                ref_title = (ref.get("title") or "").strip()
                ref_authors = (ref.get("authors") or "").strip()
                ref_year = ref.get("year")
                ref_journal = (ref.get("journal") or "").strip()
                ref_doi = (ref.get("doi") or "").strip() or found_dois.get(idx - 1, "")

                match = _ref_svc.match_ref_to_library({"doi": ref_doi, "title": ref_title}, conn2)

                conn2.execute(
                    """INSERT INTO paper_references
                       (source_paper_id, ref_index, title, authors, year, journal, doi,
                        matched_paper_id, match_confidence, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pdf_llm')""",
                    (paper_id, idx, ref_title, ref_authors, ref_year, ref_journal,
                     ref_doi, match["matched_paper_id"], match["match_confidence"]),
                )
                saved_refs.append({
                    "ref_index": idx, "title": ref_title, "authors": ref_authors,
                    "year": ref_year, "journal": ref_journal, "doi": ref_doi,
                    "matched_paper_id": match["matched_paper_id"],
                    "match_confidence": match["match_confidence"],
                })

            conn2.commit()
            all_refs = conn2.execute(
                """SELECT pr.*, p.title as matched_title, p.authors as matched_authors
                   FROM paper_references pr LEFT JOIN papers p ON pr.matched_paper_id = p.id
                   WHERE pr.source_paper_id = ? ORDER BY pr.ref_index""",
                (paper_id,),
            ).fetchall()
        finally:
            conn2.close()

        in_library = sum(1 for r in saved_refs if r["matched_paper_id"])
        with_doi = sum(1 for r in saved_refs if r["doi"])
        logging.info(f"📚 Referenzen extrahiert fuer Paper {paper_id}: {len(saved_refs)} Refs, {with_doi} DOI, {in_library} in Bib")

        yield _sse({
            "type": "complete", "status": "ok",
            "total_extracted": len(saved_refs), "with_doi": with_doi,
            "in_library": in_library, "references": [dict(r) for r in all_refs],
        })

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/papers/{paper_id}/references")
async def get_paper_references(paper_id: int):
    """Gibt die extrahierten Referenzen eines Papers zurueck."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")

        refs = conn.execute(
            """SELECT pr.*, p.title as matched_title, p.authors as matched_authors
               FROM paper_references pr
               LEFT JOIN papers p ON pr.matched_paper_id = p.id
               WHERE pr.source_paper_id = ?
               ORDER BY pr.ref_index""",
            (paper_id,),
        ).fetchall()

        return {
            "paper_id": paper_id,
            "count": len(refs),
            "references": [dict(r) for r in refs],
        }
    finally:
        conn.close()


@router.put("/api/papers/{paper_id}/references/{ref_id}")
async def update_reference(paper_id: int, ref_id: int, data: ReferenceUpdate):
    """Aktualisiert eine einzelne Referenz manuell."""
    conn = _get_conn()
    try:
        ref = conn.execute(
            "SELECT * FROM paper_references WHERE id = ? AND source_paper_id = ?",
            (ref_id, paper_id),
        ).fetchone()
        if not ref:
            raise HTTPException(status_code=404, detail="Referenz nicht gefunden")

        updates = []
        values = []
        for field in ["title", "authors", "year", "journal", "doi"]:
            val = getattr(data, field, None)
            if val is not None:
                updates.append(f"{field} = ?")
                values.append(val if val != "" else None)

        if updates:
            values.append(ref_id)
            conn.execute(f"UPDATE paper_references SET {', '.join(updates)} WHERE id = ?", values)

        # Rematch nach Aenderung
        updated = conn.execute("SELECT doi, title FROM paper_references WHERE id = ?", (ref_id,)).fetchone()
        match = _ref_svc.match_ref_to_library({"doi": updated["doi"] or "", "title": updated["title"] or ""}, conn)
        conn.execute(
            "UPDATE paper_references SET matched_paper_id = ?, match_confidence = ? WHERE id = ?",
            (match["matched_paper_id"], match["match_confidence"], ref_id),
        )
        conn.commit()

        result = conn.execute(
            """SELECT pr.*, p.title as matched_title, p.authors as matched_authors
               FROM paper_references pr LEFT JOIN papers p ON pr.matched_paper_id = p.id
               WHERE pr.id = ?""",
            (ref_id,),
        ).fetchone()
        return dict(result)
    finally:
        conn.close()


@router.delete("/api/papers/{paper_id}/references/{ref_id}")
async def delete_reference(paper_id: int, ref_id: int):
    """Loescht eine einzelne Referenz."""
    conn = _get_conn()
    try:
        ref = conn.execute(
            "SELECT id FROM paper_references WHERE id = ? AND source_paper_id = ?",
            (ref_id, paper_id),
        ).fetchone()
        if not ref:
            raise HTTPException(status_code=404, detail="Referenz nicht gefunden")
        conn.execute("DELETE FROM paper_references WHERE id = ?", (ref_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.post("/api/papers/{paper_id}/references/add")
async def add_reference(paper_id: int, data: ReferenceCreate):
    """Fuegt eine Referenz manuell hinzu."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")

        max_idx = conn.execute(
            "SELECT COALESCE(MAX(ref_index), 0) as m FROM paper_references WHERE source_paper_id = ?",
            (paper_id,),
        ).fetchone()["m"]
        next_idx = max_idx + 1

        match = _ref_svc.match_ref_to_library({"doi": data.doi or "", "title": data.title}, conn)

        conn.execute(
            """INSERT INTO paper_references
               (source_paper_id, ref_index, title, authors, year, journal, doi,
                matched_paper_id, match_confidence, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual')""",
            (paper_id, next_idx, data.title, data.authors, data.year, data.journal,
             data.doi, match["matched_paper_id"], match["match_confidence"]),
        )
        conn.commit()

        new_ref = conn.execute(
            """SELECT pr.*, p.title as matched_title, p.authors as matched_authors
               FROM paper_references pr LEFT JOIN papers p ON pr.matched_paper_id = p.id
               WHERE pr.source_paper_id = ? AND pr.ref_index = ?""",
            (paper_id, next_idx),
        ).fetchone()
        return dict(new_ref)
    finally:
        conn.close()
