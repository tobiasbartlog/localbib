"""Router: metadata validation — propose/apply, bulk-validate, DOI discovery, OCR.

Serves:
  POST /api/papers/{paper_id}/validate/propose
  POST /api/papers/{paper_id}/validate/apply
  POST /api/papers/bulk-validate
  POST /api/papers/discover-dois
  POST /api/papers/apply-doi
  POST /api/papers/fetch-abstracts
  POST /api/papers/{paper_id}/generate-abstract
  POST /api/papers/{paper_id}/ocr

Pure move from webapp.py (Backend-Modularisierung #86). No behaviour change.
HTTP contract (paths / methods / shapes / status codes) is bit-identical to the
previous webapp.py handlers.

Route-order safety: literal/collection routes (bulk-validate, discover-dois,
apply-doi, fetch-abstracts) are registered BEFORE the parametric /{paper_id}
routes so they are never shadowed by the path parameter.

Shared resources come from context.get_conn(); DB-object helpers use
_get_db(). The LLM title/author/category helpers come from
services.metadata_extraction (pure service — no HTTP concerns).

The propose/apply policy (validate_propose / validate_apply, ValidateOptions,
ValidateApplyRequest, VALIDATE_PAGES) lives in the neutral ``validation_policy``
module so the maintenance router can reuse it without a router→router import
(#92). This module imports them from there and registers the two route wrappers;
``validate_propose`` / ``validate_apply`` stay module attributes here so existing
tests patching ``routers.validate.validate_propose`` keep working.

No import from webapp or any other router.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

import metadata_validation
import validation_policy
from context import get_conn
from fastapi import APIRouter, HTTPException, Request
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    extract_text_from_pdf,
    fetch_crossref_metadata,
    generate_filename,
    discover_doi,
    ocr_pdf,
    ocr_pdf_searchable,
)
from llm_client import llm_for
from metadata_validation import CrossRefStrategy
from pydantic import BaseModel
from services.metadata_extraction import llm_extract_metadata
from validation_policy import (
    VALIDATE_PAGES,
    ValidateApplyRequest,
    ValidateOptions,
    validate_apply,
    validate_propose,
)

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing ``_get_conn()`` calls without modification.
_get_conn = get_conn


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from webapp.py)
# ---------------------------------------------------------------------------

class BulkValidateRequest(BaseModel):
    paper_ids: List[int]
    use_llm: bool = True
    pages: int = VALIDATE_PAGES


class DoiApply(BaseModel):
    paper_id: int
    doi: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


# ---------------------------------------------------------------------------
# Endpoints — literal/collection routes FIRST, then parametric /{paper_id}.
# ---------------------------------------------------------------------------

@router.post("/api/papers/{paper_id}/validate/propose")
async def validate_propose_endpoint(paper_id: int, options: ValidateOptions = None):
    """Route wrapper around ``validation_policy.validate_propose`` — bit-identical
    HTTP contract. Calls the module-global ``validate_propose`` so tests patching
    ``routers.validate.validate_propose`` are honoured here too."""
    return await validate_propose(paper_id, options)


@router.post("/api/papers/{paper_id}/validate/apply")
async def validate_apply_endpoint(paper_id: int, data: ValidateApplyRequest):
    """Route wrapper around ``validation_policy.validate_apply`` — bit-identical
    HTTP contract."""
    return await validate_apply(paper_id, data)


@router.post("/api/papers/bulk-validate")
async def bulk_validate_papers(data: BulkValidateRequest):
    """Run Metadata Validation across many Papers.

    Auto-applies high-Confidence Proposals; collects low-Confidence Proposals
    into ``review_queue`` so the frontend can prompt the user to review them
    one-by-one. See CONTEXT.md for **Confidence**.
    """
    results = []
    review_queue = []
    for pid in data.paper_ids:
        try:
            opts = ValidateOptions(use_llm=data.use_llm, pages=data.pages)
            proposal = await validate_propose(pid, opts)
            has_work = bool(proposal.get("changes")) or bool(proposal.get("category_suggestions"))
            if not has_work:
                results.append({"id": pid, "status": "noop", "confidence": proposal.get("confidence")})
                continue
            if proposal.get("confidence") == "high":
                apply_req = ValidateApplyRequest(
                    changes=proposal["changes"],
                    category_assignments=proposal.get("category_suggestions") or [],
                )
                await validate_apply(pid, apply_req)
                results.append({"id": pid, "status": "applied", "confidence": "high"})
            else:
                review_queue.append({"id": pid, "proposal": proposal})
                results.append({"id": pid, "status": "review", "confidence": "low"})
        except Exception as e:
            results.append({"id": pid, "status": "error", "error": str(e)})
    return {"results": results, "review_queue": review_queue}


@router.post("/api/papers/discover-dois")
async def bulk_discover_dois():
    """Sucht DOIs fuer alle Paper ohne DOI. Gibt Kandidaten zurueck (ohne automatisches Speichern)."""
    db = Database(Config.DB_PATH)
    papers = db.get_all_papers()

    conn = _get_conn()
    try:
        # Nur Paper ohne DOI und nicht manuell verifiziert
        papers_without_doi = []
        for p in papers:
            if p.get("doi"):
                continue
            row = conn.execute(
                "SELECT doi_manually_verified FROM papers WHERE id = ?", (p["id"],)
            ).fetchone()
            if row and row["doi_manually_verified"]:
                continue
            papers_without_doi.append(p)
    finally:
        conn.close()

    candidates = []

    for paper in papers_without_doi:
        paper_id = paper["id"]
        filename = paper.get("filename", "")
        title = paper.get("title", "")
        authors = paper.get("authors", "")
        year = paper.get("year")

        try:
            # PDF-Pfad rekonstruieren
            filepath = os.path.join(Config.ALL_DIR, filename) if filename else ""
            if not filepath or not os.path.exists(filepath):
                continue

            # Text aus PDF extrahieren
            text = extract_text_from_pdf(filepath, max_pages=5)

            if not text.strip():
                continue

            # DOI suchen via CrossRefStrategy
            cr_strategy = CrossRefStrategy(
                discover=lambda t, ti, au, yr: discover_doi(t, ti, au, yr),
                fetch=lambda d: fetch_crossref_metadata(d),
                respect_manual_doi=False,
            )
            cr_strategy.run(text, {"title": title, "authors": authors, "year": year})
            doi = cr_strategy.last_doi

            if doi:
                crossref_data = cr_strategy.last_crossref_data or {}
                candidates.append({
                    "paper_id": paper_id,
                    "doi": doi,
                    "local": {
                        "title": title,
                        "authors": authors,
                        "year": year,
                    },
                    "crossref": {
                        "title": crossref_data.get("title", ""),
                        "authors": crossref_data.get("authors", ""),
                        "year": crossref_data.get("year"),
                    },
                })
                logging.info(f"🔍 DOI-Kandidat fuer '{title[:60]}': {doi}")

        except Exception as e:
            logging.error(f"Fehler bei DOI-Suche fuer Paper {paper_id}: {e}")

    return {
        "total_checked": len(papers_without_doi),
        "candidates": candidates,
    }


@router.post("/api/papers/apply-doi")
async def apply_doi(data: DoiApply):
    """Bestaetigt und speichert eine gefundene DOI fuer ein Paper."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (data.paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)

        # DOI speichern
        conn.execute(
            "UPDATE papers SET doi = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (data.doi, data.paper_id),
        )

        # CrossRef-Metadaten holen und fehlende Felder updaten.
        # We reuse the strategy here purely to centralize the field-merging
        # logic; the caller has already chosen the DOI, so we bypass discovery.
        crossref_data = fetch_crossref_metadata(data.doi)
        if crossref_data:
            updates = {}
            for f in ("title", "authors", "year", "abstract", "journal", "publisher", "isbn"):
                if crossref_data.get(f) and not paper.get(f):
                    updates[f] = crossref_data[f]

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE papers SET {set_clause} WHERE id = ?",
                    (*updates.values(), data.paper_id),
                )

        conn.commit()
    finally:
        conn.close()

    logging.info(f"✅ DOI bestaetigt fuer Paper {data.paper_id}: {data.doi}")
    return {"status": "ok", "paper_id": data.paper_id, "doi": data.doi}


