"""Router: research-chat domain.

Serves:
  POST /api/research-chat/ask         (RAG-based Q&A over chunked papers)
  POST /api/research-chat/chunk-papers (on-demand chunking of selected papers)
  GET  /api/research-chat/chunk-status (chunk status per paper)

Pure move from ``webapp.py`` (Backend-Modularisierung #89).  HTTP contract
(paths / methods / shapes / status codes) is bit-identical to the previous
webapp.py handlers, EXCEPT that ``/ask`` retrieval was upgraded from BM25-only
to the two-stage hybrid search (#103, ``docs/PRD-semantische-suche.md`` Phase
3, Entscheidung 5/7): chunk-level cosine ranking (restricted to the caller's
``paper_ids`` -- no paper-level stage needed, the user already picked the
papers) fused with BM25 via Reciprocal Rank Fusion, same recipe as
``routers/search.py``'s ``/api/search/passages``. BM25-only stays the exact
fallback (byte-identical to the pre-#103 behavior) whenever no embedding
model is configured, no chunk vectors exist for the selected chunks, or the
query-embedding HTTP call fails (PRD Entscheidung 5 -- degradation, not hard
failure). The chat LLM stays fully decoupled from retrieval (PRD Entscheidung
7): it only ever sees the winning chunks as plain text, never a "mode" or a
score.

BM25 ranking + RRF fusion + context assembly live in ``services.research_rag``
(PURE, deterministic); cosine ranking lives in ``services.semantic_search``
(PURE, deterministic) -- both reused as-is, no new ranking math. The router
handles DB I/O (loading chunks/vectors), persistence via
``pdf_chunking.chunk_paper_to_db``, the embedding HTTP call
(``llm_client.embed_texts``), and LLM calls -- all I/O, so all in the router,
not the services.

Shared resources come from ``context`` / ``literature_manager`` / ``llm_client``
/ ``pdf_chunking`` / ``embedding_index`` / ``services.research_rag`` /
``services.semantic_search``.
No import from ``webapp`` or any other router (this module does NOT import
``routers.search`` -- the hybrid-retrieval helper is reimplemented locally
against the shared pure services, per CLAUDE.md's "a router never imports
another router").
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import services.research_rag as _rag
import services.semantic_search as _semantic
from context import get_conn
from embedding_index import embed_chunks_for_paper, unpack_vector
from literature_manager import Config
from llm_client import LLMClientError, embed_texts, llm_for
from pdf_chunking import chunk_paper_to_db

router = APIRouter()

# Alias so handler bodies relocated verbatim from webapp.py keep working.
_get_conn = get_conn

CHAT_TOP_K_CHUNKS = 8
RRF_K = 60


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str
    paper_ids: List[int]
    history: Optional[list] = None
    extra_context: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# LLM helper (performs I/O — lives in the router, not the service)
# ---------------------------------------------------------------------------

def _research_chat_llm(
    question: str,
    context: str,
    chat_history: list = None,
    extra_context: list[str] | None = None,
) -> str:
    """Sendet Frage + Kontext an das LLM und gibt die Antwort zurueck.

    extra_context: optionale Liste von Klartextbloecken (z.B. Dissertationskapitel),
    die als zusaetzlicher Kontext an das LLM weitergegeben werden.  Die Bloecke
    werden NACH dem Paper-Kontext in den User-Turn eingefuegt.  Wird kein
    extra_context uebergeben, bleibt das Verhalten unveraendert.
    """
    if not Config.KICONNECT_API_KEY:
        return "Fehler: Kein LLM-API-Key konfiguriert. Bitte in den Einstellungen einen KI Connect API Key hinterlegen."

    system_prompt = """Du bist ein wissenschaftlicher Assistent, der Fragen zu Forschungspapieren beantwortet.
Du erhaeltst Textausschnitte aus wissenschaftlichen Papieren als Kontext.

