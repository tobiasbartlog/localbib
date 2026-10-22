"""Passage retrieval: the chunk-level search and the per-Item chunk lookup.

Neutral, read-only module (like ``pdf_chunking``/``paper_ingest``): moved out
of ``routers/search.py`` so that both the HTTP surface (``/api/search/passages``,
``/api/search/reference/{citekey}/chunks``) and the Add-on Contract
(``host_services._CoreLibraryApi.search_passages``/``get_passages``, 2.5.0)
run the same ranking -- a router must not be imported by ``host_services``.

It reads the DB on a connection the caller hands in and never writes. The
embedding call and the paper-pool size are passed in by the caller, so the
router keeps its patchable names (``routers.search.embed_texts``,
``routers.search.PASSAGE_TOP_N_PAPERS``).

Ranking (#102, PRD Phase 3): no embedding model / no chunk index for the
current model / a failed query embedding -> BM25 over the real
``paper_chunks``; otherwise a two-stage cosine ranking (papers first, then the
chunks of the top-ranked papers) fused with BM25 over the same pool via RRF.

Printed pages (contract 2.5.0): :func:`printed_pages` decides which page
number a reader of the printed work would cite. It never invents one.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Callable, Optional

import services.research_rag as _rag
import services.semantic_search as _semantic
from embedding_index import unpack_vector

RRF_K = 60
# How many top-ranked papers stay in the pool before the chunk-level cosine
# pass runs -- the "keeps the matrix small" half of the two-stage design.
PASSAGE_TOP_N_PAPERS = 20

NOTE_NO_CHUNK_INDEX = (
    "Kein Chunk-Embedding-Index fuer das aktuell konfigurierte Modell vorhanden "
    "(lexikalischer Passagen-Fallback statt stillem Modell-Mismatch)."
)
NOTE_EMBED_FAILED = "Embedding-Anfrage fehlgeschlagen, lexikalischer Passagen-Fallback."


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_papers(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, cite_key, title, year, abstract FROM papers ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def load_all_chunks_meta(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id AS chunk_id, paper_id, chunk_text, page_start, page_end FROM paper_chunks"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _ranked(mode: str, hits: list[tuple[dict, float]], note: str = "") -> dict:
    out: dict = {"mode": mode, "hits": hits}
    if note:
        out["note"] = note
    return out


def _bm25(q: str, top_k: int, chunks: list[dict], note: str = "") -> dict:
    """Lexical fallback over the REAL ``paper_chunks`` (passages ARE chunks,
    there is no pseudo-chunk substitute here)."""
    hits = _rag.bm25_search(q, chunks, top_k=top_k)
    return _ranked("bm25", [(h, h["score"]) for h in hits], note)


def rank_passages(
    conn,
    q: str,
    top_k: int,
    *,
    model: str,
    embed: Callable[..., list],
    top_n_papers: int = PASSAGE_TOP_N_PAPERS,
    with_papers: bool = True,
) -> dict:
    """Rank the library's chunks for ``q``.

    Returns ``{"mode", "hits": [(chunk, score), ...], "papers": {id: paper},
    "note"?}``; a chunk is ``{chunk_id, paper_id, chunk_text, page_start,
    page_end}``. ``mode`` is ``"bm25"``, ``"semantic"`` or ``"hybrid"``. An
    empty query answers ``bm25`` with no hits. Never raises for a missing or
    failing embedding model -- it degrades to BM25. A caller that loads its
    own rows for the hits passes ``with_papers=False`` (``papers`` is then
    empty and the whole library is not read for it)."""
    q = (q or "").strip()
    if not q:
        return {"mode": "bm25", "hits": [], "papers": {}}
    top_k = max(1, top_k)

    if with_papers:
        by_paper = {p["id"]: p for p in load_papers(conn)}
        paper_ids = set(by_paper)
    else:
        by_paper = {}
        paper_ids = {r["id"] for r in conn.execute("SELECT id FROM papers").fetchall()}
    out = _rank(conn, q, top_k, paper_ids, model=model, embed=embed, top_n_papers=top_n_papers)
    out["papers"] = by_paper
    return out


def _rank(conn, q, top_k, paper_ids, *, model, embed, top_n_papers) -> dict:
    all_chunks = load_all_chunks_meta(conn)
    if not model:
        return _bm25(q, top_k, all_chunks)

    paper_vector_rows = conn.execute(
        "SELECT paper_id, vector FROM paper_embeddings WHERE model = ?", (model,)
    ).fetchall()
    chunk_vector_rows = conn.execute(
        """SELECT pc.id AS chunk_id, pc.paper_id, pc.chunk_text, pc.page_start,
                  pc.page_end, ce.vector
           FROM chunk_embeddings ce
           JOIN paper_chunks pc ON pc.id = ce.chunk_id
           WHERE ce.model = ?""",
        (model,),
    ).fetchall()
    if not paper_vector_rows or not chunk_vector_rows:
        return _bm25(q, top_k, all_chunks, NOTE_NO_CHUNK_INDEX)

    try:
        query_vector = embed([q], mode="query")[0]
    except Exception as e:
        logging.warning(f"Passagensuche: Query-Embedding fehlgeschlagen: {e}")
        return _bm25(q, top_k, all_chunks, NOTE_EMBED_FAILED)

    paper_candidates = [
        {"paper_id": r["paper_id"], "vector": unpack_vector(r["vector"])}
        for r in paper_vector_rows
        if r["paper_id"] in paper_ids
    ]
    chunk_candidates = [
        {
            "chunk_id": r["chunk_id"],
            "paper_id": r["paper_id"],
            "chunk_text": r["chunk_text"],
            "page_start": r["page_start"],
            "page_end": r["page_end"],
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
        top_n_papers=top_n_papers, top_k_chunks=len(chunk_candidates),
    )
    if not cosine_chunks:
        # Embeddings exist, but none belong to a paper that made the
        # paper-level top-N cut -- nothing to show, not an error.
        return _ranked("semantic", [])

    # BM25 within the SAME paper pool the cosine stage settled on, so the
    # RRF fusion below compares two rankings over one consistent chunk set.
    pool_paper_ids = {c["paper_id"] for c in cosine_chunks}
    pooled_chunks = [c for c in all_chunks if c["paper_id"] in pool_paper_ids]
    bm25_hits = _rag.bm25_search(q, pooled_chunks, top_k=len(pooled_chunks)) if pooled_chunks else []
    bm25_chunk_ranking = [h["chunk_id"] for h in bm25_hits]

    if not bm25_chunk_ranking:
        return _ranked("semantic", [(c, c["score"]) for c in cosine_chunks[:top_k]])

    cosine_chunk_ranking = [c["chunk_id"] for c in cosine_chunks]
    cosine_by_id = {c["chunk_id"]: c for c in cosine_chunks}
    bm25_by_id = {h["chunk_id"]: h for h in bm25_hits}
    fused = _rag.reciprocal_rank_fusion([cosine_chunk_ranking, bm25_chunk_ranking], k=RRF_K)

    hits = []
    for chunk_id, score in fused[:top_k]:
        base = cosine_by_id.get(chunk_id) or bm25_by_id.get(chunk_id)
        if base is None:
            continue
        hits.append((base, score))
    return _ranked("hybrid", hits)


# ---------------------------------------------------------------------------
# Per-Item chunk lookup (plain identity lookup, not ranked)
# ---------------------------------------------------------------------------

def count_chunks(conn, paper_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM paper_chunks WHERE paper_id = ?", (paper_id,)
    ).fetchone()["n"]


def paper_chunks(conn, paper_id: int, *, limit: int, offset: int) -> list[dict]:
    """One page of the Item's chunks in reading order:
    ``[{chunk_id, chunk_index, page_start, page_end, chunk_text}]``."""
    rows = conn.execute(
        "SELECT id AS chunk_id, chunk_index, page_start, page_end, chunk_text FROM paper_chunks "
        "WHERE paper_id = ? ORDER BY chunk_index LIMIT ? OFFSET ?",
        (paper_id, max(1, limit), max(0, offset)),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Printed pages (contract 2.5.0)
# ---------------------------------------------------------------------------

_PAGES_RANGE = re.compile(r"^\s*(\d+)\s*(?:-{1,2}|–|—)\s*(\d+)\s*$")


def _pages_range(pages_field: Any) -> Optional[tuple[int, int]]:
    """``"345-360"`` (also ``--``, en-/em-dash) -> ``(345, 360)``; anything
    else, including a reversed range, -> ``None``."""
    m = _PAGES_RANGE.match(str(pages_field or ""))
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    return (start, end) if end >= start else None


def printed_pages(
    pdf_page: Optional[int],
    *,
    page_labels: Optional[list] = None,
    pages_field: Any = None,
    pdf_page_count: Optional[int] = None,
) -> Optional[str]:
    """The printed page of 1-based PDF page ``pdf_page``, or ``None``.

    In this order: (1) the PDF's own page label for that page; (2) the
    metadata ``pages`` range (``345-360``, en-dash too) when its length equals
    the PDF's page count -> ``start + (pdf_page - 1)``; (3) ``None``. Never
    invents a page: an unknown PDF page, an empty label, a range that does not
    match the page count all answer ``None``."""
    if not isinstance(pdf_page, int) or pdf_page < 1:
        return None
    if page_labels and pdf_page <= len(page_labels):
        label = str(page_labels[pdf_page - 1] or "").strip()
        if label:
            return label
    rng = _pages_range(pages_field)
    if rng and pdf_page_count and (rng[1] - rng[0] + 1) == pdf_page_count and pdf_page <= pdf_page_count:
        return str(rng[0] + pdf_page - 1)
    return None


class PageLabelCache:
    """Page labels and page count per PDF, read lazily and kept in memory.

    ``labels(path)`` -> ``(labels or None, page_count or None)``. A PDF
    without labels answers ``(None, count)``. Opening the PDF never fails the
    caller: any error is "no labels, unknown count". Keyed by path plus the
    file's mtime/size so a replaced PDF is read again."""

    def __init__(self, opener: Optional[Callable[[str], Any]] = None, max_entries: int = 512) -> None:
        self._opener = opener
        self._max = max_entries
        self._cache: dict = {}
        self._lock = threading.Lock()

    def labels(self, path: Optional[str]) -> tuple[Optional[list], Optional[int]]:
        if not path:
            return None, None
        key = _file_key(path)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        value = self._read(path)
        with self._lock:
            if len(self._cache) >= self._max:
                self._cache.clear()
            self._cache[key] = value
        return value

    def _read(self, path: str) -> tuple[Optional[list], Optional[int]]:
        try:
            opener = self._opener or _open_pdf
            doc = opener(path)
            try:
                count = int(doc.page_count)
                rules_of = getattr(doc, "get_page_labels", None)
                if callable(rules_of):  # the catalog's rules: no page is loaded
                    labels = labels_from_rules(rules_of() or [], count)
                else:
                    labels = [str(doc[i].get_label() or "") for i in range(count)]
            finally:
                doc.close()
        except Exception as exc:
            logging.debug("Seitenlabels fuer %s nicht lesbar: %s", path, exc)
            return None, None
        return (labels if any(labels) else None), count


_ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))


def _label_number(n: int, style: str) -> str:
    if n < 1:
        return str(n) if style == "D" else ""
    if style == "D":
        return str(n)
    if style in ("r", "R"):
        out = ""
        for value, digits in _ROMAN:
            while n >= value:
                out += digits
                n -= value
        return out.upper() if style == "R" else out
    if style in ("a", "A"):  # a..z, aa..zz, aaa.. (PDF 32000, 12.4.2)
        letters = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return letters.upper() if style == "A" else letters
    return ""  # no style: the prefix alone


def labels_from_rules(rules: list, count: int) -> list[str]:
    """Every page's label from the PDF's ``/PageLabels`` rules (PyMuPDF's
    ``doc.get_page_labels()``: ``{startpage, prefix, style, firstpagenum}``),
    i.e. what ``page.get_label()`` answers -- without loading each page of a
    900-page book. A page before the first rule has no label (``""``)."""
    ordered = sorted(rules, key=lambda r: int(r.get("startpage", 0)))
    out: list[str] = []
    current = -1
    for pno in range(count):
        while current + 1 < len(ordered) and int(ordered[current + 1].get("startpage", 0)) <= pno:
            current += 1
        if current < 0:
            out.append("")
            continue
        rule = ordered[current]
        n = pno - int(rule.get("startpage", 0)) + int(rule.get("firstpagenum") or 1)
        out.append(str(rule.get("prefix") or "") + _label_number(n, str(rule.get("style") or "")))
    return out


def _open_pdf(path: str):
    import fitz  # PyMuPDF, a core dependency

    return fitz.open(path)


def _file_key(path: str) -> tuple:
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)
