"""Neutral helper module: embedding indexing (paper- and chunk-level) for
semantic search.

Phase 1 of ``docs/PRD-semantische-suche.md`` — Paper-Ebene: one vector per
paper (Titel + Abstract) so the whole library is searchable semantically,
even papers whose PDF was never chunked. Phase 3 (#102) adds the Chunk-Ebene:
one vector per ``paper_chunks`` row for passage-level retrieval ("welche
Textstelle belegt das?"). Mirrors the ``pdf_chunking.py`` pattern (#87
discovery): this is neither a router nor a ``services/`` module — it is
allowed to persist via ``context.get_conn`` (the embeddings are a derived
index over ``papers``/``paper_chunks``, written on the same import path
chunking uses), and it performs the embedding HTTP call via
``llm_client.embed_texts`` (Kern-Slice H0, #97). No router/webapp imports.

Functions:
  * ``_document_text``       — pure-ish (needs conn for the substitute-abstract
                                fallback): builds the Titel[+Abstract] embedding
                                input for one paper (PRD Entscheidung 3).
  * ``_chunk_document_text`` — pure: builds the context-enriched embedding
                                input for one chunk (PRD Entscheidung 9) —
                                ``Titel (+ Jahr/Journal) + "\\n" + Chunk-Text``.
                                The STORED ``paper_chunks.chunk_text`` is never
                                touched; only the embedding INPUT is enriched.
  * ``pack_vector`` / ``unpack_vector`` — float32 BLOB <-> list[float].
  * ``embed_paper_to_db``    — embeds ONE paper; used by the import path
                                (analogous to ``chunk_paper_to_db``). Never
                                raises — degrades to ``False`` so a missing/failed
                                embedding never blocks an import (PRD Entscheidung 5).
  * ``embed_chunks_for_paper`` — embeds the CHUNKS of ONE paper (#153); the
                                chunk-level twin of ``embed_paper_to_db`` for the
                                import path, so a freshly imported paper is
                                passage-searchable without waiting for the next
                                ``reindex_chunks`` run. Same degrade-never-raise
                                contract; idempotent (skips chunks already
                                embedded with the configured model).
  * ``reindex_papers``       — full paper-level run for the Maintenance
                                endpoint; resumable/idempotent (skips paper_ids
                                already indexed with the currently configured
                                model).
  * ``reindex_chunks``       — full chunk-level run (Phase 3, #102); same
                                resumable/idempotent contract, one row per
                                ``paper_chunks`` entry.
  * ``embedding_status``     — n indexed / m total, model, dimension — for
                                both papers and chunks.
  * ``semantic_paper_ids``   — query -> cosine-ranked ``paper_id``s, or
                                ``None`` when semantic ranking cannot run at
                                all (no model / no index / embed HTTP
                                failure). Issue #104: backs
                                ``host_services._CoreLibraryApi.search_references``
                                (``plugin_api.LibraryApi``) so plugin/agent
                                callers get semantic ranking additively, with
                                the caller degrading to lexical search itself.
"""

from __future__ import annotations

import array
import logging

import services.semantic_search as _semantic
from context import get_conn
from literature_manager import Config
from llm_client import embed_texts

# Alias so this module follows the pdf_chunking.py convention.
_get_conn = get_conn

# PRD Entscheidung 3: fehlt das Abstract, werden die ersten ~1500 Zeichen aus
# paper_chunks als Ersatz-Abstract embedded (Seite 1 traegt fast immer das
# faktische Abstract).
SUBSTITUTE_ABSTRACT_CHARS = 1500

# Wie viele Paper pro embed_texts()-Aufruf im Vollindex-Lauf gebuendelt werden.
# embed_texts() chunkt intern nochmal bei EMBED_REQUEST_CHUNK_SIZE (~50) pro
# HTTP-Request; dieser Wert steuert nur, wie oft der Indexer zwischen den
# Aufrufen committet.
INDEX_BATCH_SIZE = 50


def pack_vector(vector: list) -> bytes:
    """Float32-BLOB-Kodierung eines Vektors (kompakt, stdlib-only)."""
    return array.array("f", vector).tobytes()


def unpack_vector(blob: bytes) -> list:
    """Kehrt ``pack_vector`` um."""
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


