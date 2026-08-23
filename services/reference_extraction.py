"""Service: reference extraction from PDFs (PURE).

Provides the pure functions that make up the reference-extraction pipeline:

1. ``extract_all_pdf_pages``  — full-document page-text extraction (PyMuPDF).
2. ``find_reference_section`` — heuristic detection of the bibliography section.
3. ``llm_extract_references`` — LLM-based structured extraction (chunked).
4. ``crossref_doi_lookup``    — CrossRef title-based DOI lookup.
5. ``match_ref_to_library``   — library matching via ``paper_matcher``.

PURE per the side-effect-free invariant (CLAUDE.md / PRD-backend-modularization):
no DB writes, no SSE formatting, no filesystem mutations beyond *reading* the
given PDF. All HTTP is ``requests``-based (injectable/mockable at module level).
DB persistence and SSE emission live exclusively in ``routers/references.py``.

Moved from ``webapp.py`` as part of Backend-Modularisierung #88.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from literature_manager import Config
from llm_client import llm_for
from paper_matcher import match as _pm_match

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_all_pdf_pages(filepath: str) -> list[str]:
    """Gibt eine Liste mit dem Text jeder PDF-Seite zurueck (alle Seiten)."""
    import fitz
    try:
        doc = fitz.open(filepath)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return pages
    except Exception as e:
        logger.warning(f"PDF-Seitenextraktion fehlgeschlagen: {e}")
        return []


# ---------------------------------------------------------------------------
# Reference section detection
# ---------------------------------------------------------------------------

def find_reference_section(pages: list[str]) -> str:
    """Findet den Literaturverzeichnis-Abschnitt in PDF-Seitentexten.

    Heuristiken:
    - Ueberschrift-Matching: References, Bibliography, Literatur, Quellenverzeichnis, etc.
    - Scoring: DOI-Dichte, nummerierte Eintraege [1], Jahreszahlen (19xx/20xx), 'et al.'
    """
    if not pages:
        return ""

    # Header-Patterns fuer Referenzabschnitte
    # Unterstuetzt mehrstufige Nummerierung wie 9.1., 10.2.3 etc.
    ref_headers = re.compile(
        r'^\s*(?:\d+(?:\.\d+)*\.?\s+)?'
        r'(References|Bibliography|Literatur(?:verzeichnis)?|Quellenverzeichnis|'
        r'Literaturliste|Cited\s+Works|Works\s+Cited|Bibliographie|'
        r'Referenzen|Quellen|Literature|Sources|Bibliograf[ií]a)\s*$',
        re.IGNORECASE | re.MULTILINE,
    )

    # Score jede Seite nach Referenz-typischen Merkmalen
    def _score_page(text: str) -> float:
        score = 0.0
        lines = text.strip().split('\n')
        if not lines:
            return 0.0
        # DOI-Dichte
        doi_count = len(re.findall(r'10\.\d{4,}/', text))
        score += min(doi_count * 2.0, 10.0)
        # Nummerierte Eintraege [1], [2], ...
        numbered = len(re.findall(r'\[\d{1,3}\]', text))
        score += min(numbered * 1.5, 10.0)
        # Jahreszahlen in Klammern (19xx)/(20xx)
        years = len(re.findall(r'\((?:19|20)\d{2}\)', text))
        score += min(years * 1.0, 8.0)
        # "et al." Haeufigkeit
        etal = len(re.findall(r'et\s+al\.', text, re.IGNORECASE))
        score += min(etal * 1.5, 6.0)
        # Viele kurze Absaetze (typ. fuer Literaturlisten)
        short_lines = sum(1 for l in lines if 20 < len(l.strip()) < 300)
        if len(lines) > 5 and short_lines / len(lines) > 0.5:
            score += 3.0
        return score

    # Strategie 1: Suche nach expliziter Ueberschrift
    # Sammle alle Seiten mit Header-Match und waehle die erste in der hinteren Haelfte
    # (um Inhaltsverzeichnis-Erwaehnung und Running-Headers korrekt zu handhaben)
    header_matches = []
    for i, page_text in enumerate(pages):
        if ref_headers.search(page_text):
            header_matches.append(i)

    if header_matches:
        # Bevorzuge den ersten Match in der hinteren Haelfte des Dokuments
        half = len(pages) // 2
        later_matches = [m for m in header_matches if m >= half]
        best_start = later_matches[0] if later_matches else header_matches[-1]
        # Bestimme das Ende des Literaturverzeichnisses:
        # Seiten nach best_start mit Score >= 3.0 gehoeren dazu,
        # bei Score < 1.5 (Anhang/Index) aufhoeren
        ref_pages = [pages[best_start]]
        for j in range(best_start + 1, len(pages)):
            sc = _score_page(pages[j])
            if sc >= 1.5:
                ref_pages.append(pages[j])
            else:
                break
        return "\n\n--- PAGE BREAK ---\n\n".join(ref_pages)

    # Strategie 2: Scoring-basiert - letzte Seiten mit hohem Score
    if len(pages) >= 3:
        # Nur die letzten 40% der Seiten pruefen
        start_idx = max(0, int(len(pages) * 0.6))
        scores = [(i, _score_page(pages[i])) for i in range(start_idx, len(pages))]
        scores.sort(key=lambda x: -x[1])

        if scores and scores[0][1] >= 5.0:
            # Finde den Beginn des letzten zusammenhaengenden Clusters von hochscorenden Seiten
            high_pages = sorted([i for i, s in scores if s >= 4.0])
            # Letztes Cluster: Seiten die max 2 Seiten auseinander liegen
            cluster_start = high_pages[-1]
            for j in range(len(high_pages) - 2, -1, -1):
                if high_pages[j + 1] - high_pages[j] <= 2:
                    cluster_start = high_pages[j]
                else:
                    break
            ref_pages = pages[cluster_start:]
            return "\n\n--- PAGE BREAK ---\n\n".join(ref_pages)

    # Fallback: letzte 3 Seiten
    fallback = pages[-3:] if len(pages) >= 3 else pages
    return "\n\n--- PAGE BREAK ---\n\n".join(fallback)


# ---------------------------------------------------------------------------
# LLM reference extraction
# ---------------------------------------------------------------------------

REF_CHUNK_MAX_CHARS = 15000


def split_reference_text(ref_text: str, max_chars: int = REF_CHUNK_MAX_CHARS) -> list[str]:
    """Teilt den Referenz-Text in LLM-taugliche Chunks (an Zeilengrenzen).

    Gibt fuer kurze Texte genau einen Chunk zurueck. Reine Funktion ohne I/O;
    der Router iteriert darueber und meldet pro Chunk Fortschritt (ADR-0006)."""
    if len(ref_text) <= max_chars:
        return [ref_text]
    chunks = []
    current = 0
    while current < len(ref_text):
        end = current + max_chars
        # Nicht mitten in einer Zeile abschneiden
        if end < len(ref_text):
            nl = ref_text.rfind('\n', current, end)
            if nl > current:
                end = nl + 1
        chunks.append(ref_text[current:end])
        current = end
    return chunks


def extract_references_from_chunk(chunk: str) -> list[dict]:
    """Extrahiert strukturierte Referenzen aus EINEM Text-Chunk via LLM."""
    if not Config.KICONNECT_API_KEY:
        return []
    return _llm_extract_references_single(chunk)


def llm_extract_references(ref_text: str) -> list[dict]:
    """Sendet den Referenz-Text an das LLM und extrahiert strukturierte Referenzen.
    Bei langen Texten wird in Chunks aufgeteilt und mehrfach aufgerufen.

    Bequemer Wrapper fuer Aufrufer ohne Feinfortschritt (z.B. Bulk-Extraktion).
    Der Single-Paper-Weg iteriert stattdessen selbst ueber
    ``split_reference_text`` + ``extract_references_from_chunk``."""
    if not Config.KICONNECT_API_KEY:
        return []
    all_refs: list[dict] = []
    for chunk in split_reference_text(ref_text):
        all_refs.extend(extract_references_from_chunk(chunk))
    return all_refs


def _llm_extract_references_single(ref_text: str) -> list[dict]:
    """Sendet einen einzelnen Referenz-Text-Chunk an das LLM."""

    prompt = f"""Du bist ein Experte fuer wissenschaftliche Literatur.
