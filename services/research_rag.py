"""Service: Research-RAG (PURE, DETERMINISTIC).

Provides the deterministic core of the research-chat pipeline:

1. ``tokenize``               — simple lowercased alphanumeric tokenizer.
2. ``bm25_search``            — BM25 ranking over a list of chunk dicts.
3. ``build_chat_context``     — assembles the LLM context string + sources list.
4. ``reciprocal_rank_fusion`` — fuses multiple ranked-ID lists (RRF, #100).

Text-chunking is intentionally NOT duplicated here.  The pure chunker lives in
the neutral ``pdf_chunking`` module (``_chunk_text``).  Import it from there if
a caller needs it.

PURE per the side-effect-free invariant (CLAUDE.md / PRD-backend-modularization):
no DB writes, no network I/O, no filesystem mutations.  Chunks are returned to
the router (``routers/research_chat.py``) for persistence.

Moved from ``webapp.py`` as part of Backend-Modularisierung #89.
"""

from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list:
    """Einfache Tokenisierung: Kleinschreibung, alphanumerische Tokens."""
    return re.findall(r'[a-z\xe4\xf6\xfc\xdf0-9]{2,}', text.lower())


# ---------------------------------------------------------------------------
# BM25 ranking
# ---------------------------------------------------------------------------

def bm25_search(
    query: str,
    chunks: list,
    top_k: int = 8,
    k1: float = 1.5,
    b: float = 0.75,
) -> list:
    """BM25-Ranking ueber Chunk-Texte.

    chunks: Liste von dicts mit mindestens ``{"chunk_text", "paper_id",
    "page_start"}``; weitere Felder werden unveraendert durchgereicht.

    Returns: Top-k Chunks sortiert nach Score absteigend, jeweils mit einem
    ``"score"``-Feld (float, 4 Dezimalstellen); nur Chunks mit Score > 0.
    """
    if not chunks or not query.strip():
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Tokenize all documents
    doc_tokens = [tokenize(c["chunk_text"]) for c in chunks]
    doc_lengths = [len(t) for t in doc_tokens]

    N = len(chunks)
    avgdl = sum(doc_lengths) / N if N > 0 else 1

    # Document frequency per term
    df: Counter = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            df[t] += 1

    # IDF for query terms
    idf: dict[str, float] = {}
    for t in query_tokens:
        n_t = df.get(t, 0)
        idf[t] = math.log((N - n_t + 0.5) / (n_t + 0.5) + 1.0)

    # BM25 scores
    scores: list[tuple[int, float]] = []
    for i, tokens in enumerate(doc_tokens):
        tf: Counter = Counter(tokens)
        score = 0.0
        dl = doc_lengths[i]
        for qt in query_tokens:
            f = tf.get(qt, 0)
            if f == 0:
                continue
            numerator = f * (k1 + 1)
            denominator = f + k1 * (1 - b + b * dl / avgdl)
            score += idf.get(qt, 0.0) * numerator / denominator
        scores.append((i, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    results = []
    for idx, score in scores[:top_k]:
        if score > 0:
            chunk = dict(chunks[idx])
            chunk["score"] = round(score, 4)
            results.append(chunk)
    return results


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (#100, PRD-semantische-suche.md Phase 2, Entscheidung 4)
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(rankings: list, k: int = 60) -> list:
    """Fuses multiple ranked-ID lists into one via Reciprocal Rank Fusion.

    ``rankings``: list of ranked-ID lists, each ordered best-first (e.g. a
    BM25 paper-ranking and a cosine paper-ranking). IDs must be hashable
    (``paper_id`` ints in the semantic-search caller); an ID need not appear
    in every ranking -- a missing ranking simply contributes 0 to that ID's
    score (this is how "cosine-only" papers without chunks stay visible,
    #100 acceptance criterion 2).

    Score per ID: ``sum(1 / (k + rank))`` over every ranking it appears in,
    rank 1-based. ``k=60`` is the RRF default from the original TREC paper
    (Cormack et al. 2009) -- parameter-poor and robust across very different
    score scales (BM25 vs. cosine), which is the whole point of RRF over
    a hand-tuned score normalisation (PRD Entscheidung 4).

    Returns ``[(id, score), ...]`` sorted by score descending (float, 6
    decimals). Ties keep the order in which the ID was first encountered
    (ranking order, then position within that ranking) for a stable,
    deterministic result.
    """
    scores: dict = {}
    first_seen: dict = {}
    order = 0
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            if item not in scores:
                scores[item] = 0.0
                first_seen[item] = order
                order += 1
            scores[item] += 1.0 / (k + rank)

    items = sorted(scores.keys(), key=lambda it: (-scores[it], first_seen[it]))
    return [(it, round(scores[it], 6)) for it in items]


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_chat_context(chunks: list, paper_info: dict) -> tuple:
    """Baut den Kontext-String fuer das LLM aus den gefundenen Chunks.

    Gruppiert Chunks nach Paper (Reihenfolge beibehalten) und nummeriert
    Quellen sequenziell.

    Args:
        chunks:     Chunk-Dicts (mindestens ``paper_id``, ``page_start``,
                    ``chunk_text``).
        paper_info: Mapping ``paper_id -> {title, authors, year, ...}``.

    Returns:
        (context_string, sources_list)
        sources_list: ``[{paper_id, title, authors, year,
                          pages_referenced, previews}, ...]``
    """
    paper_chunks: OrderedDict = OrderedDict()
    for chunk in chunks:
        pid = chunk.get("paper_id")
        if pid not in paper_chunks:
            paper_chunks[pid] = []
        paper_chunks[pid].append(chunk)

    context_parts: list[str] = []
    sources: list[dict] = []
    for source_idx, (pid, p_chunks) in enumerate(paper_chunks.items(), 1):
        pi = paper_info.get(pid, {})
        paper_title = pi.get("title", "Unbekannt")
        pages = sorted(set(c.get("page_start", 0) for c in p_chunks))
        previews: list[dict] = []
        for chunk in p_chunks:
            page = chunk.get("page_start", "?")
            context_parts.append(
                f"[Quelle {source_idx}] ({paper_title}, S. {page}):\n{chunk['chunk_text']}"
            )
            previews.append({
                "page": page,
                "text": chunk["chunk_text"][:300],
            })
        sources.append({
            "paper_id": pid,
            "title": pi.get("title", ""),
            "authors": pi.get("authors", ""),
            "year": pi.get("year"),
            "pages_referenced": pages,
            "previews": previews,
        })

    return "\n\n---\n\n".join(context_parts), sources
