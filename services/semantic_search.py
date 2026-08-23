"""Service: semantic-search ranking (PURE, DETERMINISTIC).

Cosine-similarity ranking over paper-level embedding vectors -- the ranking
math half of ``GET/POST /api/search/semantic`` (#99,
``docs/PRD-semantische-suche.md`` Phase 2, Modulschnitt-Entscheidung 8).
Also holds the two-stage paper->chunk ranking for passage retrieval (#102,
Phase 3, ``GET/POST /api/search/passages``). Mirrors ``services/research_rag.py``:
no DB I/O, no HTTP, no logging config -- just pure functions over vector
fixtures so they stay unit-testable with fixed inputs.

The model-safety rule (PRD Entscheidung 7: only ever compare vectors that
were produced by the SAME embedding model) is enforced by the CALLER
(``routers/search.py``), which must only pass candidates whose stored
``model`` matches the model the query vector was produced with. This module
has no notion of "model" at all -- it is pure vector math.
"""

from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors.

    Returns ``0.0`` for empty vectors, mismatched lengths, or zero-norm
    vectors (never raises/divides by zero).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_by_cosine(query_vector: list[float], candidates: list[dict], top_k: int = 10) -> list[dict]:
    """Ranks ``candidates`` by cosine similarity to ``query_vector``.

    ``candidates``: list of dicts, each with at least a ``"vector"`` key
    (``list[float]``); any other keys (``paper_id``, ``citekey``, ``title``,
    ...) are passed through unchanged.

    Returns the top ``top_k`` candidates as shallow copies with the raw
    ``"vector"`` key removed and a ``"score"`` key added (float, rounded to
    4 decimals), sorted score descending. Ties keep the original relative
    order (stable sort). No score threshold is applied here -- callers that
    want one apply it themselves.
    """
    scored: list[dict] = []
    for cand in candidates:
        vector = cand.get("vector") or []
        score = cosine(query_vector, vector)
        entry = {k: v for k, v in cand.items() if k != "vector"}
        entry["score"] = round(score, 4)
        scored.append(entry)
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_k]


def two_stage_paper_then_chunk_ranking(
    query_vector: list[float],
    paper_candidates: list[dict],
    chunk_candidates: list[dict],
    top_n_papers: int = 20,
    top_k_chunks: int = 10,
) -> list[dict]:
    """Two-stage passage retrieval (PRD Phase 3, #102): first ranks PAPERS by
    cosine similarity and keeps only the top ``top_n_papers``, THEN ranks
    CHUNKS by cosine similarity restricted to chunks whose ``paper_id`` made
    that cut.

    This is the "keeps the matrix small" idea from the PRD (Designentscheidung
    2/8, #102 acceptance criterion "< 1s nach Matrix-Warm-up"): a chunk-level
    cosine pass over the WHOLE library is O(#chunks) (tens of thousands at
    real library scale); restricting it to the handful of papers the
    paper-level stage already judged relevant keeps every search's chunk pass
    small regardless of how large the library grows, at the (accepted) cost
    of never surfacing a chunk from a paper that did not make the paper-level
    cut.

    ``paper_candidates``: ``[{"paper_id": ..., "vector": [...]}, ...]``.
    ``chunk_candidates``: ``[{"paper_id": ..., "vector": [...], ...}, ...]`` --
    any extra keys (``chunk_id``, ``chunk_text``, ``page_start``, ...) pass
    through unchanged, mirroring ``rank_by_cosine``.

    Returns the top ``top_k_chunks`` chunk dicts (shallow copies, ``vector``
    key removed, ``score`` key added), sorted descending -- empty if either
    input is empty or no chunk belongs to a top-ranked paper.
    """
    if not paper_candidates or not chunk_candidates:
        return []
    paper_ranking = rank_by_cosine(query_vector, paper_candidates, top_k=top_n_papers)
    top_paper_ids = {p["paper_id"] for p in paper_ranking}
    pooled = [c for c in chunk_candidates if c.get("paper_id") in top_paper_ids]
    if not pooled:
        return []
    return rank_by_cosine(query_vector, pooled, top_k=top_k_chunks)