Analysiere den folgenden Literaturverzeichnis-Abschnitt aus einem wissenschaftlichen Paper
und extrahiere ALLE Referenzen als strukturierte Liste.

TEXT DES LITERATURVERZEICHNISSES:
{ref_text}

Extrahiere fuer jede Referenz die folgenden Felder (soweit vorhanden):
- title: Der vollstaendige Titel des zitierten Werks
- authors: Autoren im Format "Nachname1, Vorname1; Nachname2, Vorname2"
- year: Erscheinungsjahr als Zahl
- journal: Zeitschrift/Konferenz/Verlag
- doi: DOI falls im Text angegeben (Format: 10.xxxx/...)

WICHTIG:
- Extrahiere ALLE Referenzen, nicht nur einige wenige
- Gib den Titel immer vollstaendig und korrekt wieder
- Wenn kein DOI im Text steht, lass das Feld leer
- Antworte NUR mit einem JSON-Array, kein anderer Text

Format (NUR JSON-Array):
[
  {{"title": "Titel des Werks", "authors": "Nachname, Vorname", "year": 2020, "journal": "Journal Name", "doi": "10.xxxx/..."}},
  ...
]"""

    return llm_for("reference_extract").complete_json(
        [{"role": "user", "content": prompt}], expect=list, default=[], timeout=120
    )


# ---------------------------------------------------------------------------
# CrossRef DOI lookup
# ---------------------------------------------------------------------------

def crossref_doi_lookup(title: str, authors: str = "") -> Optional[str]:
    """Sucht DOI bei CrossRef anhand Titel+Autoren. Gibt DOI zurueck oder None."""
    if not title or len(title) < 10:
        return None
    try:
        query = title
        if authors:
            # Ersten Autor-Nachnamen hinzufuegen
            first_author = authors.split(";")[0].split(",")[0].strip()
            if first_author:
                query = f"{title} {first_author}"

        params = {
            "query.bibliographic": query,
            "rows": 3,
            "select": "DOI,title",
        }
        if Config.polite_mailto():
            params["mailto"] = Config.polite_mailto()
        resp = requests.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": Config.user_agent()},
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        items = resp.json().get("message", {}).get("items", [])
        if not items:
            return None

        def _norm(s):
            return re.sub(r'[^a-z0-9\s]', '', s.lower()).strip()

        query_norm = _norm(title)
        query_words = set(query_norm.split())
        for item in items:
            result_titles = item.get("title", [])
            if not result_titles:
                continue
            result_norm = _norm(result_titles[0])
            result_words = set(result_norm.split())
            if not query_words or not result_words:
                continue
            overlap = len(query_words & result_words)
            similarity = overlap / max(len(query_words), len(result_words))
            if similarity >= 0.65:
                return item.get("DOI", "")
        return None
    except Exception as e:
        logger.warning(f"CrossRef DOI-Lookup fehlgeschlagen: {e}")
        return None


# ---------------------------------------------------------------------------
# Library matching
# ---------------------------------------------------------------------------

def match_ref_to_library(ref: dict, conn) -> dict:
    """Prueft ob eine extrahierte Referenz bereits in der Bibliothek existiert."""
    result = _pm_match(ref, conn)
    return {
        "matched_paper_id": result.matched_paper_id,
        "match_confidence": result.match_confidence,
    }
