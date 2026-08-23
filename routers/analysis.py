"""Router: analysis domain.

Serves:
  POST /api/analysis/thesis           (thesis PDF analysis, no DB writes)
  POST /api/analysis/build            (citation network via OpenAlex + network_orchestrator)
  POST /api/analysis/update-citations (refresh OpenAlex metadata for DOI-bearing papers)
  GET  /api/analysis/node-abstract    (fetch abstract by DOI or OpenAlex ID)
  POST /api/analysis/chat             (LLM Q&A over selected papers, SSE stream)

Pure move from ``webapp.py`` (Backend-Modularisierung #90). HTTP contract
(paths / methods / shapes / status codes / SSE behaviour) is bit-identical to
the previous webapp.py handlers.

Citation-network routes (build, update-citations) delegate to
``network_orchestrator`` exactly as before; the router persists the returned
``paper_updates`` to the DB.

Thesis analysis logic (author extraction, citation matching, availability)
lives in ``services.thesis_analyzer`` (PURE, unit-tested). Reference helpers
(page extraction, section detection, LLM parsing) live in
``services.reference_extraction`` (PURE). Module-level aliases expose
them for test patching via ``patch.object(routers.analysis, "...")``.

No import from ``webapp`` or any other router.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime

import network_orchestrator
import services.reference_extraction as _ref_svc
import services.thesis_analyzer as _thesis_svc
from context import get_conn
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from literature_manager import Config, extract_text_from_pdf
from llm_client import LLMClientError, llm_for
from openalex_client import OpenAlexClient
from pydantic import BaseModel

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing ``_get_conn()`` calls without modification.
_get_conn = get_conn

# Module-level aliases for easy test-patching via
# ``patch.object(routers.analysis, "_extract_all_pdf_pages", ...)`` etc.
_extract_all_pdf_pages = _ref_svc.extract_all_pdf_pages
_find_reference_section = _ref_svc.find_reference_section
_llm_extract_references = _ref_svc.llm_extract_references
_check_online_availability = _thesis_svc.check_online_availability


# ---------------------------------------------------------------------------
# Local helpers (not exported; needed by multiple handlers)
# ---------------------------------------------------------------------------

def _oa_mailto() -> str:
    return Config.polite_mailto()


def _normalize_doi(doi: str) -> str:
    """Normalize DOI to short form (without https://doi.org/ prefix)."""
    doi = (doi or "").strip()
    if doi.startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.lower().rstrip(".")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    paper_ids: list[int] = []
    external_papers: list[dict] = []  # [{title, authors, year, doi, abstract}]
    question: str
    history: list[dict] = []  # previous messages [{role, content}]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/analysis/thesis")
async def analyze_thesis(file: UploadFile = File(...)):
    """Analysiert eine Abschlussarbeit (PDF) ohne sie in die Bibliothek aufzunehmen.

    Extrahiert:
    1. Alle Quellen aus dem Literaturverzeichnis
    2. Prueft welche Quellen online auffindbar sind
    3. Prueft welche Quellen im Text zitiert werden

    Die PDF wird nur temporaer verarbeitet und danach geloescht.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien werden akzeptiert")

    # Temporaere Datei erstellen
    tmp_dir = tempfile.mkdtemp(prefix="thesis_analysis_")
    tmp_path = os.path.join(tmp_dir, file.filename)

    try:
        # PDF speichern
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)

        # 1. Gesamten Text extrahieren
        all_pages = _extract_all_pdf_pages(tmp_path)
        if not all_pages:
            raise HTTPException(status_code=400, detail="PDF konnte nicht gelesen werden. Moeglicherweise ist sie geschuetzt oder beschaedigt.")

        full_text = "\n\n".join(all_pages)
        total_pages = len(all_pages)

        # 2. Literaturverzeichnis finden und Referenzen extrahieren
        ref_section = _find_reference_section(all_pages)
        if not ref_section or len(ref_section.strip()) < 50:
            raise HTTPException(
                status_code=400,
                detail="Kein Literaturverzeichnis gefunden. Bitte stelle sicher, dass die PDF ein Literaturverzeichnis enthaelt."
            )

        references = _llm_extract_references(ref_section)
        if not references:
            raise HTTPException(
                status_code=400,
                detail="Keine Referenzen im Literaturverzeichnis erkannt. Moeglicherweise ist der Text nicht maschinenlesbar (Scan?)."
            )

        # Index fuer nummerierte Zuordnung setzen
        for i, ref in enumerate(references):
            ref["_ref_index"] = i + 1

        # 3. Body-Text extrahieren (Text VOR dem Literaturverzeichnis)
        ref_section_start_text = ref_section[:200]
        body_text = full_text
        ref_start_pos = full_text.find(ref_section_start_text[:100])
        if ref_start_pos > 0:
            body_text = full_text[:ref_start_pos]

        # 4. Zitation-Referenz-Abgleich (leitet Patterns aus den Referenzen ab)
        used_refs, unused_refs = _thesis_svc.match_citations_to_references(
            references, body_text
        )

        # 5. Online-Verfuegbarkeit pruefen (mit Rate-Limiting)
        not_found_online = []
        found_online = []

        for i, ref in enumerate(references):
            try:
                availability = _check_online_availability(ref)
                ref["_online"] = availability
                if availability["online_found"]:
                    found_online.append(ref)
                else:
                    not_found_online.append(ref)
            except Exception as e:
                ref["_online"] = {"online_found": False, "source": None, "error": str(e)}
                not_found_online.append(ref)

            # Rate-Limiting: 200ms zwischen Anfragen
            if i < len(references) - 1:
                time.sleep(0.2)

        # 6. Ergebnis zusammenstellen
        result = {
            "filename": file.filename,
            "total_pages": total_pages,
            "total_references": len(references),
            "references": [],
            "summary": {
                "total": len(references),
                "found_online": len(found_online),
                "not_found_online": len(not_found_online),
                "used_in_text": len(used_refs),
                "not_used_in_text": len(unused_refs),
            },
            "not_found_online": [],
            "not_used_in_text": [],
        }

        # Referenzen fuer Ausgabe vorbereiten
        for i, ref in enumerate(references):
            ref_data = {
                "index": i + 1,
                "title": ref.get("title", ""),
                "authors": ref.get("authors", ""),
                "year": ref.get("year"),
                "journal": ref.get("journal", ""),
                "doi": ref.get("doi", ""),
                "used_in_text": ref.get("_used_in_text", False),
                "online_found": ref.get("_online", {}).get("online_found", False),
                "online_source": ref.get("_online", {}).get("source"),
                "online_doi": ref.get("_online", {}).get("doi_found"),
            }
            result["references"].append(ref_data)

        result["not_found_online"] = [
            {
                "index": ref.get("_ref_index", 0),
                "title": ref.get("title", ""),
                "authors": ref.get("authors", ""),
                "year": ref.get("year"),
                "journal": ref.get("journal", ""),
                "doi": ref.get("doi", ""),
            }
            for ref in not_found_online
        ]

        result["not_used_in_text"] = [
            {
                "index": ref.get("_ref_index", 0),
                "title": ref.get("title", ""),
                "authors": ref.get("authors", ""),
                "year": ref.get("year"),
                "journal": ref.get("journal", ""),
                "doi": ref.get("doi", ""),
            }
            for ref in unused_refs
        ]

        return result

    finally:
        # Temporaere Dateien aufraeumen
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if os.path.exists(tmp_dir):
                os.rmdir(tmp_dir)
        except Exception:
            pass


@router.post("/api/analysis/build")
async def build_citation_network(depth: int = Query(1, ge=1, le=5)):
    """Baut ein Zitationsnetzwerk via OpenAlex API auf.
    depth=1: direkte Referenzen, bis depth=5: fuenf Ebenen tief.

    Thin wrapper around network_orchestrator.build_network() — all
    business logic lives in the orchestrator module since refactor #24."""
    oa_client = OpenAlexClient(_oa_mailto())
    conn = _get_conn()
    try:
        result = network_orchestrator.build_network(conn, oa_client, depth)
        network_orchestrator.persist_updates(conn, result.paper_updates)
        return result.payload
    finally:
        conn.close()


@router.post("/api/analysis/update-citations")
async def update_citations():
    """Aktualisiert OpenAlex-Zitationsdaten fuer alle Paper mit DOI."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, doi FROM papers WHERE doi != '' AND doi IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    papers = [dict(r) for r in rows]
    doi_papers = []
    for p in papers:
        doi = (p.get("doi") or "").strip()
        if doi:
            p["_doi"] = _normalize_doi(doi)
            doi_papers.append(p)

    if not doi_papers:
        return {"updated": 0}

    oa = OpenAlexClient(_oa_mailto())
    all_dois = [p["_doi"] for p in doi_papers]
    doi_to_paper = {p["_doi"]: p for p in doi_papers}
    updated = 0

    try:
        works = oa.fetch_works_by_doi(all_dois)
        now = datetime.now().isoformat()
        conn = _get_conn()
        try:
            for w in works:
                p = doi_to_paper.get(w.doi)
                if p:
                    conn.execute(
                        "UPDATE papers SET openalex_id=?, cited_by_count=?, openalex_updated_at=? WHERE id=?",
                        (w.id, w.cited_by_count, now, p["id"]),
                    )
                    updated += 1
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.warning(f"OpenAlex Update fehlgeschlagen: {e}")

    return {"updated": updated}


@router.get("/api/analysis/node-abstract")
async def get_node_abstract(doi: str = "", openalex_id: str = ""):
    """Holt den Abstract eines Papers von OpenAlex."""
    if not doi and not openalex_id:
        return {"abstract": None}

    oa = OpenAlexClient(_oa_mailto())
    abstract = oa.fetch_abstract(doi=doi, oa_id=openalex_id)
    return {"abstract": abstract or None}


@router.post("/api/analysis/chat")
async def analysis_chat(req: ChatRequest):
    """Stellt Fragen an das LLM ueber ausgewaehlte Paper. Streamt die Antwort als SSE."""
    if not Config.KICONNECT_API_KEY:
        return JSONResponse(status_code=400, content={"error": "Kein LLM API-Key konfiguriert. Bitte in den Einstellungen setzen."})

    if not req.paper_ids and not req.external_papers:
        return JSONResponse(status_code=400, content={"error": "Keine Paper ausgewaehlt."})

    # Build context from DB papers (with PDF text)
    paper_contexts = []

    if req.paper_ids:
        conn = _get_conn()
        try:
            placeholders = ",".join("?" for _ in req.paper_ids)
            rows = conn.execute(
                f"SELECT id, title, authors, year, doi, abstract, filename FROM papers WHERE id IN ({placeholders})",
                req.paper_ids,
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            p = dict(r)
            ctx = f"--- Paper: {p['title']} ---\n"
            ctx += f"Autoren: {p.get('authors', 'Unbekannt')}\n"
            if p.get("year"):
                ctx += f"Jahr: {p['year']}\n"
            if p.get("doi"):
                ctx += f"DOI: {p['doi']}\n"
            if p.get("abstract"):
                ctx += f"Abstract: {p['abstract']}\n"

            # Extract PDF text for richer context
            filename = p.get("filename", "")
            if filename:
                filepath = os.path.join(Config.ALL_DIR, os.path.basename(filename))
                if os.path.exists(filepath):
                    try:
                        text = extract_text_from_pdf(filepath, max_pages=15)
                        if text and text.strip():
                            ctx += f"\nPDF-Text (Auszug):\n{text[:8000]}\n"
                    except Exception as e:
                        logging.warning(f"PDF text extraction for chat failed: {e}")

            paper_contexts.append(ctx)

    # Build context from external papers (metadata + abstract only)
    for ep in req.external_papers:
        ctx = f"--- Paper: {ep.get('title', 'Unbekannt')} ---\n"
        ctx += f"Autoren: {ep.get('authors', 'Unbekannt')}\n"
        if ep.get("year"):
            ctx += f"Jahr: {ep['year']}\n"
        if ep.get("doi"):
            ctx += f"DOI: {ep['doi']}\n"
        if ep.get("abstract"):
            ctx += f"Abstract: {ep['abstract']}\n"
        ctx += "\n(Hinweis: Fuer dieses Paper liegt kein PDF vor. Nutze dein Wissen ueber das Paper basierend auf Titel, Autoren und Abstract.)\n"
        paper_contexts.append(ctx)

    if not paper_contexts:
        return JSONResponse(status_code=400, content={"error": "Keine Paper-Daten gefunden."})

    combined_context = "\n\n".join(paper_contexts)

    system_prompt = f"""Du bist ein Experte fuer wissenschaftliche Literatur und hilfst beim Verstehen von Forschungspapern.
Du hast Zugriff auf die folgenden Paper:

{combined_context}

Beantworte die Fragen des Nutzers basierend auf den bereitgestellten Paper-Informationen.
Sei praezise und wissenschaftlich korrekt. Zitiere relevante Stellen aus den Papern wenn moeglich.
Wenn du dir bei etwas nicht sicher bist, sage das klar. Antworte auf Deutsch, es sei denn der Nutzer fragt auf Englisch."""

    messages = [{"role": "system", "content": system_prompt}]
    # Add conversation history
    for msg in req.history[-10:]:  # Keep last 10 messages for context
        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    messages.append({"role": "user", "content": req.question})

    def generate():
        try:
            llm = llm_for("thesis_analysis")
            for token in llm.stream(messages):
                yield f"data: {json.dumps({'content': token})}\n\n"
            yield "data: [DONE]\n\n"
        except LLMClientError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logging.error(f"Chat streaming error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