@router.post("/api/papers/fetch-abstracts")
async def bulk_fetch_abstracts(request: Request):
    """Holt Abstracts per CrossRef fuer Paper mit DOI aber ohne Abstract.
    Optional: body mit paper_ids um nur bestimmte Paper zu pruefen."""
    db = Database(Config.DB_PATH)
    papers = db.get_all_papers()

    # Filter to selected paper_ids if provided
    try:
        body = await request.json()
        paper_ids = body.get("paper_ids") if body else None
    except Exception:
        paper_ids = None

    if paper_ids:
        selected_ids = set(paper_ids)
        papers = [p for p in papers if p["id"] in selected_ids]

    # Nur Paper mit DOI aber ohne Abstract
    candidates = [p for p in papers if p.get("doi") and not p.get("abstract")]

    fetched = 0
    for paper in candidates:
        try:
            abstract = metadata_validation.fetch_crossref_abstract(
                paper["doi"], fetch=lambda d: fetch_crossref_metadata(d)
            )
            if abstract:
                conn = _get_conn()
                try:
                    conn.execute(
                        "UPDATE papers SET abstract = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (abstract[:5000], paper["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                fetched += 1
                logging.info(f"📄 Abstract geholt fuer: {paper.get('title', '')[:60]}")
        except Exception as e:
            logging.error(f"Fehler beim Abstract-Fetch fuer Paper {paper['id']}: {e}")

    return {"total": len(candidates), "fetched": fetched}


@router.post("/api/papers/{paper_id}/generate-abstract")
async def generate_abstract(paper_id: int):
    """Generiert/holt ein Abstract fuer ein einzelnes Paper.

    Strategie:
    1. DOI vorhanden -> CrossRef Abstract holen
    2. Kein CrossRef-Abstract -> LLM sucht Abstract im PDF-Text
    3. Kein Abstract im PDF -> LLM erstellt Zusammenfassung
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()

    abstract = ""
    source = ""

    # --- Schritt 1: CrossRef via DOI ---
    if paper.get("doi"):
        cr_abstract = metadata_validation.fetch_crossref_abstract(
            paper["doi"], fetch=lambda d: fetch_crossref_metadata(d)
        )
        if cr_abstract:
            abstract = cr_abstract
            source = "crossref"
            logging.info(f"📄 Abstract via CrossRef gefunden fuer Paper {paper_id}")

    # --- Schritt 2 & 3: LLM ---
    if not abstract and Config.KICONNECT_API_KEY:
        # PDF-Text laden
        filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
        pdf_text = ""
        if os.path.exists(filepath):
            pdf_text = extract_text_from_pdf(filepath, max_pages=10)

        if not pdf_text.strip():
            # Fallback: gespeicherten OCR-Text nutzen
            pdf_text = paper.get("ocr_text", "") or ""

        if pdf_text.strip():
            text_snippet = pdf_text[:6000]
            llm = llm_for("abstract")

            # Schritt 2: LLM sucht Abstract im Dokument
            found = metadata_validation.llm_find_abstract_in_text(
                llm,
                paper.get("title", "") or "",
                paper.get("authors", "") or "",
                text_snippet,
            )
            if found:
                abstract = found
                source = "pdf"
                logging.info(f"📄 Abstract im PDF gefunden (LLM) fuer Paper {paper_id}")

            # Schritt 3: LLM generiert Zusammenfassung
            if not abstract:
                generated = metadata_validation.llm_generate_abstract(
                    llm,
                    paper.get("title", "") or "",
                    paper.get("authors", "") or "",
                    text_snippet,
                )
                if generated:
                    abstract = generated
                    source = "generated"
                    logging.info(f"📄 Abstract generiert (LLM) fuer Paper {paper_id}")

    if not abstract:
        return {"status": "error", "message": "Kein Abstract gefunden oder generiert. Pruefe ob DOI/PDF vorhanden und LLM konfiguriert ist."}

    # In DB speichern
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE papers SET abstract = ?, abstract_source = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (abstract[:5000], source, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "abstract": abstract, "source": source}


@router.post("/api/papers/{paper_id}/ocr")
async def ocr_paper(paper_id: int, pages: int = 10):
    """Fuehrt OCR auf einem Paper durch und aktualisiert den gespeicherten Text.

    Nuetzlich fuer gescannte PDFs die bei der Erstverarbeitung keinen Text lieferten.
    Loest danach optional eine LLM-Metadaten-Extraktion aus.
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()

    filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="PDF-Datei nicht gefunden")

    # 1. OCR ausfuehren (Text extrahieren)
    ocr_text = ocr_pdf(filepath, max_pages=pages)
    if not ocr_text.strip():
        return {
            "status": "no_text",
            "message": "OCR konnte keinen Text extrahieren. Tesseract installiert?",
            "chars": 0,
        }

    # 2. Durchsuchbares PDF erstellen (Textlayer einbetten)
    searchable_ok = ocr_pdf_searchable(filepath)

    # 3. OCR-Text in DB speichern
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE papers SET ocr_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ocr_text[:10000], paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 4. LLM-Metadaten-Extraktion mit OCR-Text
    llm_data = {}
    if Config.KICONNECT_API_KEY:
        llm = llm_for("metadata_extract")
        llm_data = llm_extract_metadata(
            llm,
            ocr_text[:4000],
            paper["title"],
            paper["authors"],
            filename=paper["filename"],
            original_filename=paper.get("original_filename", ""),
        )

        # Datenbank mit LLM-Ergebnissen aktualisieren
        update_fields = []
        update_values = []
        changes = {}
        if llm_data:
            for field in ["title", "authors", "year", "doi", "isbn"]:
                val = llm_data.get(field)
                if not val:
                    continue
                current = paper.get(field)
                current_str = str(current) if current else ""
                # Aktualisieren wenn: leer, XXXX-Prefix, oder Titel aus Dateiname (Underscores)
                is_empty = not current_str.strip()
                is_placeholder = current_str.startswith("XXXX")
                is_filename_title = (field == "title" and "_" in current_str
                                     and current_str == os.path.splitext(paper.get("original_filename", ""))[0])
                if is_empty or is_placeholder or is_filename_title:
                    # Titel-Unterstriche bereinigen falls LLM sie zurueckgibt
                    if field == "title" and "_" in str(val):
                        val = str(val).replace("_", " ").strip()
                    update_fields.append(f"{field} = ?")
                    update_values.append(val)
                    changes[field] = val

        if update_fields:
            update_values.append(paper_id)
            conn = _get_conn()
            try:
                conn.execute(
                    f"UPDATE papers SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    update_values,
                )
                conn.commit()
            finally:
                conn.close()

            # Dateiname aktualisieren
            if changes.get("title") or changes.get("authors") or changes.get("year"):
                merged = {
                    "title": changes.get("title", paper["title"]),
                    "authors": changes.get("authors", paper["authors"]),
                    "year": changes.get("year", paper["year"]),
                }
                candidate = generate_filename(merged)
                if candidate != paper["filename"]:
                    target = os.path.join(Config.ALL_DIR, candidate)
                    counter = 1
                    base_candidate = candidate
                    while os.path.exists(target) and target != filepath:
                        name, ext = os.path.splitext(base_candidate)
                        candidate = f"{name}_{counter}{ext}"
                        target = os.path.join(Config.ALL_DIR, candidate)
                        counter += 1
                    try:
                        os.rename(filepath, target)
                        conn = _get_conn()
                        try:
                            conn.execute("UPDATE papers SET filename = ? WHERE id = ?",
                                        (candidate, paper_id))
                            conn.commit()
                        finally:
                            conn.close()
                        db = _get_db()
                        _rebuild_all_symlinks(db)
                    except OSError as e:
                        logging.warning(f"Rename failed: {e}")

    # Aktualisiertes Paper zurueckgeben
    conn = _get_conn()
    try:
        updated = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        result = dict(updated)
    finally:
        conn.close()

    result["_ocr_chars"] = len(ocr_text)
    result["_ocr_llm"] = llm_data
    result["_ocr_searchable"] = searchable_ok
    return result
