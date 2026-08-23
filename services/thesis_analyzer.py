"""Service: thesis analysis (PURE).

Provides pure functions for analysing submitted theses:

1. ``extract_author_names``           — parse author last-names from a string.
2. ``match_citations_to_references``  — match body-text citations to a reference list.
3. ``check_online_availability``      — HTTP availability check (no DB writes).

PURE per the side-effect-free invariant (CLAUDE.md / PRD-backend-modularization):
no DB writes, no SSE formatting, no filesystem mutations. HTTP calls in
``check_online_availability`` are done via ``requests`` and are mockable at
the function level by patching ``routers.analysis._check_online_availability``.

Moved from ``webapp.py`` as part of Backend-Modularisierung #90.
"""
from __future__ import annotations

import logging
import re

import requests as http_requests

from literature_manager import Config
from openalex_client import OpenAlexClient
from services.reference_extraction import crossref_doi_lookup as _crossref_doi_lookup

logger = logging.getLogger(__name__)


def _oa_mailto() -> str:
    return Config.polite_mailto()


# ---------------------------------------------------------------------------
# Author-name extraction
# ---------------------------------------------------------------------------

def extract_author_names(authors_str: str) -> list[str]:
    """Extrahiert Nachnamen aus einem Autoren-String.

    Erkennt Formate wie:
    - 'Mueller, Hans; Schmidt, Anna'
    - 'Mueller, H.; Schmidt, A.'
    - 'Hans Mueller, Anna Schmidt'
    - 'Mueller and Schmidt'
    """
    if not authors_str or not authors_str.strip():
        return []

    names = []
    # Aufteilen nach ; oder ' and ' oder ' und ' oder ' & '
    parts = re.split(r'\s*[;]\s*|\s+and\s+|\s+und\s+|\s*&\s*', authors_str)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Format "Nachname, Vorname" -> erster Teil ist Nachname
        if ',' in part:
            lastname = part.split(',')[0].strip()
        else:
            # Format "Vorname Nachname" -> letztes Wort ist Nachname
            words = part.split()
            lastname = words[-1] if words else ""

        # Bereinigen: nur Buchstaben, Leerzeichen und Bindestriche (fuer Multi-Wort-Namen wie "Ghazi Wakili")
        lastname = re.sub(r'[^a-zA-ZäöüÄÖÜß\- ]', '', lastname).strip()
        if lastname and len(lastname) >= 2:
            names.append(lastname)

    return names


# ---------------------------------------------------------------------------
# Citation-to-reference matching
# ---------------------------------------------------------------------------