REGELN:
- Beantworte die Frage NUR basierend auf dem gegebenen Kontext
- Zitiere JEDE Information mit [Quelle X] direkt im Text, wobei X die Quellennummer ist
- WICHTIG: Verwende ALLE relevanten Quellen! Wenn Information aus Quelle 1, 2 und 3 stammt, schreibe z.B. "... [Quelle 1] [Quelle 3]" oder "Laut [Quelle 2] ..."
- Jeder Absatz und jede Aussage MUSS mindestens eine [Quelle X] Markierung enthalten
- Wenn du die Frage nicht aus dem Kontext beantworten kannst, sage das ehrlich
- Antworte auf Deutsch, es sei denn die Frage ist auf Englisch
- Sei praezise und wissenschaftlich korrekt
- Fasse relevante Informationen aus mehreren Quellen zusammen wenn moeglich
- Strukturiere die Antwort uebersichtlich mit Absaetzen"""

    messages = [{"role": "system", "content": system_prompt}]

    # Chat-History einfuegen (letzte 6 Nachrichten)
    if chat_history:
        for msg in chat_history[-6:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    user_message = f"""KONTEXT (Textausschnitte aus Papieren):
{context}"""

    # Optionale zusaetzliche Kontextbloecke (z.B. Dissertationskapitel vom SPA injiziert)
    if extra_context:
        for block in extra_context:
            user_message += f"\n\nWEITERER KONTEXT:\n{block}"

    user_message += f"""

FRAGE: {question}

