"""Router: maintenance domain.

Serves:
  POST /api/maintenance/rebuild-links       (rebuild category folders + hardlinks)
  POST /api/maintenance/update-page-counts  (read page counts from PDFs)
  POST /api/maintenance/full-refresh        (SSE stream — 6-step full refresh)
  GET  /api/maintenance/embeddings/status          (semantic search: n/m papers+chunks indexed, model, dim)
  POST /api/maintenance/embeddings/reindex         (semantic search: full paper-level indexer, #98)
  POST /api/maintenance/embeddings/reindex-chunks  (semantic search: full chunk-level indexer, #102)

Pure move from ``webapp.py`` (Backend-Modularisierung #92). HTTP contract
(paths / methods / shapes / status codes / SSE behaviour) is bit-identical to the
previous webapp.py handlers.

The handlers persist (DB writes, symlink/hardlink creation) and call the
``literature_manager`` rebuild helpers exactly as before. The Metadata Validation
step of ``full_refresh`` delegates to ``validation_policy.validate_propose`` /
``validate_apply`` — referenced as module attributes so the #78 characterization
tests can patch the single canonical target ``validation_policy.<name>``. The
rebuild-links / page-count / abstract / category / openalex / chunk helpers come
from ``literature_manager`` / ``pdf_chunking`` and are patched on this module
(``routers.maintenance.<name>``) by those tests.

No import from ``webapp`` or any other router.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import metadata_validation
import validation_policy
from context import get_conn
from embedding_index import embedding_status, reindex_chunks, reindex_papers
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from literature_manager import (
    Config,
    Database,
    categorize_with_llm,
    create_symlinks,
    extract_text_from_pdf,
    fetch_crossref_metadata,
    _rebuild_category_folders,
)
from llm_client import llm_for
from openalex_client import OpenAlexClient
from pdf_chunking import chunk_paper_to_db
from validation_policy import ValidateApplyRequest, ValidateOptions

router = APIRouter()

# Backwards-compatible alias so handler bodies copied verbatim from webapp.py
# keep their existing ``_get_conn()`` calls without modification.
_get_conn = get_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _oa_mailto() -> str:
    return Config.polite_mailto()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/maintenance/rebuild-links")
async def rebuild_links():
    """Löscht den Kategorien-Ordner vollständig und baut ihn neu auf."""
    db = _get_db()
    errors = []

    if os.path.isdir(Config.CATEGORIES_DIR):
        # Alle Dateien löschen
        for root, dirs, files in os.walk(Config.CATEGORIES_DIR):
            for fname in files:
                try:
                    os.remove(os.path.join(root, fname))
                except Exception as e:
                    errors.append(f"remove {fname}: {e}")
        # Leere Verzeichnisse von unten nach oben entfernen (entfernt veraltete Ordner)
        for root, dirs, files in os.walk(Config.CATEGORIES_DIR, topdown=False):
            if root == Config.CATEGORIES_DIR:
                continue
            try:
                os.rmdir(root)  # schlägt fehl wenn Ordner nicht leer ist — gewollt
            except OSError:
                pass

    os.makedirs(Config.CATEGORIES_DIR, exist_ok=True)
    _rebuild_category_folders(db)

    papers = db.get_all_papers()
    rebuilt = 0
    failed = 0
    for p in papers:
        try:
            create_symlinks(db, p["id"], p["filename"])
            rebuilt += 1
        except Exception as e:
            errors.append(f"symlink {p['filename']}: {e}")
            failed += 1

    removed = 0  # Ordner werden still entfernt, keine genaue Zählung nötig
    return {"status": "ok", "rebuilt": rebuilt, "failed": failed, "removed": removed, "errors": errors}


@router.post("/api/maintenance/update-page-counts")
async def update_page_counts():
    """Liest für alle Paper die Seitenanzahl aus der PDF und speichert sie in der DB."""
    import fitz as _fitz
    conn = _get_conn()
    try:
        papers = conn.execute("SELECT id, filename FROM papers").fetchall()
        updated = 0
        failed = 0
        for p in papers:
            filepath = os.path.join(Config.ALL_DIR, os.path.basename(p["filename"]))
            try:
                doc = _fitz.open(filepath)
                page_count = len(doc)
                doc.close()
                conn.execute("UPDATE papers SET page_count = ? WHERE id = ?", (page_count, p["id"]))
                updated += 1
            except Exception:
                failed += 1
        conn.commit()
        return {"status": "ok", "updated": updated, "failed": failed}
    finally:
        conn.close()


@router.post("/api/maintenance/full-refresh")
async def full_refresh():
    """Vollständiger Re-Refresh aller Paper:
    1. Seitenanzahl (page_count)
    2. Metadaten-Validierung via DOI + CrossRef
    3. Fehlende Abstracts holen (CrossRef → LLM)
    4. Fehlende Kategorien per LLM zuweisen
    5. OpenAlex Zitationszahlen aktualisieren
    6. RAG-Chunks erstellen (nur wenn noch keine vorhanden)
    """
    import fitz as _fitz

    db = _get_db()

    async def generate():
        conn = _get_conn()
        try:
            papers = conn.execute(
                "SELECT id, filename, title, doi, abstract, openalex_id FROM papers ORDER BY id"
            ).fetchall()
            papers = [dict(p) for p in papers]
        finally:
            conn.close()

        total = len(papers)
        if total == 0:
            yield f"data: {json.dumps({'type': 'done', 'message': 'Keine Paper gefunden.'})}\n\n"
            return

        stats = {
            "page_count": 0, "validated": 0, "abstracts": 0,
            "categorized": 0, "openalex": 0, "chunks": 0, "errors": 0
        }

        def sse(msg, current=0, step=""):
            pct = int((current / total) * 100) if total else 0
            return f"data: {json.dumps({'type': 'progress', 'message': msg, 'current': current, 'total': total, 'percent': pct, 'step': step, 'stats': stats})}\n\n"

        # ── SCHRITT 1: Seitenanzahl ──────────────────────────────────────────
        yield sse("Schritt 1/6: Seitenanzahl ermitteln...", 0, "page_count")
        for i, p in enumerate(papers):
            filepath = os.path.join(Config.ALL_DIR, os.path.basename(p["filename"]))
            try:
                doc = _fitz.open(filepath)
                pc = len(doc)
                doc.close()
                conn = _get_conn()
                try:
                    conn.execute("UPDATE papers SET page_count = ? WHERE id = ?", (pc, p["id"]))
                    conn.commit()
                finally:
                    conn.close()
                stats["page_count"] += 1
            except Exception:
                stats["errors"] += 1
            if i % 10 == 0:
                yield sse(f"Schritt 1/6: Seitenanzahl — {i+1}/{total}", i+1, "page_count")
        yield sse(f"✓ Seitenanzahl: {stats['page_count']} Paper aktualisiert", total, "page_count")

        # ── SCHRITT 2: Metadaten-Validierung ────────────────────────────────
        # Background pass: auto-apply only high-Confidence Proposals; low-
        # confidence Papers stay unchanged (no popup is reachable from here).
        yield sse("Schritt 2/6: Metadaten validieren (DOI + CrossRef)...", 0, "validate")
        for i, p in enumerate(papers):
            try:
                opts = ValidateOptions(use_llm=False, pages=15)
                proposal = await validation_policy.validate_propose(p["id"], opts)
                if (
                    proposal.get("confidence") == "high"
                    and (proposal.get("changes") or proposal.get("category_suggestions"))
                ):
                    apply_req = ValidateApplyRequest(
                        changes=proposal.get("changes") or {},
                        category_assignments=proposal.get("category_suggestions") or [],
                    )
                    await validation_policy.validate_apply(p["id"], apply_req)
                stats["validated"] += 1
            except Exception:
                stats["errors"] += 1
            if i % 5 == 0:
                yield sse(f"Schritt 2/6: Metadaten — {i+1}/{total}", i+1, "validate")
        yield sse(f"✓ Metadaten: {stats['validated']} Paper validiert", total, "validate")

        # ── SCHRITT 3: Fehlende Abstracts ────────────────────────────────────
        yield sse("Schritt 3/6: Fehlende Abstracts holen...", 0, "abstracts")
        conn = _get_conn()
        try:
            papers_refreshed = conn.execute(
                "SELECT id, filename, doi, abstract, ocr_text FROM papers ORDER BY id"
            ).fetchall()
            papers_refreshed = [dict(p) for p in papers_refreshed]
        finally:
            conn.close()

        for i, p in enumerate(papers_refreshed):
            if p.get("abstract"):
                continue  # bereits vorhanden
            try:
                abstract = ""
                # Versuch 1: CrossRef
                if p.get("doi"):
                    cr = fetch_crossref_metadata(p["doi"])
                    if cr and cr.get("abstract", "").strip():
                        abstract = cr["abstract"].strip()[:5000]
                # Versuch 2: LLM aus PDF-Text
                if not abstract and Config.KICONNECT_API_KEY:
                    filepath = os.path.join(Config.ALL_DIR, os.path.basename(p["filename"]))
                    pdf_text = ""
                    if os.path.exists(filepath):
                        pdf_text = extract_text_from_pdf(filepath, max_pages=5)
                    if not pdf_text.strip():
                        pdf_text = p.get("ocr_text", "") or ""
                    if pdf_text.strip():
                        # metadata_validation owns prompt + KEIN_ABSTRACT sentinel
                        found = metadata_validation.llm_find_abstract_in_text(
                            llm_for("abstract"),
                            p.get("title", "") or "", p.get("authors", "") or "",
                            pdf_text[:4000],
                        )
                        if len(found) > 50:
                            abstract = found[:5000]

                if abstract:
                    conn = _get_conn()
                    try:
                        conn.execute(
                            "UPDATE papers SET abstract = ?, abstract_source = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (abstract, "refresh", p["id"])
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    stats["abstracts"] += 1
            except Exception as e:
                logging.warning(f"Abstract-Refresh fehlgeschlagen für Paper {p['id']}: {e}")
                stats["errors"] += 1
            if i % 5 == 0:
                yield sse(f"Schritt 3/6: Abstracts — {i+1}/{total}", i+1, "abstracts")
        yield sse(f"✓ Abstracts: {stats['abstracts']} neu gefunden", total, "abstracts")

        # ── SCHRITT 4: Fehlende Kategorien ───────────────────────────────────
        yield sse("Schritt 4/6: Fehlende Kategorien zuweisen...", 0, "categories")
        if Config.KICONNECT_API_KEY:
            conn = _get_conn()
            try:
                uncategorized_ids = [
                    r["id"] for r in conn.execute(
                        "SELECT p.id FROM papers p LEFT JOIN paper_categories pc ON p.id = pc.paper_id WHERE pc.paper_id IS NULL"
                    ).fetchall()
                ]
            finally:
                conn.close()

            categories_json = db.get_categories()
            category_tree = db.get_category_tree()

            for i, pid in enumerate(uncategorized_ids):
                try:
                    conn = _get_conn()
                    try:
                        row = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
                    finally:
                        conn.close()
                    if not row:
                        continue
                    paper = dict(row)
                    filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
                    pdf_text = ""
                    if os.path.exists(filepath):
                        pdf_text = extract_text_from_pdf(filepath, max_pages=5)
                    assignments = categorize_with_llm(paper, pdf_text, categories_json, category_tree)
                    if assignments:
                        for cat_id, confidence in assignments:
                            db.assign_category(pid, cat_id, confidence, "llm_refresh")
                        stats["categorized"] += 1
                except Exception as e:
                    logging.warning(f"Kategorisierungs-Refresh fehlgeschlagen für Paper {pid}: {e}")
                    stats["errors"] += 1
                yield sse(f"Schritt 4/6: Kategorien — {i+1}/{len(uncategorized_ids)} unkategorisiert", i+1, "categories")
        yield sse(f"✓ Kategorien: {stats['categorized']} Paper neu kategorisiert", total, "categories")

        # ── SCHRITT 5: OpenAlex Zitationen ───────────────────────────────────
        yield sse("Schritt 5/6: OpenAlex Zitationszahlen aktualisieren...", 0, "openalex")
        try:
            conn = _get_conn()
            try:
                doi_papers = conn.execute(
                    "SELECT id, doi FROM papers WHERE doi != '' AND doi IS NOT NULL"
                ).fetchall()
                doi_papers = [dict(p) for p in doi_papers]
            finally:
                conn.close()

            oa = OpenAlexClient(_oa_mailto())
            doi_to_id = {p["doi"]: p["id"] for p in doi_papers}
            all_dois = [p["doi"] for p in doi_papers]
            try:
                works = oa.fetch_works_by_doi(all_dois)
                conn = _get_conn()
                try:
                    for work in works:
                        paper_id = doi_to_id.get(work.doi)
                        if paper_id:
                            conn.execute(
                                "UPDATE papers SET openalex_id = ?, cited_by_count = ?, openalex_updated_at = ? WHERE id = ?",
                                (work.id, work.cited_by_count, datetime.now().isoformat(), paper_id),
                            )
                            stats["openalex"] += 1
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logging.warning(f"OpenAlex-Batch fehlgeschlagen: {e}")
            yield sse(f"Schritt 5/6: OpenAlex — {len(doi_papers)}/{len(doi_papers)}", len(doi_papers), "openalex")
        except Exception as e:
            logging.error(f"OpenAlex-Refresh fehlgeschlagen: {e}")
            stats["errors"] += 1
        yield sse(f"✓ OpenAlex: {stats['openalex']} Zitationszahlen aktualisiert", total, "openalex")

        # ── SCHRITT 6: RAG-Chunks ────────────────────────────────────────────
        yield sse("Schritt 6/6: RAG-Chunks erstellen...", 0, "chunks")
        for i, p in enumerate(papers):
            filepath = os.path.join(Config.ALL_DIR, os.path.basename(p["filename"]))
            if not os.path.exists(filepath):
                continue
            try:
                n = chunk_paper_to_db(p["id"], filepath)
                if n > 0:
                    stats["chunks"] += 1
            except Exception as e:
                logging.warning(f"Chunking fehlgeschlagen für Paper {p['id']}: {e}")
                stats["errors"] += 1
            if i % 10 == 0:
                yield sse(f"Schritt 6/6: Chunks — {i+1}/{total}", i+1, "chunks")
        yield sse(f"✓ Chunks: {stats['chunks']} Paper gechunkt", total, "chunks")

        yield f"data: {json.dumps({'type': 'done', 'stats': stats, 'total': total})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Semantische Suche Phase 1 (#98): Paper-Embeddings-Indexer
# ---------------------------------------------------------------------------

@router.get("/api/maintenance/embeddings/status")
async def get_embedding_status():
    """n von m Papern UND n von m Chunks indexiert (#102), aktuell
    konfiguriertes Modell + Dimension.

    Funktioniert auch ohne konfiguriertes ``LLM_EMBED_MODEL`` (n=0, model="") —
    kein 500er, die semantische Suche ist dann optional ausgeblendet (PRD
    Entscheidung 5)."""
    return embedding_status()


@router.post("/api/maintenance/embeddings/reindex")
async def reindex_embeddings():
    """Vollindex-Lauf ueber alle Paper (Titel+Abstract, Ersatz-Abstract aus
    paper_chunks). Wiederaufnehmbar/idempotent: bereits mit dem aktuellen
    Modell indexierte Paper werden uebersprungen; ein Modellwechsel in den
    Settings macht bestehende Vektoren erkennbar veraltet und re-embedded sie."""
    return reindex_papers()


@router.post("/api/maintenance/embeddings/reindex-chunks")
async def reindex_chunk_embeddings():
    """Vollindex-Lauf ueber alle Chunks (Passage-Retrieval, Phase 3, #102).

    Embedding-Input pro Chunk kontextangereichert (Titel[+Jahr/Journal] +
    Chunk-Text, PRD Entscheidung 9); der gespeicherte ``paper_chunks.chunk_text``
    bleibt unveraendert. Wiederaufnehmbar/idempotent wie der Paper-Indexer."""
    return reindex_chunks()