def match_citations_to_references(
    references: list[dict],
    body_text: str,
) -> tuple[list[dict], list[dict]]:
    """Gleicht Referenzen mit dem Fliesstext ab.

    Neuer Ansatz: Leitet aus den extrahierten Referenzen die wahrscheinlichen
    Zitationsformen ab und sucht diese im Body-Text.

    Unterstuetzt automatisch:
    - Nummerierte Zitationen: [1], [2,3], [1-5]
    - Harvard-Stil: (Nachname, Jahr), (Nachname et al., Jahr)
    - Narrativer Stil: Nachname (Jahr), Nachname et al. (Jahr)
    - Deutsche Varianten: vgl. Nachname, (vgl. Nachname, Jahr)

    Gibt zurueck:
    - used_refs: Referenzen die im Text zitiert werden
    - unused_refs: Referenzen die NICHT im Text gefunden werden
    """
    used_indices = set()
    body_lower = body_text.lower()  # noqa: F841 — kept for parity with original

    # ---------------------------------------------------------------
    # Phase 1: Nummerierte Zitationen pruefen [1], [2], [3-5] etc.
    # ---------------------------------------------------------------
    numbered_markers = set()
    for m in re.finditer(r'\[(\d[\d,\s\-–]+)\]', body_text):
        content = m.group(1)
        for part in re.split(r'[,;]\s*', content):
            part = part.strip()
            range_match = re.match(r'(\d+)\s*[-–]\s*(\d+)', part)
            if range_match:
                start_n, end_n = int(range_match.group(1)), int(range_match.group(2))
                for n in range(start_n, min(end_n + 1, start_n + 100)):
                    numbered_markers.add(n)
            elif part.isdigit():
                numbered_markers.add(int(part))

    # Wenn genuegend nummerierte Marker gefunden -> nummerierte Zuordnung
    if len(numbered_markers) >= 3:
        for i, ref in enumerate(references):
            ref_idx = ref.get("_ref_index", i + 1)
            if ref_idx in numbered_markers:
                used_indices.add(i)

    # ---------------------------------------------------------------
    # Phase 2: Referenzbasierte Suche (Kern des neuen Ansatzes)
    # Fuer jede Referenz: Nachnamen + Jahr extrahieren, daraus
    # Suchpatterns ableiten und im Body suchen
    # ---------------------------------------------------------------
    for i, ref in enumerate(references):
        if i in used_indices:
            continue  # Bereits ueber Nummern gefunden

        authors_str = (ref.get("authors") or "").strip()
        year = ref.get("year")
        title = (ref.get("title") or "").strip()

        author_names = extract_author_names(authors_str)
        first_author = author_names[0] if author_names else ""

        if not first_author and not title:
            continue

        found = False

        # --- Autorname + Jahr Patterns ---
        if first_author and year:
            year_str = str(year)
            fa_escaped = re.escape(first_author)

            # Alle gaengigen Zitationsformen generieren
            # WICHTIG: [^)]* bzw. [^\]]* erlaubt Seitenzahlen wie ", S. 293-294" vor der schliessenden Klammer
            search_patterns = [
                # Harvard runde Klammern: (Mueller, 2020), (Mueller 2020), (Mueller, 2020, S. 5)
                r'\(' + fa_escaped + r'[,\s]+' + year_str + r'[a-z]?' + r'[^)]*\)',
                # Harvard runde Klammern mit et al.: (Mueller et al., 2020, S. 293-294)
                r'\(' + fa_escaped + r'\s+et\s+al\.?,?\s*' + year_str + r'[a-z]?' + r'[^)]*\)',
                # Harvard eckige Klammern: [Mueller, 2020], [Mueller 2020, p. 5]
                r'\[' + fa_escaped + r'[,\s]+' + year_str + r'[a-z]?' + r'[^\]]*\]',
                # Narrativ: Mueller (2020), Mueller (2020, S. 5)
                fa_escaped + r'\s+\(' + year_str + r'[a-z]?' + r'[^)]*\)',
                # Narrativ mit et al.: Mueller et al. (2020, S. 293)
                fa_escaped + r'\s+et\s+al\.?\s*\(' + year_str + r'[a-z]?' + r'[^)]*\)',
                # Deutsch narrativ: Mueller und Schmidt (2020)
                fa_escaped + r'\s+(?:und|and|&)\s+\w+\s+\(' + year_str + r'[a-z]?' + r'[^)]*\)',
                # vgl.-Varianten: (vgl. Mueller, 2020, S. 5), (vgl. Mueller 2020)
                r'\(\s*vgl\.?\s+' + fa_escaped + r'[,\s]+' + year_str + r'[a-z]?' + r'[^)]*\)',
                # vgl. mit et al.: (vgl. Mueller et al., 2020, S. 293-294)
                r'\(\s*vgl\.?\s+' + fa_escaped + r'\s+et\s+al\.?,?\s*' + year_str + r'[a-z]?' + r'[^)]*\)',
                # Kompakter Stil: Nachname + Jahr nah beieinander (max 30 Zeichen)
                fa_escaped + r'.{0,30}' + year_str,
            ]

            # Bei 2 Autoren: Kombination beider Nachnamen
            if len(author_names) >= 2:
                sa_escaped = re.escape(author_names[1])
                search_patterns.extend([
                    r'\(' + fa_escaped + r'\s+(?:und|and|&)\s+' + sa_escaped + r'[,\s]+' + year_str + r'[a-z]?' + r'[^)]*\)',
                    fa_escaped + r'\s+(?:und|and|&)\s+' + sa_escaped + r'\s+\(' + year_str + r'[a-z]?' + r'[^)]*\)',
                ])

            for pattern in search_patterns:
                try:
                    if re.search(pattern, body_text, re.IGNORECASE):
                        found = True
                        break
                except re.error:
                    continue

        # --- Nur Nachname (ohne Jahr) als Fallback ---
        if not found and first_author and len(first_author) >= 3:
            # Suche nach "(Nachname," oder "(vgl. Nachname" ohne Jahr-Anforderung
            # Nur wenn der Name selten genug ist (nicht bei sehr kurzen/haeufigen Namen)
            name_count = len(re.findall(re.escape(first_author), body_text, re.IGNORECASE))
            # Wenn der Name im Body vorkommt aber nicht im Literaturverzeichnis-Bereich
            # (d.h. er taucht tatsaechlich als Zitation auf)
            if 1 <= name_count <= 50:
                # Pruefen ob der Name in einem Zitationskontext auftaucht
                citation_contexts = [
                    r'\([^)]*' + re.escape(first_author) + r'[^)]*\)',  # In runden Klammern
                    r'\[[^\]]*' + re.escape(first_author) + r'[^\]]*\]',  # In eckigen Klammern
                    r'(?:vgl\.?|see|s\.)\s+' + re.escape(first_author),  # Nach vgl./see
                    r'(?:nach|laut|gemaess|according\s+to)\s+' + re.escape(first_author),  # Nach laut/nach
                ]
                for ctx_pat in citation_contexts:
                    try:
                        if re.search(ctx_pat, body_text, re.IGNORECASE):
                            found = True
                            break
                    except re.error:
                        continue

        # --- Titel-Fallback: 3+ aufeinanderfolgende Woerter ---
        if not found and title and len(title) > 15:
            title_words = [w for w in title.split() if len(w) >= 3]  # Kurze Woerter ignorieren
            min_words = 3 if len(title_words) <= 6 else 4
            if len(title_words) >= min_words:
                for j in range(len(title_words) - min_words + 1):
                    search_phrase = r'\s+'.join(re.escape(w) for w in title_words[j:j + min_words])
                    try:
                        if re.search(search_phrase, body_text, re.IGNORECASE):
                            found = True
                            break
                    except re.error:
                        continue

        if found:
            used_indices.add(i)

    # ---------------------------------------------------------------
    # Ergebnis aufbauen
    # ---------------------------------------------------------------
    used_refs = []
    unused_refs = []
    for i, ref in enumerate(references):
        if i in used_indices:
            ref["_used_in_text"] = True
            used_refs.append(ref)
        else:
            ref["_used_in_text"] = False
            unused_refs.append(ref)

    return used_refs, unused_refs


