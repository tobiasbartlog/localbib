"""Router: semantic search (#99/#100, ``docs/PRD-semantische-suche.md`` Phase 2)
plus two-stage passage retrieval (#102, Phase 3).

Serves:
  GET  /api/search/semantic
  POST /api/search/semantic
  GET  /api/search/passages
  POST /api/search/passages

Query -> ``embed_texts(mode="query")`` -> cosine ranking over
``paper_embeddings`` (``services.semantic_search``, PURE ranking math),
fused (#100) with a BM25 ranking over the REAL ``paper_chunks`` via
Reciprocal Rank Fusion (``services.research_rag.reciprocal_rank_fusion``) --
whichever papers have chunks contribute a lexical signal on top of the
cosine signal every embedded paper already has. There is also a lexical-only
BM25 fallback (``services.research_rag.bm25_search``, over pseudo-chunks
built from paper title+abstract -- works even for papers that were never
PDF-chunked) whenever semantic search cannot run at all:

  * no ``LLM_EMBED_MODEL`` configured (PRD Entscheidung 5 -- degradation is a
    product requirement, not just an error path: the app must stay fully
    usable without an embedding model),
  * no indexed vectors exist for the CURRENTLY configured model -- this also
    covers the "index was built with model A, config now points at model B"
    mismatch (PRD Entscheidung 7: vectors from different embedding models are
    never compared -- a silent false-positive match is worse than a lexical
    fallback), or
  * the query-embedding HTTP call itself fails.

None of these paths ever produce a 500; the response always carries a
``mode`` field -- ``"semantic"`` (cosine only -- no chunk in the library
matched the query lexically), ``"bm25"`` (lexical only -- no embedding
available) or ``"hybrid"`` (both cosine and the chunk-BM25 ranking
contributed) -- so the frontend can show which signal(s) produced the
result, plus an optional ``"note"`` with a short reason on fallback.

``GET/POST /api/search/passages`` (#102, PRD Phase 3) is the chunk-level
sibling: same degradation ladder (no model / no chunk index for the current
model / query-embed failure -> lexical fallback over ``paper_chunks``), but
the ``"semantic"``/``"hybrid"`` paths run a TWO-STAGE ranking
(``services.semantic_search.two_stage_paper_then_chunk_ranking``) -- rank
papers first, then only cosine-rank the chunks belonging to the top-ranked
papers, which keeps the matrix small regardless of library size (PRD
Designentscheidung 2/8). Results carry ``page_start`` so a hit is directly
citable. Kept as a separate endpoint (not a field bolted onto
``/api/search/semantic``) so #99/#100/#101's paper-level response shape stays
byte-identical.

``GET /api/search/reference/{citekey}`` and
``GET /api/search/reference/{citekey}/chunks`` (#104, PRD Phase 4 -- the
"Agent-Schnittstelle" the whole PRD was originally triggered by) are the
citekey-based lookup half of the agent-facing surface: once a search
endpoint above has surfaced a citekey, an external agent fetches that
paper's full metadata+abstract, or pages through its raw ``paper_chunks``
(unlike the search endpoints these are NOT ranked/scored -- plain lookup by
identity), without ever needing a browser session. Both 404 on an unknown
citekey. Plain DB reads, no embedding model involved, so no degradation
ladder applies here. See the README "Agenten-API" section for curl
examples.

No import from ``webapp`` or any other router.
"""

from __future__ import annotations

import logging

from cite_key_generator import base_key as _legacy_cite_key_base
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import services.research_rag as _rag
import services.semantic_search as _semantic
from context import get_conn
from embedding_index import unpack_vector
from literature_manager import Config
from llm_client import embed_texts

router = APIRouter()

DEFAULT_TOP_K = 10
ABSTRACT_EXCERPT_CHARS = 300
RRF_K = 60


class SemanticSearchRequest(BaseModel):
    q: str
    top_k: int = DEFAULT_TOP_K


# ---------------------------------------------------------------------------
# Helpers (I/O -- live in the router, not the pure service)
# ---------------------------------------------------------------------------