def _document_text(conn, paper: dict) -> str | None:
    """Baut den Embedding-Input (Titel[+Abstract]) fuer ein Paper.

    Bevorzugt das echte Abstract. Fehlt es, wird ein Ersatz-Abstract aus den
    ersten ~1500 Zeichen der ``paper_chunks`` gebildet (PRD Entscheidung 3);
    ungechunkte Paper fallen auf Titel-only zurueck. Gibt ``None`` zurueck,
    wenn nicht einmal ein Titel vorhanden ist (nichts embed-bares).
    """
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        rows = conn.execute(
            "SELECT chunk_text FROM paper_chunks WHERE paper_id = ? ORDER BY chunk_index",
            (paper["id"],),
        ).fetchall()
        if rows:
            joined = " ".join(r["chunk_text"] for r in rows)
            abstract = joined[:SUBSTITUTE_ABSTRACT_CHARS].strip()

    if not title and not abstract:
        return None
    return f"{title}\n{abstract}".strip() if abstract else title


# ---------------------------------------------------------------------------
# Chunk-Ebene / Passage-Retrieval (#102, PRD Phase 3)
# ---------------------------------------------------------------------------

def _chunk_document_text(paper: dict, chunk_text: str) -> str:
    """Baut den kontextangereicherten Embedding-Input fuer EINEN Chunk.

    PRD Entscheidung 9: ``Titel (+ Jahr/Journal) + "\\n" + Chunk-Text`` --
    Gegenmittel gegen den bekannten Fixed-Size-Chunk-Schwachpunkt "welches
    Paper war das nochmal?" (billige "Contextual Retrieval"-Variante ohne
    LLM-Pass). Der gespeicherte ``paper_chunks.chunk_text`` bleibt davon
    komplett unberuehrt -- nur dieser lokale String wird an ``embed_texts``
    geschickt, nichts wird zurueckgeschrieben.

    Degradiert sauber, wenn Titel/Jahr/Journal fehlen (nie eine leere erste
    Zeile mit ueberfluessigen Klammern/Trennzeichen).
    """
    title = (paper.get("title") or "").strip()
    extras: list[str] = []
    year = paper.get("year")
    if year:
        extras.append(str(year))
    journal = (paper.get("journal") or "").strip()
    if journal:
        extras.append(journal)

    if extras:
        suffix = ", ".join(extras)
        header = f"{title} ({suffix})" if title else f"({suffix})"
    else:
        header = title

    return f"{header}\n{chunk_text}" if header else chunk_text


def _embed_pending_chunks(conn, pending: list, model: str) -> tuple:
    """Embedded bereits vorbereitete ``(chunk_id, text)``-Paare und persistiert sie.

    Der gemeinsame Kern von ``reindex_chunks`` (alle Chunks) und
    ``embed_chunks_for_paper`` (die Chunks EINES Papers, Import-Pfad) — beide
    unterscheiden sich nur darin, WELCHE Chunks sie auswaehlen, nicht darin,
    wie embedded/geschrieben wird. Ein fehlgeschlagener Batch wird geloggt und
    als ``errors`` gezaehlt, nicht geworfen (PRD Entscheidung 5).

    Returns ``(indexed, errors, dim)``.
    """
    indexed = 0
    errors = 0
    dim = 0
    for i in range(0, len(pending), INDEX_BATCH_SIZE):
        batch = pending[i : i + INDEX_BATCH_SIZE]
        texts = [t for _, t in batch]
        try:
            vectors = embed_texts(texts, mode="document")
        except Exception as e:
            logging.warning(f"Chunk-Embedding-Batch fehlgeschlagen ({len(batch)} Chunks): {e}")
            errors += len(batch)
            continue
        for (chunk_id, _text), vector in zip(batch, vectors):
            dim = len(vector)
            conn.execute(
                "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, model, dim, vector) "
                "VALUES (?, ?, ?, ?)",
                (chunk_id, model, dim, pack_vector(vector)),
            )
            indexed += 1
        conn.commit()
    return indexed, errors, dim