Beantworte die Frage ausfuehrlich basierend auf dem Kontext.
WICHTIG: Markiere JEDE Aussage im Text mit [Quelle X] Verweisen. Nutze ALLE relevanten Quellen, nicht nur eine.
Beispiel: Material X hat Eigenschaft Y [Quelle 1] und wird fuer Z verwendet [Quelle 3]."""

    messages.append({"role": "user", "content": user_message})

    try:
        llm = llm_for("research_chat")
        return llm.complete(messages, timeout=90, temperature=0.3)
    except LLMClientError as e:
        logging.warning(f"Research-Chat LLM Fehler: {e}")
        return f"LLM-Fehler. Bitte versuche es erneut."
    except Exception as e:
        logging.error(f"Research-Chat LLM Exception: {e}")
        return f"Fehler bei der LLM-Kommunikation: {str(e)}"


# ---------------------------------------------------------------------------
# Hybrid chunk retrieval (#103): chunk-level cosine (restricted to the
# caller's paper_ids) fused with BM25 via RRF -- degrades to plain BM25
# whenever embeddings are unavailable.
# ---------------------------------------------------------------------------

def _hybrid_top_chunks(conn, question: str, chunks: list, top_k: int = CHAT_TOP_K_CHUNKS) -> tuple:
    """Ranks ``chunks`` (already scoped to the requested ``paper_ids``) for the
    given ``question``, using cosine + BM25 fused via RRF when an embedding
    model is configured and chunk vectors exist for these chunks -- otherwise
    BM25 alone, EXACTLY the pre-#103 behavior (no embedding call is even
    attempted, so this path is byte-identical to before).

    No paper-level stage is needed here (unlike ``/api/search/passages``):
    the caller already restricted the pool to the papers the user picked for
    this chat, so this only ever ranks chunks, not papers first.

    Returns ``(top_chunks, mode)`` where ``mode`` is ``"bm25"``, ``"semantic"``
    (cosine only -- no chunk matched lexically) or ``"hybrid"`` (both
    contributed), mirroring ``routers/search.py``'s convention.
    """
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model or not chunks:
        return _rag.bm25_search(question, chunks, top_k=top_k), "bm25"

    chunk_ids = [c["id"] for c in chunks]
    placeholders = ",".join("?" for _ in chunk_ids)
    vector_rows = conn.execute(
        f"SELECT chunk_id, vector FROM chunk_embeddings WHERE model = ? AND chunk_id IN ({placeholders})",
        [model] + chunk_ids,
    ).fetchall()
    if not vector_rows:
        return _rag.bm25_search(question, chunks, top_k=top_k), "bm25"

    try:
        query_vector = embed_texts([question], mode="query")[0]
    except Exception as e:
        logging.warning(f"Research-Chat: Query-Embedding fehlgeschlagen: {e}")
        return _rag.bm25_search(question, chunks, top_k=top_k), "bm25"

    by_id = {c["id"]: c for c in chunks}
    chunk_candidates = []
    for row in vector_rows:
        c = by_id.get(row["chunk_id"])
        if c is None:
            continue
        chunk_candidates.append({**c, "vector": unpack_vector(row["vector"])})
    if not chunk_candidates:
        return _rag.bm25_search(question, chunks, top_k=top_k), "bm25"

    # Full cosine ranking (not truncated yet) -- RRF needs the whole rank
    # list, truncation to top_k happens after fusion.
    cosine_full = _semantic.rank_by_cosine(query_vector, chunk_candidates, top_k=len(chunk_candidates))
    bm25_hits = _rag.bm25_search(question, chunks, top_k=len(chunks))
    if not bm25_hits:
        # No lexical match at all -- cosine is the sole signal.
        return cosine_full[:top_k], "semantic"

    cosine_ranking = [c["id"] for c in cosine_full]
    bm25_ranking = [h["id"] for h in bm25_hits]
    cosine_by_id = {c["id"]: c for c in cosine_full}
    bm25_by_id = {h["id"]: h for h in bm25_hits}
    fused = _rag.reciprocal_rank_fusion([cosine_ranking, bm25_ranking], k=RRF_K)

    results = []
    for chunk_id, score in fused[:top_k]:
        base = cosine_by_id.get(chunk_id) or bm25_by_id.get(chunk_id)
        if base is None:
            continue
        entry = dict(base)
        entry["score"] = score
        results.append(entry)
    return results, "hybrid"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/research-chat/ask")
async def research_chat_ask(req: ChatRequest):
    """Beantwortet eine Frage basierend auf den angegebenen Papieren (RAG)."""
    if not req.paper_ids:
        raise HTTPException(status_code=400, detail="Keine Paper ausgewaehlt")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Keine Frage gestellt")

    conn = _get_conn()
    try:
        # Paper-Infos laden
        placeholders = ",".join("?" for _ in req.paper_ids)
        papers = conn.execute(
            f"SELECT id, title, authors, year, filename FROM papers WHERE id IN ({placeholders})",
            req.paper_ids,
        ).fetchall()
        paper_info = {p["id"]: dict(p) for p in papers}

        if not paper_info:
            raise HTTPException(status_code=404, detail="Keine der angegebenen Paper gefunden")

        # Chunks laden
        chunks = conn.execute(
            f"""SELECT id, paper_id, chunk_index, page_start, chunk_text
                FROM paper_chunks WHERE paper_id IN ({placeholders})
                ORDER BY paper_id, chunk_index""",
            req.paper_ids,
        ).fetchall()
        chunks = [dict(c) for c in chunks]

        if not chunks:
            # Versuche on-the-fly zu chunken
            chunked_count = 0
            for pid in req.paper_ids:
                pinfo = paper_info.get(pid)
                if pinfo:
                    pdf_path = os.path.join(Config.ALL_DIR, pinfo["filename"])
                    if os.path.exists(pdf_path):
                        n = chunk_paper_to_db(pid, pdf_path)
                        chunked_count += n
                        # #153: frisch erzeugte Chunks sofort embedden, sonst
                        # beantwortet der Chat genau diese Frage noch rein
                        # lexikalisch (degradiert selbst, wirft nie).
                        if n > 0:
                            embed_chunks_for_paper(pid)

            if chunked_count > 0:
                # Nochmal laden
                chunks = conn.execute(
                    f"""SELECT id, paper_id, chunk_index, page_start, chunk_text
                        FROM paper_chunks WHERE paper_id IN ({placeholders})
                        ORDER BY paper_id, chunk_index""",
                    req.paper_ids,
                ).fetchall()
                chunks = [dict(c) for c in chunks]

            if not chunks:
                return {
                    "answer": "Keine Textinhalte verfuegbar. Die ausgewaehlten Paper konnten nicht analysiert werden (eventuell gescannte PDFs ohne OCR).",
                    "sources": [],
                    "chunks_total": 0,
                }

        # Hybrid-Suche (cosine + BM25 via RRF, degradiert auf reines BM25
        # ohne Embedding-Modell/-Index -- #103)
        top_chunks, retrieval_mode = _hybrid_top_chunks(conn, req.question, chunks, top_k=CHAT_TOP_K_CHUNKS)

        if not top_chunks:
            return {
                "answer": "Keine relevanten Textpassagen zu dieser Frage gefunden. Versuche eine andere Formulierung.",
                "sources": [],
                "chunks_total": len(chunks),
            }

        # Kontext bauen und LLM fragen
        context, sources = _rag.build_chat_context(top_chunks, paper_info)
        answer = _research_chat_llm(req.question, context, req.history, req.extra_context)

        return {
            "answer": answer,
            "sources": sources if not answer.startswith("Fehler:") else [],
            "chunks_total": len(chunks),
            "chunks_used": len(top_chunks),
            "mode": retrieval_mode,
        }
    finally:
        conn.close()


@router.post("/api/research-chat/chunk-papers")
async def chunk_papers(paper_ids: List[int] = Query(...)):
    """Chunked die angegebenen Paper (falls noetig). Gibt Status pro Paper zurueck."""
    results = []
    conn = _get_conn()
    try:
        for pid in paper_ids:
            row = conn.execute(
                "SELECT id, title, filename FROM papers WHERE id = ?", (pid,)
            ).fetchone()
            if not row:
                results.append({"paper_id": pid, "status": "not_found", "chunks": 0})
                continue

            # Pruefen ob bereits gechunkt
            existing = conn.execute(
                "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", (pid,)
            ).fetchone()[0]
            if existing > 0:
                results.append({"paper_id": pid, "status": "already_chunked", "chunks": existing})
                continue

            pdf_path = os.path.join(Config.ALL_DIR, row["filename"])
            if not os.path.exists(pdf_path):
                results.append({"paper_id": pid, "status": "pdf_missing", "chunks": 0})
                continue

            n = chunk_paper_to_db(pid, pdf_path)
            # #153: Chunk-Vektoren gleich mitziehen, damit die Hybrid-Suche fuer
            # dieses Paper nicht bis zum naechsten Maintenance-Reindex auf reines
            # BM25 zurueckfaellt.
            n_vectors = embed_chunks_for_paper(pid) if n > 0 else 0
            results.append({
                "paper_id": pid,
                "status": "chunked" if n > 0 else "no_text",
                "chunks": n,
                "chunk_vectors": n_vectors,
                "title": row["title"],
            })
    finally:
        conn.close()

    return {
        "results": results,
        "total_chunks": sum(r["chunks"] for r in results),
    }


@router.get("/api/research-chat/chunk-status")
async def chunk_status(paper_ids: str = Query(...)):
    """Gibt den Chunk-Status fuer die angegebenen Paper-IDs zurueck.
    paper_ids: kommagetrennte IDs."""
    try:
        ids = [int(x.strip()) for x in paper_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungueltige Paper-IDs")

    if not ids:
        return {"papers": [], "all_chunked": True}

    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT p.id, p.title,
                       COALESCE((SELECT COUNT(*) FROM paper_chunks pc WHERE pc.paper_id = p.id), 0) as chunk_count
                FROM papers p WHERE p.id IN ({placeholders})""",
            ids,
        ).fetchall()

        papers = []
        all_chunked = True
        for r in rows:
            chunked = r["chunk_count"] > 0
            if not chunked:
                all_chunked = False
            papers.append({
                "paper_id": r["id"],
                "title": r["title"],
                "chunk_count": r["chunk_count"],
                "chunked": chunked,
            })

        return {"papers": papers, "all_chunked": all_chunked}
    finally:
        conn.close()
