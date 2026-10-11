"""Router: maintenance domain.

Serves:
  POST /api/maintenance/recompute          (SSE stream — recompute technical data, #213)
  GET  /api/maintenance/embeddings/status  (semantic search: n/m items + passages indexed,
                                            model, dim, stale)

Recompute is block ② of Settings → Refresh, and the one action behind the
context hints (AI models: "the search index no longer matches the model";
Library: "rebuild the category folders?"). It recomputes only what LocalBib
derives — page counts, citation counts, text passages, the search index and the
category folders — and never writes a field the user curates. Contents the user
curates go through the reviewed refresh (``routers/refresh_review.py``).

It replaced the full refresh and the single maintenance endpoints (page counts,
category links, the two index runs) — none of them was in the Add-on Contract's
stable endpoint table, and no Add-on called them.

The step order, the event shape and the DOI matching live in
``services/recompute.py``; this module runs the steps and persists. Blocking
work (PDF reading, HTTP, embedding, the file system) runs in a worker thread one
item or one package at a time, so the stream reports progress in between and a
cancelled request stops after the item it is on.

No import from ``webapp`` or any other router.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List

from context import get_conn
from embedding_index import (
    INDEX_BATCH_SIZE,
    embed_chunk_batch,
    embed_paper_batch,
    embedding_status,
    plan_chunk_index,
    plan_paper_index,
    purge_stale_vectors,
)
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from filenames import clear_category_folders, stored_pdf_path
from literature_manager import (
    Config,
    Database,
    _rebuild_category_folders,
    create_symlinks,
)
from openalex_client import BATCH_SIZE as OPENALEX_BATCH_SIZE
from openalex_client import OpenAlexClient
from pdf_chunking import chunk_paper_to_db
from pydantic import BaseModel
from services import recompute

router = APIRouter()

# One run at a time: two runs would clear and refill the same category folders
# under each other's feet and embed the same vectors twice. Set and released
# inside the stream, so a request whose stream never starts holds nothing.
_RUN = {"active": False}


class RecomputeRequest(BaseModel):
    steps: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(frame: dict) -> str:
    return f"data: {json.dumps(frame)}\n\n"


def _load_papers() -> list:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, filename, doi FROM papers ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _with_pdf(papers: list) -> list:
    """``[(paper, pdf path)]`` for the items whose PDF is on disk. A missing file
    is the Diagnosis' finding, not an error of a recompute step."""
    out = []
    for p in papers:
        path = stored_pdf_path(p.get("filename") or "")
        if path:
            out.append((p, path))
    return out


def _page_count_one(paper_id: int, path: str) -> None:
    import fitz  # PyMuPDF — imported here like everywhere it reads a page count
    doc = fitz.open(path)
    try:
        pages = len(doc)
    finally:
        doc.close()
    conn = get_conn()
    try:
        conn.execute("UPDATE papers SET page_count = ? WHERE id = ?", (pages, paper_id))
        conn.commit()
    finally:
        conn.close()


def _write_citations(pairs: list) -> None:
    now = datetime.now().isoformat()
    conn = get_conn()
    try:
        for paper_id, work in pairs:
            conn.execute(
                "UPDATE papers SET openalex_id = ?, cited_by_count = ?, openalex_updated_at = ? WHERE id = ?",
                (work.id, work.cited_by_count, now, paper_id),
            )
        conn.commit()
    finally:
        conn.close()


def _chunked_ids() -> set:
    conn = get_conn()
    try:
        return {r["paper_id"] for r in conn.execute("SELECT DISTINCT paper_id FROM paper_chunks").fetchall()}
    finally:
        conn.close()


def _prepare_category_folders(db: Database) -> None:
    """Empties the category folders (every earlier entry, whatever its link
    mode left behind) and recreates one folder per category."""
    clear_category_folders()
    os.makedirs(Config.CATEGORIES_DIR, exist_ok=True)
    _rebuild_category_folders(db)


# ---------------------------------------------------------------------------
# The five steps — each an async generator of progress frames
# ---------------------------------------------------------------------------

async def _page_counts(steps, papers, stats):
    s = stats["page_counts"]
    todo = _with_pdf(papers)
    yield recompute.progress_event(steps, "page_counts", 0, len(todo), stats)
    for i, (paper, path) in enumerate(todo, 1):
        try:
            await asyncio.to_thread(_page_count_one, paper["id"], path)
            s["updated"] += 1
        except Exception as e:
            logging.warning(f"Seitenzahl nicht lesbar fuer Paper {paper['id']}: {e}")
            s["errors"] += 1
        yield recompute.progress_event(steps, "page_counts", i, len(todo), stats)


async def _citations(steps, papers, stats):
    s = stats["citations"]
    todo = [p for p in papers if (p.get("doi") or "").strip()]
    yield recompute.progress_event(steps, "citations", 0, len(todo), stats)
    if not todo:
        return
    client = OpenAlexClient(Config.polite_mailto())
    for start in range(0, len(todo), OPENALEX_BATCH_SIZE):
        if start:
            await asyncio.sleep(0.15)  # polite pacing, as the client does between its own packages
        batch = todo[start:start + OPENALEX_BATCH_SIZE]
        try:
            works = await asyncio.to_thread(
                client.fetch_works_by_doi, [p["doi"] for p in batch], raise_errors=True)
            pairs = recompute.match_works(batch, works)
            await asyncio.to_thread(_write_citations, pairs)
            s["updated"] += len({pid for pid, _work in pairs})
        except Exception as e:
            logging.warning(f"OpenAlex-Paket fehlgeschlagen ({len(batch)} Eintraege): {e}")
            s["errors"] += len(batch)
        yield recompute.progress_event(steps, "citations", start + len(batch), len(todo), stats)