def _load_papers(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, cite_key, title, year, abstract FROM papers ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _excerpt(text: str, n: int = ABSTRACT_EXCERPT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def _hit_fields(p: dict) -> dict:
    return {
        "paper_id": p["id"],
        "citekey": p.get("cite_key") or "",
        "title": p.get("title") or "",
        "year": p.get("year"),
        "abstract_excerpt": _excerpt(p.get("abstract") or ""),
    }


def _hit(p: dict, score: float) -> dict:
    return {**_hit_fields(p), "score": score}


def _load_chunks(conn) -> list[dict]:
    rows = conn.execute("SELECT paper_id, chunk_text FROM paper_chunks").fetchall()
    return [dict(r) for r in rows]


def _bm25_paper_ranking(q: str, chunks: list[dict]) -> list[int]:
    """Ranks ``paper_id``s by their best-matching real ``paper_chunks`` row.

    Reduces the chunk-level BM25 ranking to a paper-level ranking (best
    chunk per paper, deduplicated, order preserved) so it can be fused with
    the paper-level cosine ranking via RRF. Empty when the library has no
    chunks at all, or none match the query lexically -- the caller treats
    that as "BM25 does not contribute" (mode stays ``"semantic"``)."""
    if not chunks:
        return []
    hits = _rag.bm25_search(q, chunks, top_k=len(chunks))
    seen: set[int] = set()
    ranking: list[int] = []
    for h in hits:
        pid = h["paper_id"]
        if pid not in seen:
            seen.add(pid)
            ranking.append(pid)
    return ranking


def _bm25_fallback(q: str, top_k: int, papers: list[dict], note: str = "") -> dict:
    """Lexical fallback over paper title+abstract (Phase 2 = paper granularity,
    so this reuses ``bm25_search`` with pseudo-chunks -- no ``paper_chunks``
    dependency needed)."""
    pseudo_chunks = [
        {
            "chunk_text": f"{p.get('title', '')}\n{p.get('abstract', '')}".strip(),
            "paper_id": p["id"],
        }
        for p in papers
        if (p.get("title") or p.get("abstract"))
    ]
    hits = _rag.bm25_search(q, pseudo_chunks, top_k=top_k)
    by_id = {p["id"]: p for p in papers}
    results = [_hit(by_id[h["paper_id"]], h["score"]) for h in hits if h["paper_id"] in by_id]
    resp: dict = {"mode": "bm25", "results": results}
    if note:
        resp["note"] = note
    return resp


def _search(q: str, top_k: int) -> dict:
    q = (q or "").strip()
    if not q:
        return {"mode": "bm25", "results": []}
    top_k = max(1, top_k)

    conn = get_conn()
    try:
        papers = _load_papers(conn)

        model = (Config.LLM_EMBED_MODEL or "").strip()
        if not model:
            return _bm25_fallback(q, top_k, papers)

        vector_rows = conn.execute(
            "SELECT paper_id, vector FROM paper_embeddings WHERE model = ?",
            (model,),
        ).fetchall()
        if not vector_rows:
            return _bm25_fallback(
                q, top_k, papers,
                note="Kein Embedding-Index fuer das aktuell konfigurierte Modell vorhanden "
                     "(lexikalischer Fallback statt stillem Modell-Mismatch).",
            )

        try:
            query_vector = embed_texts([q], mode="query")[0]
        except Exception as e:
            logging.warning(f"Semantische Suche: Query-Embedding fehlgeschlagen: {e}")
            return _bm25_fallback(
                q, top_k, papers,
                note="Embedding-Anfrage fehlgeschlagen, lexikalischer Fallback.",
            )

        by_id = {p["id"]: p for p in papers}
        candidates = []
        for row in vector_rows:
            p = by_id.get(row["paper_id"])
            if not p:
                continue
            candidates.append({**_hit_fields(p), "vector": unpack_vector(row["vector"])})

        # Full cosine ranking (not truncated to top_k yet) -- RRF needs the
        # whole rank list, truncation to top_k happens after fusion.
        cosine_full = _semantic.rank_by_cosine(query_vector, candidates, top_k=len(candidates))

        bm25_paper_ranking = _bm25_paper_ranking(q, _load_chunks(conn))
        if not bm25_paper_ranking:
            # No paper_chunks in the library (or none matched the query
            # lexically) -- BM25 contributes nothing, stay cosine-only.
            return {"mode": "semantic", "results": cosine_full[:top_k]}

        cosine_paper_ranking = [c["paper_id"] for c in cosine_full]
        cosine_by_id = {c["paper_id"]: c for c in cosine_full}
        fused = _rag.reciprocal_rank_fusion([cosine_paper_ranking, bm25_paper_ranking], k=RRF_K)

        results = []
        for paper_id, score in fused[:top_k]:
            base = cosine_by_id.get(paper_id)
            if base is None:
                p = by_id.get(paper_id)
                if not p:
                    continue
                base = _hit_fields(p)
            results.append({**base, "score": score})
        return {"mode": "hybrid", "results": results}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/search/semantic")
async def semantic_search_get(q: str = "", top_k: int = DEFAULT_TOP_K):
    return _search(q, top_k)


@router.post("/api/search/semantic")
async def semantic_search_post(req: SemanticSearchRequest):
    return _search(req.q, req.top_k)


# ---------------------------------------------------------------------------
# Passage retrieval (#102, PRD Phase 3): two-stage paper -> chunk search
# ---------------------------------------------------------------------------

PASSAGE_TOP_K = 10
# How many top-ranked papers stay in the pool before the chunk-level cosine
# pass runs -- the "keeps the matrix small" half of the two-stage design.
PASSAGE_TOP_N_PAPERS = 20
PASSAGE_EXCERPT_CHARS = 300


class PassageSearchRequest(BaseModel):
    q: str
    top_k: int = PASSAGE_TOP_K


def _load_all_chunks_meta(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id AS chunk_id, paper_id, chunk_text, page_start FROM paper_chunks"
    ).fetchall()
    return [dict(r) for r in rows]


def _passage_hit(chunk: dict, by_paper: dict, score: float) -> dict:
    p = by_paper.get(chunk.get("paper_id"), {})
    return {
        "paper_id": chunk.get("paper_id"),
        "citekey": p.get("cite_key") or "",
        "title": p.get("title") or "",
        "year": p.get("year"),
        "page_start": chunk.get("page_start"),
        "chunk_excerpt": _excerpt(chunk.get("chunk_text") or "", PASSAGE_EXCERPT_CHARS),
        "score": score,
    }


def _passage_bm25_fallback(q: str, top_k: int, chunks: list[dict], by_paper: dict, note: str = "") -> dict:
    """Lexical fallback over the REAL ``paper_chunks`` (unlike the paper-level
    fallback, there is no pseudo-chunk substitute here -- passages ARE
    chunks)."""
    hits = _rag.bm25_search(q, chunks, top_k=top_k)
    results = [_passage_hit(h, by_paper, h["score"]) for h in hits]
    resp: dict = {"mode": "bm25", "results": results}
    if note:
        resp["note"] = note
    return resp


def _passage_search(q: str, top_k: int) -> dict:
    q = (q or "").strip()
    if not q:
        return {"mode": "bm25", "results": []}
    top_k = max(1, top_k)

    conn = get_conn()
    try:
        papers = _load_papers(conn)
        by_paper = {p["id"]: p for p in papers}
        all_chunks = _load_all_chunks_meta(conn)

        model = (Config.LLM_EMBED_MODEL or "").strip()
        if not model:
            return _passage_bm25_fallback(q, top_k, all_chunks, by_paper)

        paper_vector_rows = conn.execute(
            "SELECT paper_id, vector FROM paper_embeddings WHERE model = ?", (model,)
        ).fetchall()
        chunk_vector_rows = conn.execute(
            """SELECT pc.id AS chunk_id, pc.paper_id, pc.chunk_text, pc.page_start, ce.vector
               FROM chunk_embeddings ce
               JOIN paper_chunks pc ON pc.id = ce.chunk_id
               WHERE ce.model = ?""",
            (model,),
        ).fetchall()
        if not paper_vector_rows or not chunk_vector_rows:
            return _passage_bm25_fallback(
                q, top_k, all_chunks, by_paper,
                note="Kein Chunk-Embedding-Index fuer das aktuell konfigurierte Modell vorhanden "
                     "(lexikalischer Passagen-Fallback statt stillem Modell-Mismatch).",
            )

        try:
            query_vector = embed_texts([q], mode="query")[0]
        except Exception as e:
            logging.warning(f"Passagensuche: Query-Embedding fehlgeschlagen: {e}")
            return _passage_bm25_fallback(
                q, top_k, all_chunks, by_paper,
                note="Embedding-Anfrage fehlgeschlagen, lexikalischer Passagen-Fallback.",
            )

        paper_candidates = [
            {"paper_id": r["paper_id"], "vector": unpack_vector(r["vector"])}
            for r in paper_vector_rows
            if r["paper_id"] in by_paper
        ]
        chunk_candidates = [
            {
                "chunk_id": r["chunk_id"],
                "paper_id": r["paper_id"],
                "chunk_text": r["chunk_text"],
                "page_start": r["page_start"],
                "vector": unpack_vector(r["vector"]),
            }
            for r in chunk_vector_rows
        ]

        # Stage 1+2 (pure, services.semantic_search): rank papers, then only
        # cosine-rank chunks belonging to the top-ranked papers. Not truncated
        # to top_k yet -- the BM25/RRF fusion below needs the full pooled
        # ranking, truncation happens after fusion.
        cosine_chunks = _semantic.two_stage_paper_then_chunk_ranking(
            query_vector, paper_candidates, chunk_candidates,
            top_n_papers=PASSAGE_TOP_N_PAPERS, top_k_chunks=len(chunk_candidates),
        )
        if not cosine_chunks:
            # Embeddings exist, but none belong to a paper that made the
            # paper-level top-N cut -- nothing to show, not an error.
            return {"mode": "semantic", "results": []}

        # BM25 within the SAME paper pool the cosine stage settled on, so the
        # RRF fusion below compares two rankings over one consistent chunk set.
        pool_paper_ids = {c["paper_id"] for c in cosine_chunks}
        pooled_chunks = [c for c in all_chunks if c["paper_id"] in pool_paper_ids]
        bm25_hits = _rag.bm25_search(q, pooled_chunks, top_k=len(pooled_chunks)) if pooled_chunks else []
        bm25_chunk_ranking = [h["chunk_id"] for h in bm25_hits]

        if not bm25_chunk_ranking:
            results = [_passage_hit(c, by_paper, c["score"]) for c in cosine_chunks[:top_k]]
            return {"mode": "semantic", "results": results}

        cosine_chunk_ranking = [c["chunk_id"] for c in cosine_chunks]
        cosine_by_id = {c["chunk_id"]: c for c in cosine_chunks}
        bm25_by_id = {h["chunk_id"]: h for h in bm25_hits}
        fused = _rag.reciprocal_rank_fusion([cosine_chunk_ranking, bm25_chunk_ranking], k=RRF_K)

        results = []
        for chunk_id, score in fused[:top_k]:
            base = cosine_by_id.get(chunk_id) or bm25_by_id.get(chunk_id)
            if base is None:
                continue
            results.append(_passage_hit(base, by_paper, score))
        return {"mode": "hybrid", "results": results}
    finally:
        conn.close()


@router.get("/api/search/passages")
async def passage_search_get(q: str = "", top_k: int = PASSAGE_TOP_K):
    return _passage_search(q, top_k)


@router.post("/api/search/passages")
async def passage_search_post(req: PassageSearchRequest):
    return _passage_search(req.q, req.top_k)


# ---------------------------------------------------------------------------
# Citekey-based reference/chunk retrieval (#104, PRD Phase 4)
# ---------------------------------------------------------------------------

REFERENCE_CHUNKS_DEFAULT_LIMIT = 50


def _cite_key(p: dict) -> str:
    """Same fallback as ``routers/papers.py``/``host_services.py``: prefer the
    stored ``cite_key`` column, fall back to the legacy on-the-fly scheme for
    rows that predate the backfill."""
    return p.get("cite_key") or _legacy_cite_key_base(p.get("authors") or "", p.get("year"))


def _find_paper_by_citekey(conn, citekey: str) -> dict | None:
    citekey = (citekey or "").strip()
    if not citekey:
        return None
    rows = conn.execute(
        "SELECT id, cite_key, title, authors, year, journal, doi, abstract FROM papers"
    ).fetchall()
    for r in rows:
        p = dict(r)
        if _cite_key(p) == citekey:
            return p
    return None


@router.get("/api/search/reference/{citekey}")
async def get_reference(citekey: str):
    """Full metadata + abstract for one paper, looked up by its citekey --
    the plain-lookup half of the agent surface, complementing the search
    endpoints above (a search hit carries a ``citekey``; this resolves it)."""
    conn = get_conn()
    try:
        p = _find_paper_by_citekey(conn, citekey)
        if not p:
            raise HTTPException(status_code=404, detail=f"Unbekannter Citekey: {citekey}")
        return {
            "citekey": _cite_key(p),
            "title": p.get("title") or "",
            "authors": p.get("authors") or "",
            "year": p.get("year"),
            "journal": p.get("journal") or "",
            "doi": p.get("doi") or "",
            "abstract": p.get("abstract") or "",
        }
    finally:
        conn.close()


@router.get("/api/search/reference/{citekey}/chunks")
async def get_reference_chunks(
    citekey: str, limit: int = REFERENCE_CHUNKS_DEFAULT_LIMIT, offset: int = 0
):
    """Paginated raw ``paper_chunks`` for one paper, looked up by citekey --
    e.g. to pull the full context around a passage a ``/api/search/passages``
    hit already pointed at. Not ranked/scored (plain identity lookup, unlike
    the search endpoints); ``page_start``/``page_end`` make each chunk
    directly citable."""
    conn = get_conn()
    try:
        p = _find_paper_by_citekey(conn, citekey)
        if not p:
            raise HTTPException(status_code=404, detail=f"Unbekannter Citekey: {citekey}")
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_chunks WHERE paper_id = ?", (p["id"],)
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT id AS chunk_id, page_start, page_end, chunk_text FROM paper_chunks "
            "WHERE paper_id = ? ORDER BY chunk_index LIMIT ? OFFSET ?",
            (p["id"], max(1, limit), max(0, offset)),
        ).fetchall()
        return {
            "citekey": _cite_key(p),
            "total": total,
            "chunks": [dict(r) for r in rows],
        }
    finally:
        conn.close()