def reindex_chunks() -> dict:
    """Vollindex-Lauf ueber alle Chunks (Phase 3, #102; Maintenance-Endpunkt).

    Wiederaufnehmbar/idempotent wie ``reindex_papers``: Chunks, deren
    gespeicherter Vektor bereits vom aktuell konfigurierten Modell stammt,
    werden uebersprungen. Ein Modellwechsel macht alle vorhandenen
    Chunk-Vektoren "veraltet", der naechste Lauf re-embedded sie automatisch.
    Der Embedding-Input ist kontextangereichert (``_chunk_document_text``,
    PRD Entscheidung 9); ``paper_chunks.chunk_text`` bleibt unveraendert.

    Degradiert statt zu werfen, wenn kein Embedding-Modell konfiguriert ist
    (PRD Entscheidung 5).

    Returns:
        {"indexed": int, "skipped": int, "errors": int, "total": int,
         "model": str, "dim": int}
    """
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        return {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}

    conn = _get_conn()
    try:
        chunks = [
            dict(c)
            for c in conn.execute(
                """SELECT pc.id AS chunk_id, pc.chunk_text AS chunk_text,
                          p.title AS title, p.year AS year, p.journal AS journal
                   FROM paper_chunks pc
                   JOIN papers p ON p.id = pc.paper_id
                   ORDER BY pc.id"""
            ).fetchall()
        ]
        already_indexed = {
            r["chunk_id"]: r["model"]
            for r in conn.execute("SELECT chunk_id, model FROM chunk_embeddings").fetchall()
        }

        pending: list[tuple[int, str]] = []
        skipped = 0
        for c in chunks:
            if already_indexed.get(c["chunk_id"]) == model:
                skipped += 1
                continue
            text = _chunk_document_text(c, c["chunk_text"])
            pending.append((c["chunk_id"], text))

        indexed, errors, dim = _embed_pending_chunks(conn, pending, model)

        if dim == 0:
            # Nichts neu indexiert in diesem Lauf -- Dimension aus einem
            # bestehenden Vektor des aktuellen Modells fuer die Statusanzeige holen.
            dim_row = conn.execute(
                "SELECT dim FROM chunk_embeddings WHERE model = ? LIMIT 1", (model,)
            ).fetchone()
            dim = dim_row["dim"] if dim_row else 0

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "total": len(chunks),
            "model": model,
            "dim": dim,
        }
    finally:
        conn.close()


