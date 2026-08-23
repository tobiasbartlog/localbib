"""Neutral helper module: the post-import index hook (#153).

Ein frisch importiertes Paper braucht drei abgeleitete Indizes, damit es in
allen Suchpfaden auftaucht:

1. ``paper_chunks``      — Research-Chat / Passage-Retrieval (BM25-Basis)
2. ``paper_embeddings``  — semantische Suche auf Paper-Ebene (ein Vektor)
3. ``chunk_embeddings``  — semantische Suche auf Passage-Ebene (ein Vektor pro Chunk)

Bis #153 wurden (1) und (2) im Import-Pfad erzeugt, (3) aber ausschliesslich im
Maintenance-Vollauf ``reindex_chunks``. Neu importierte Paper fielen deshalb in
``/api/search/passages`` und im Research-Chat stillschweigend auf reines BM25
zurueck — sichtbar nur daran, dass ``chunk_embeddings`` fuer sie leer blieb.
Dieses Modul buendelt die drei Schritte zu EINEM Hook, damit kein Importpfad
mehr einen davon vergessen kann.

Wie ``pdf_chunking``/``embedding_index`` ist dies weder ein Router noch ein
``services/``-Modul: es darf ueber die dortigen Helfer persistieren (die Indizes
sind abgeleitete Daten, geschrieben auf dem Import-Pfad), macht aber keinerlei
HTTP-/Transport-Arbeit.

Kontrakt: ``index_paper_after_import`` wirft **nie**. Jeder Schritt degradiert
einzeln (PRD "Semantische Suche" Entscheidung 5) — ein ausgefallenes
Embedding-Gateway, ein Scan-PDF ohne Text oder ein fehlendes Modell darf einen
Import niemals scheitern lassen; alles Ausgefallene ist per Maintenance-Reindex
nachholbar.
"""

from __future__ import annotations

import logging

from embedding_index import embed_chunks_for_paper, embed_paper_to_db
from pdf_chunking import chunk_paper_to_db


def index_paper_after_import(paper_id: int, pdf_path: str) -> dict:
    """Baut alle abgeleiteten Indizes fuer ein frisch importiertes Paper.

    Reihenfolge ist bewusst: erst chunken, dann das Paper-Embedding (es nutzt
    die Chunks als Ersatz-Abstract, wenn kein echtes Abstract vorliegt — PRD
    Entscheidung 3), dann die Chunk-Vektoren.

    Args:
        paper_id: das gerade importierte Paper.
        pdf_path: Pfad zur abgelegten PDF (in ``Config.ALL_DIR``).

    Returns:
        ``{"chunks": int, "paper_embedded": bool, "chunk_vectors": int}`` —
        ``chunks`` ist die Gesamtzahl der Chunks des Papers (auch wenn es
        bereits gechunkt war), ``chunk_vectors`` die Zahl der in DIESEM Lauf
        neu erzeugten Chunk-Vektoren.
    """
    result = {"chunks": 0, "paper_embedded": False, "chunk_vectors": 0}

    try:
        result["chunks"] = chunk_paper_to_db(paper_id, pdf_path)
    except Exception as e:  # defensiv: chunk_paper_to_db faengt selbst schon ab
        logging.warning(f"Auto-Chunking fehlgeschlagen fuer Paper {paper_id}: {e}")

    try:
        result["paper_embedded"] = embed_paper_to_db(paper_id)
    except Exception as e:  # defensiv: embed_paper_to_db degradiert selbst
        logging.warning(f"Auto-Embedding fehlgeschlagen fuer Paper {paper_id}: {e}")

    try:
        result["chunk_vectors"] = embed_chunks_for_paper(paper_id)
    except Exception as e:  # defensiv: embed_chunks_for_paper degradiert selbst
        logging.warning(f"Auto-Chunk-Embedding fehlgeschlagen fuer Paper {paper_id}: {e}")

    return result