async def _passages(steps, papers, stats):
    """Passages for every item with a PDF that has none yet. Existing passages
    stay — re-cutting them would throw their search vectors away."""
    s = stats["passages"]
    chunked = await asyncio.to_thread(_chunked_ids)
    todo = [(p, path) for p, path in _with_pdf(papers) if p["id"] not in chunked]
    yield recompute.progress_event(steps, "passages", 0, len(todo), stats)
    for i, (paper, path) in enumerate(todo, 1):
        try:
            s["updated"] += await asyncio.to_thread(chunk_paper_to_db, paper["id"], path)
        except Exception as e:
            logging.warning(f"Chunking fehlgeschlagen fuer Paper {paper['id']}: {e}")
            s["errors"] += 1
        yield recompute.progress_event(steps, "passages", i, len(todo), stats)


async def _search_index(steps, papers, stats):
    """Embeds every item and passage without a vector of the bound model; after
    a run without errors, vectors of any other model are dropped."""
    s = stats["search_index"]
    model = Config.embed_model()
    if not model:
        # The role was unbound while the run was underway.
        s["errors"] += 1
        yield recompute.progress_event(steps, "search_index", 0, 0, stats)
        return
    item_plan = await asyncio.to_thread(plan_paper_index, model)
    chunk_plan = await asyncio.to_thread(plan_chunk_index, model)
    work = ([("items", embed_paper_batch, item_plan["pending"])]
            + [("passages", embed_chunk_batch, chunk_plan["pending"])])
    total = len(item_plan["pending"]) + len(chunk_plan["pending"])
    done = 0
    yield recompute.progress_event(steps, "search_index", 0, total, stats)
    for counter, embed, pending in work:
        for start in range(0, len(pending), INDEX_BATCH_SIZE):
            batch = pending[start:start + INDEX_BATCH_SIZE]
            try:
                indexed, errors, _dim = await asyncio.to_thread(embed, batch, model)
            except Exception as e:
                logging.warning(f"Suchindex-Paket fehlgeschlagen ({len(batch)}): {e}")
                indexed, errors = 0, len(batch)
            s[counter] += indexed
            s["updated"] += indexed
            s["errors"] += errors
            done += len(batch)
            yield recompute.progress_event(steps, "search_index", done, total, stats)
    if not s["errors"]:
        await asyncio.to_thread(purge_stale_vectors, model)


async def _category_folders(steps, papers, stats):
    """Rebuilds the category folders in the current link mode. Never touches
    ``Config.ALL_DIR`` — the PDFs themselves."""
    s = stats["category_folders"]
    db = Database(Config.DB_PATH)
    todo = _with_pdf(papers)
    yield recompute.progress_event(steps, "category_folders", 0, len(todo), stats)
    try:
        await asyncio.to_thread(_prepare_category_folders, db)
    except Exception as e:
        logging.error(f"Kategorie-Ordner konnten nicht geleert werden: {e}")
        s["errors"] += 1
        return
    for i, (paper, _path) in enumerate(todo, 1):
        try:
            placed = await asyncio.to_thread(create_symlinks, db, paper["id"], paper["filename"])
            if placed["placed"]:
                s["updated"] += 1
            if placed["failed"]:
                s["errors"] += 1
            s["fallbacks"] += placed["fallbacks"]
        except Exception as e:
            logging.warning(f"Kategorie-Eintraege fehlgeschlagen fuer Paper {paper['id']}: {e}")
            s["errors"] += 1
        yield recompute.progress_event(steps, "category_folders", i, len(todo), stats)


_RUNNERS = {
    "page_counts": _page_counts,
    "citations": _citations,
    "passages": _passages,
    "search_index": _search_index,
    "category_folders": _category_folders,
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/maintenance/recompute")
async def recompute_technical_data(data: RecomputeRequest):
    """Recompute the chosen steps, streamed: one ``progress`` frame per item
    (per package for citations and the search index), then ``done`` with the
    numbers per step and the error count. ``search_index`` needs a bound
    embedding role and is refused without one."""
    try:
        steps = recompute.normalize_steps(data.steps)
    except recompute.StepError as e:
        raise HTTPException(status_code=400, detail=e.code)
    if "search_index" in steps and not Config.llm_ready("embedding"):
        raise HTTPException(status_code=409, detail="error.recompute_embedding_not_ready")

    async def generate():
        if _RUN["active"]:
            yield _sse({"type": "error", "message": "error.recompute_running"})
            return
        _RUN["active"] = True
        try:
            papers = await asyncio.to_thread(_load_papers)
            stats = recompute.new_stats(steps)
            for step in steps:
                async for frame in _RUNNERS[step](steps, papers, stats):
                    yield _sse(frame)
            yield _sse(recompute.done_event(steps, stats, len(papers)))
        finally:
            _RUN["active"] = False

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/maintenance/embeddings/status")
async def get_embedding_status():
    """n von m Papern UND n von m Chunks indexiert (#102), aktuell
    konfiguriertes Modell + Dimension, und ob Vektoren eines anderen Modells
    da sind (``stale``, #213).

    Funktioniert auch ohne gebundene Embedding-Rolle (n=0, model="") —
    kein 500er, die semantische Suche ist dann optional ausgeblendet (PRD
    Entscheidung 5)."""
    return embedding_status()