def embed_paper_to_db(paper_id: int) -> bool:
    """Embedded EIN Paper (Titel+Abstract/Ersatz-Abstract) und speichert es.

    Fuer den Auto-Embedding-Hook im Import-Pfad (analog
    ``pdf_chunking.chunk_paper_to_db``): degradiert bei fehlendem
    Embedding-Modell, fehlendem Paper/Text oder jedem HTTP-/Validierungsfehler
    auf ``False`` statt zu werfen — ein ausgefallenes Embedding-Gateway darf
    den Import nie blockieren (PRD Entscheidung 5); nachholbar per
    ``reindex_papers``. Idempotent: ist das Paper bereits mit dem aktuell
    konfigurierten Modell indexiert, wird nichts erneut geschickt.
    """
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        return False

    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT model FROM paper_embeddings WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        if existing and existing["model"] == model:
            return True  # bereits mit aktuellem Modell indexiert

        row = conn.execute(
            "SELECT id, title, abstract FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if not row:
            return False
        text = _document_text(conn, dict(row))
        if not text:
            return False

        vectors = embed_texts([text], mode="document")
        if not vectors:
            return False
        vector = vectors[0]

        conn.execute(
            "INSERT OR REPLACE INTO paper_embeddings (paper_id, model, dim, vector) "
            "VALUES (?, ?, ?, ?)",
            (paper_id, model, len(vector), pack_vector(vector)),
        )
        conn.commit()
        return True
    except Exception as e:
        logging.warning(f"Embedding fehlgeschlagen fuer Paper {paper_id}: {e}")
        return False
    finally:
        conn.close()


def embed_chunks_for_paper(paper_id: int) -> int:
    """Embedded die Chunks EINES Papers (Passage-Retrieval) und speichert sie.

    Das Chunk-Level-Gegenstueck zu ``embed_paper_to_db`` fuer den Import-Pfad
    (#153): ohne diesen Hook entstanden Chunk-Vektoren ausschliesslich im
    Maintenance-Vollauf ``reindex_chunks``, sodass frisch importierte Paper in
    ``/api/search/passages`` und im Research-Chat stillschweigend auf reines
    BM25 zurueckfielen, bis jemand den Reindex angestossen hat.

    Idempotent wie ``reindex_chunks``: Chunks, deren gespeicherter Vektor bereits
    vom aktuell konfigurierten Modell stammt, werden uebersprungen (ein
    Modellwechsel macht sie erkennbar veraltet und dieser Aufruf embedded sie
    neu). Degradiert auf ``0`` statt zu werfen — ein fehlendes Modell oder ein
    ausgefallenes Embedding-Gateway darf einen Import nie blockieren (PRD
    Entscheidung 5); nachholbar per ``reindex_chunks``.

    Returns: Anzahl der in diesem Lauf neu embeddeten Chunks.
    """
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        return 0

    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT pc.id AS chunk_id, pc.chunk_text AS chunk_text,
                      p.title AS title, p.year AS year, p.journal AS journal
               FROM paper_chunks pc
               JOIN papers p ON p.id = pc.paper_id
               WHERE pc.paper_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM chunk_embeddings ce
                     WHERE ce.chunk_id = pc.id AND ce.model = ?
                 )
               ORDER BY pc.id""",
            (paper_id, model),
        ).fetchall()
        if not rows:
            return 0

        pending = [
            (r["chunk_id"], _chunk_document_text(dict(r), r["chunk_text"])) for r in rows
        ]
        indexed, errors, _dim = _embed_pending_chunks(conn, pending, model)
        if errors:
            logging.warning(
                f"Chunk-Embedding fuer Paper {paper_id}: {errors} von {len(pending)} "
                f"Chunks fehlgeschlagen (nachholbar per Reindex)"
            )
        elif indexed:
            logging.info(f"Semantischer Index: {indexed} Chunk-Vektoren fuer Paper {paper_id}")
        return indexed
    except Exception as e:
        logging.warning(f"Chunk-Embedding fehlgeschlagen fuer Paper {paper_id}: {e}")
        return 0
    finally:
        conn.close()


def reindex_papers() -> dict:
    """Vollindex-Lauf ueber alle Paper (Maintenance-Endpunkt).

    Wiederaufnehmbar/idempotent: Paper, deren gespeicherter Vektor bereits vom
    aktuell konfigurierten Modell stammt, werden uebersprungen — ein zweiter
    Lauf ohne Aenderungen ist ein No-op. Ein Modellwechsel in den Settings
    macht alle vorhandenen Vektoren "veraltet" (anderes ``model``), der
    naechste Lauf re-embedded sie automatisch.

    Degradiert statt zu werfen, wenn kein Embedding-Modell konfiguriert ist
    (PRD Entscheidung 5) — der Router zeigt das ueber ``model: ""`` an, statt
    einen 500er zu produzieren.

    Returns:
        {"indexed": int, "skipped": int, "errors": int, "total": int,
         "model": str, "dim": int}
    """
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        return {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}

    conn = _get_conn()
    try:
        papers = [
            dict(p)
            for p in conn.execute("SELECT id, title, abstract FROM papers ORDER BY id").fetchall()
        ]
        already_indexed = {
            r["paper_id"]: r["model"]
            for r in conn.execute("SELECT paper_id, model FROM paper_embeddings").fetchall()
        }

        pending: list[tuple[int, str]] = []
        skipped = 0
        for p in papers:
            if already_indexed.get(p["id"]) == model:
                skipped += 1
                continue
            text = _document_text(conn, p)
            if not text:
                skipped += 1
                continue
            pending.append((p["id"], text))

        indexed = 0
        errors = 0
        dim = 0
        for i in range(0, len(pending), INDEX_BATCH_SIZE):
            batch = pending[i : i + INDEX_BATCH_SIZE]
            texts = [t for _, t in batch]
            try:
                vectors = embed_texts(texts, mode="document")
            except Exception as e:
                logging.warning(f"Embedding-Batch fehlgeschlagen ({len(batch)} Paper): {e}")
                errors += len(batch)
                continue
            for (paper_id, _text), vector in zip(batch, vectors):
                dim = len(vector)
                conn.execute(
                    "INSERT OR REPLACE INTO paper_embeddings (paper_id, model, dim, vector) "
                    "VALUES (?, ?, ?, ?)",
                    (paper_id, model, dim, pack_vector(vector)),
                )
                indexed += 1
            conn.commit()

        if dim == 0:
            # Nichts neu indexiert in diesem Lauf — Dimension aus einem
            # bestehenden Vektor des aktuellen Modells fuer die Statusanzeige holen.
            dim_row = conn.execute(
                "SELECT dim FROM paper_embeddings WHERE model = ? LIMIT 1", (model,)
            ).fetchone()
            dim = dim_row["dim"] if dim_row else 0

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "total": len(papers),
            "model": model,
            "dim": dim,
        }
    finally:
        conn.close()


def embedding_status() -> dict:
    """Statusauskunft fuer den Maintenance-Endpunkt: n von m Papern UND n von m
    Chunks indexiert (#102 erweitert die urspruengliche Paper-only-Auskunft um
    die Chunk-Zahlen), Modell, Dimension (Paper-Ebene -- Chunk-Ebene kann eine
    andere Dimension haben, falls das Modell wechselt, waehrend ein
    Chunk-Reindex noch aussteht; dafuer gibt es keine eigene Chunk-Dimension im
    Response, die Chunk-Zaehler allein reichen fuer den Fortschrittsbalken).
    Funktioniert auch ohne konfiguriertes Modell (n=0)."""
    model = (Config.LLM_EMBED_MODEL or "").strip()
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"]
        chunks_total = conn.execute("SELECT COUNT(*) AS n FROM paper_chunks").fetchone()["n"]
        if model:
            indexed = conn.execute(
                "SELECT COUNT(*) AS n FROM paper_embeddings WHERE model = ?", (model,)
            ).fetchone()["n"]
            dim_row = conn.execute(
                "SELECT dim FROM paper_embeddings WHERE model = ? LIMIT 1", (model,)
            ).fetchone()
            dim = dim_row["dim"] if dim_row else 0
            chunks_indexed = conn.execute(
                "SELECT COUNT(*) AS n FROM chunk_embeddings WHERE model = ?", (model,)
            ).fetchone()["n"]
        else:
            indexed = 0
            dim = 0
            chunks_indexed = 0
        return {
            "indexed": indexed,
            "total": total,
            "model": model,
            "dim": dim,
            "chunks_indexed": chunks_indexed,
            "chunks_total": chunks_total,
        }
    finally:
        conn.close()


def semantic_paper_ids(query: str, top_k: int = 20) -> list | None:
    """Cosine-ranks ``paper_id``s against ``query`` using the paper-level
    embedding index (``services.semantic_search.rank_by_cosine``, PURE
    ranking math re-used verbatim from ``routers/search.py``).

    Returns ``None`` -- not an empty list -- whenever semantic ranking
    cannot run at all: no query text, no ``LLM_EMBED_MODEL`` configured, no
    indexed vectors for the currently configured model (PRD Entscheidung 7:
    never compare vectors across models), or the query-embed HTTP call
    fails. Callers use that ``None`` to fall back to a lexical search
    themselves (PRD Entscheidung 5: degradation is a product requirement).
    An empty list is a real "found nothing" result and is returned as-is.

    Used by ``host_services._CoreLibraryApi.search_references`` (issue
    #104) to back ``plugin_api.LibraryApi.search_references`` with semantic
    ranking, additively over its existing lexical (``db.search_papers``)
    behaviour -- plugins predating embeddings keep working
    unchanged (contract allows lexical degradation).
    """
    query = (query or "").strip()
    if not query:
        return None
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        return None

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT paper_id, vector FROM paper_embeddings WHERE model = ?", (model,)
        ).fetchall()
        if not rows:
            return None

        try:
            query_vector = embed_texts([query], mode="query")[0]
        except Exception as e:
            logging.warning(f"Semantische Referenzsuche: Query-Embedding fehlgeschlagen: {e}")
            return None

        candidates = [
            {"paper_id": r["paper_id"], "vector": unpack_vector(r["vector"])} for r in rows
        ]
        ranked = _semantic.rank_by_cosine(query_vector, candidates, top_k=top_k)
        return [c["paper_id"] for c in ranked]
    finally:
        conn.close()
