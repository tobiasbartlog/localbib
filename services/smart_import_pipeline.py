"""Service: the smart-import metadata pipeline (PURE).

Consolidates the ~500-line block that used to be duplicated, near word-for-word,
between the ``/api/import/upload-smart`` and ``/api/import/process-smart/{filename}``
handlers (#87). This module owns the *pure* half of that pipeline: read the PDF
text, discover the DOI + CrossRef metadata (with the plausibility check),
apply the ISBN/year/title fallbacks, and run the optional LLM metadata
validation. It returns a structured :class:`ImportResult`.

PURE per the side-effect-free invariant (CLAUDE.md): no DB reads/writes, no SSE
formatting, no filesystem mutations. The only FS access is *reading* the given
PDF (text extraction + page count via PyMuPDF). All transport (SSE), all DB
dedup LOOKUPS, and all persistence (``add_paper`` / ``create_symlinks`` /
``chunk_paper_to_db`` / abstract write / file copy) stay in the ROUTER.

Progress reporting: ``run_pipeline`` is a generator that *yields* neutral
progress dicts (``{"type": "progress", "step": ..., "message": ..., "percent": ...}``)
in the exact order/content the old handlers emitted them, and ``return``s the
final :class:`ImportResult` (available via ``StopIteration.value``). The router
maps each yielded dict onto an SSE frame — the service never touches the wire.

External calls (``discover_doi`` / ``fetch_crossref_metadata`` / the LLM) are
imported at module level so tests can patch them on this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from literature_manager import (
    Config,
    discover_doi,
    extract_isbn_from_text,
    extract_text_from_pdf,
    extract_year_from_text,
    fetch_crossref_metadata,
)
from llm_client import llm_for
from services.metadata_extraction import llm_extract_metadata


@dataclass
class ImportResult:
    """Structured result of the pure smart-import metadata pipeline.

    Attributes
    ----------
    text:
        The full extracted PDF text (the router persists ``text[:10000]`` as
        ``ocr_text`` and feeds snippets to categorisation/abstract).
    metadata:
        The proposed metadata dict (title/authors/year/doi/isbn/abstract/
        journal/publisher). This is what the router writes into the paper row.
    crossref_plausible:
        Whether the CrossRef title overlapped the PDF text enough to be trusted.
        The router does not need it post-call, but it documents how ``metadata``
        was assembled (and the LLM-validation step already consumed it).
    title_signal:
        The proposed title used as the content-dedup SIGNAL. The router queries
        the DB with this (``find_duplicate_paper(doi=..., title=...)``) — the
        lookup itself stays in the router, keeping this service pure.
    doi_signal:
        The proposed DOI used as the other content-dedup signal.
    """

    text: str
    metadata: dict
    crossref_plausible: bool = False
    title_signal: str = ""
    doi_signal: str = ""


def _sse(data: dict) -> dict:
    """Identity helper kept for readability: the service yields plain progress
    dicts; the router is responsible for the ``data: ...\\n\\n`` SSE framing."""
    return data


def run_pipeline(
    target_path: str,
    original_filename: str,
    *,
    do_doi: bool = True,
    do_validate: bool = True,
) -> Iterator[dict]:
    """Run the pure metadata half of the smart-import pipeline.

    Yields progress dicts (router maps them to SSE) and returns an
    :class:`ImportResult` via ``StopIteration.value``.

    The PDF at ``target_path`` must already exist and (if configured) be
    unlocked — the router does the hash/dedup check and the unlock BEFORE
    calling this generator, then the persistence AFTER it.
    """
    # -- Text extrahieren --
    yield _sse({"type": "progress", "step": "text", "message": "Extrahiere Text aus PDF...", "percent": 10})
    text = extract_text_from_pdf(target_path, Config.MAX_OCR_PAGES)
    text_len = len(text.strip())
    yield _sse({"type": "progress", "step": "text", "message": f"{text_len} Zeichen extrahiert", "percent": 15})

    metadata = {
        "title": "", "authors": "", "year": None, "doi": "", "isbn": "",
        "abstract": "", "journal": "", "publisher": "",
    }

    # -- DOI suchen & CrossRef --
    crossref_plausible = False
    if do_doi:
        yield _sse({"type": "progress", "step": "doi", "message": "Suche DOI...", "percent": 20})
        doi = discover_doi(text, "", "")
        if doi:
            metadata["doi"] = doi
            yield _sse({"type": "progress", "step": "doi", "message": f"DOI gefunden: {doi}", "percent": 25})

            yield _sse({"type": "progress", "step": "crossref", "message": "Hole CrossRef-Metadaten...", "percent": 30})
            crossref_data = fetch_crossref_metadata(doi)
            if crossref_data:
                # Plausibilitaetspruefung: Stimmt der CrossRef-Titel mit dem PDF-Anfang ueberein?
                cr_words = set(re.sub(r'[^a-z0-9\s]', '', (crossref_data.get("title") or "").lower()).split())
                cr_words -= {'the', 'a', 'an', 'of', 'in', 'on', 'for', 'and', 'to', 'with', 'by', 'from', 'at', 'as', 'is'}
                if cr_words:
                    pdf_first_words = set(re.sub(r'[^a-z0-9\s]', '', text[:5000].lower()).split())
                    overlap = len(cr_words & pdf_first_words) / len(cr_words)
                    crossref_plausible = overlap >= 0.5
                else:
                    crossref_plausible = True

                if crossref_plausible:
                    metadata.update(crossref_data)
                    yield _sse({"type": "progress", "step": "crossref",
                                "message": f"Metadaten: {metadata['title'][:70]}...",
                                "percent": 35})
                else:
                    yield _sse({"type": "progress", "step": "crossref",
                                "message": f"CrossRef-Titel passt nicht zum PDF ('{crossref_data.get('title','')[:50]}...') - ignoriert",
                                "percent": 35})
                    metadata["doi"] = ""  # Verwerfe falsche DOI
            else:
                yield _sse({"type": "progress", "step": "crossref", "message": "CrossRef lieferte keine Daten", "percent": 35})
        else:
            yield _sse({"type": "progress", "step": "doi", "message": "Keine DOI gefunden", "percent": 35})

    # Fallbacks
    if not metadata["isbn"]:
        isbn = extract_isbn_from_text(text)
        if isbn:
            metadata["isbn"] = isbn

    if not metadata["year"]:
        year = extract_year_from_text(text)
        if year:
            metadata["year"] = year

    if not metadata["title"]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        metadata["title"] = lines[0][:200] if lines else original_filename.replace(".pdf", "")

    # -- LLM Metadaten-Validierung --
    if do_validate and Config.KICONNECT_API_KEY:
        yield _sse({"type": "progress", "step": "validate", "message": "KI validiert Metadaten...", "percent": 40})
        _llm = llm_for("metadata_extract")
        llm_data = llm_extract_metadata(
            _llm, text[:4000], metadata["title"], metadata["authors"],
            filename=original_filename, original_filename=original_filename,
        )
        if llm_data:
            # LLM-Titel uebernehmen wenn CrossRef nicht plausibel war oder kein Titel vorhanden
            if llm_data.get("title") and (not crossref_plausible or not metadata["title"] or "_" in metadata["title"]):
                metadata["title"] = llm_data["title"]
            if llm_data.get("authors") and (not metadata["authors"] or not crossref_plausible):
                metadata["authors"] = llm_data["authors"]
            if llm_data.get("year") and (not metadata["year"] or not crossref_plausible):
                metadata["year"] = llm_data["year"]
            if llm_data.get("doi") and not metadata["doi"]:
                metadata["doi"] = llm_data["doi"]
            if llm_data.get("isbn") and not metadata["isbn"]:
                metadata["isbn"] = llm_data["isbn"]
            yield _sse({"type": "progress", "step": "validate",
                        "message": f"KI: Titel = {metadata['title'][:60]}...", "percent": 50})
        else:
            yield _sse({"type": "progress", "step": "validate", "message": "KI-Validierung ohne Ergebnis", "percent": 50})
    else:
        yield _sse({"type": "progress", "step": "validate", "message": "KI-Validierung uebersprungen", "percent": 50})

    return ImportResult(
        text=text,
        metadata=metadata,
        crossref_plausible=crossref_plausible,
        title_signal=metadata.get("title", ""),
        doi_signal=metadata.get("doi", ""),
    )
