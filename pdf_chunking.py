"""Neutral helper module: PDF text chunking + persistence for the Research-Chat.

Holds the chunking trio relocated out of ``routers/bibtex_import.py`` (#87) so
that BOTH the bibtex-import router and the smart-import router can use it without
a router-to-router import (which the ``routers-independent`` import-linter
contract forbids).

This module is neither a router nor a service: it is allowed to persist via
``context.get_conn`` (the chunk rows are an index over the PDF, written on the
single import path). It performs no HTTP/transport work.

Functions:
  * ``_chunk_text``         — pure: splits text into overlapping chunks.
  * ``_extract_pages_text`` — reads a PDF page-by-page (PyMuPDF).
  * ``chunk_paper_to_db``   — chunks a paper's PDF and stores rows in paper_chunks.
"""

from __future__ import annotations

import logging

from context import get_conn

# Alias so code relocated verbatim keeps its ``_get_conn()`` calls.
_get_conn = get_conn


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list:
    """Teilt Text in ueberlappende Chunks auf.

    Returns list of dicts: [{text, char_start, char_end}, ...]
    """
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Am Satzende oder Absatz umbrechen wenn moeglich
        if end < len(text):
            # Suche nach dem letzten Satzende im Chunk
            best_break = -1
            for sep in ['\n\n', '.\n', '. ', '! ', '? ']:
                pos = text.rfind(sep, start + chunk_size // 2, end)
                if pos > best_break:
                    best_break = pos + len(sep)
            if best_break > start:
                end = best_break

        chunk_text = text[start:end].strip()
        if len(chunk_text) > 50:  # Nur Chunks mit genuegend Text
            chunks.append({
                "text": chunk_text,
                "char_start": start,
                "char_end": end,
            })

        start = end - overlap if end < len(text) else len(text)

    return chunks


def _extract_pages_text(pdf_path: str) -> list:
    """Extrahiert Text seitenweise aus PDF. Returns [(page_num, text), ...]"""
    pages = []
    try:
        import fitz
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if text and text.strip():
                pages.append((page_num + 1, text.strip()))
        doc.close()
    except Exception as e:
        logging.warning(f"PDF-Seitenextraktion fehlgeschlagen: {e}")
    return pages


def chunk_paper_to_db(paper_id: int, pdf_path: str) -> int:
    """Chunked ein Paper und speichert die Chunks in paper_chunks.

    Gibt die Anzahl gespeicherter Chunks zurueck.
    """
    conn = _get_conn()
    try:
        # Pruefen ob bereits Chunks vorhanden
        existing = conn.execute(
            "SELECT COUNT(*) FROM paper_chunks WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0]
        if existing > 0:
            return existing  # Bereits gechunkt

        # Text seitenweise extrahieren
        pages = _extract_pages_text(pdf_path)
        if not pages:
            # Fallback: OCR-Text aus DB verwenden
            row = conn.execute("SELECT ocr_text FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if row and row["ocr_text"]:
                pages = [(1, row["ocr_text"])]
            else:
                return 0

        # Pro Seite chunken und Seitenzuordnung merken
        all_chunks = []
        for page_num, page_text in pages:
            page_chunks = _chunk_text(page_text, chunk_size=800, overlap=150)
            for pc in page_chunks:
                pc["page"] = page_num
            all_chunks.extend(page_chunks)

        if not all_chunks:
            return 0

        # In DB schreiben
        for i, chunk in enumerate(all_chunks):
            token_count = len(chunk["text"].split())
            conn.execute(
                """INSERT INTO paper_chunks
                   (paper_id, chunk_index, page_start, page_end, chunk_text, token_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (paper_id, i, chunk["page"], chunk["page"], chunk["text"], token_count),
            )

        conn.commit()
        logging.info(f"Research-Chat: {len(all_chunks)} Chunks fuer Paper {paper_id} gespeichert")
        return len(all_chunks)
    except Exception as e:
        logging.error(f"Chunking fehlgeschlagen fuer Paper {paper_id}: {e}")
        return 0
    finally:
        conn.close()