# ---------------------------------------------------------------------------
# Online availability check
# ---------------------------------------------------------------------------

def check_online_availability(ref: dict) -> dict:
    """Prueft ob eine Referenz online auffindbar ist.

    Strategien:
    1. DOI-Lookup (wenn DOI vorhanden)
    2. CrossRef Title-Search
    3. OpenAlex Title-Search

    Gibt ein dict zurueck mit online_found (bool) und details.
    """
    result = {"online_found": False, "doi_found": None, "source": None, "openalex_id": None}
    mailto = _oa_mailto()

    ref_doi = (ref.get("doi") or "").strip()
    ref_title = (ref.get("title") or "").strip()
    ref_authors = (ref.get("authors") or "").strip()

    # Strategie 1: DOI pruefen
    if ref_doi:
        # DOI bereinigen
        clean_doi = ref_doi
        if clean_doi.startswith("http"):
            clean_doi = re.sub(r'^https?://doi\.org/', '', clean_doi)
        clean_doi = clean_doi.strip().rstrip('.')

        try:
            r = http_requests.get(
                f"https://doi.org/{clean_doi}",
                allow_redirects=False,
                timeout=10,
                headers={
                    "Accept": "application/json",
                    "User-Agent": Config.user_agent(),
                },
            )
            if r.status_code in (200, 301, 302, 303):
                result["online_found"] = True
                result["doi_found"] = clean_doi
                result["source"] = "doi"
                return result
        except Exception as e:
            logger.debug(f"DOI-Check fehlgeschlagen fuer {clean_doi}: {e}")

    # Strategie 2: CrossRef Title-Search
    if ref_title and len(ref_title) >= 10:
        try:
            found_doi = _crossref_doi_lookup(ref_title, ref_authors)
            if found_doi:
                result["online_found"] = True
                result["doi_found"] = found_doi
                result["source"] = "crossref"
                return result
        except Exception as e:
            logger.debug(f"CrossRef-Suche fehlgeschlagen fuer '{ref_title[:50]}': {e}")

    # Strategie 3: OpenAlex Title-Search
    if ref_title and len(ref_title) >= 10:
        try:
            oa = OpenAlexClient(mailto)
            work = oa.fetch_work_by_title(ref_title, min_similarity=0.55)
            if work and work.doi:
                result["online_found"] = True
                result["doi_found"] = work.doi
                result["source"] = "openalex"
                result["openalex_id"] = work.id
                return result
        except Exception as e:
            logger.debug(f"OpenAlex-Suche fehlgeschlagen fuer '{ref_title[:50]}': {e}")

    return result
