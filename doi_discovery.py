#!/usr/bin/env python3
"""DOI-Entdeckung: Regex-Extraktion (DOI/Jahr/ISBN/arXiv) + Online-Suche."""

import re
import logging
from typing import Optional

import requests

from config import Config
from openalex_client import OpenAlexClient


def extract_doi_from_text(text: str, max_chars: int = 3000) -> Optional[str]:
    """Extrahiert DOI aus Text mittels Regex.

    Durchsucht nur die ersten max_chars Zeichen (ca. erste 1-2 Seiten),
    um zu vermeiden, dass DOIs aus dem Literaturverzeichnis als DOI des
    Dokuments selbst fehlidentifiziert werden.
    """
    search_text = text[:max_chars]
    # Verschiedene DOI-Formate
    patterns = [
        r'(?:doi[:\s]*)(10\.\d{4,}/[^\s]+)',
        r'https?://(?:dx\.)?doi\.org/(10\.\d{4,}/[^\s]+)',
        r'(10\.\d{4,}/[^\s,;]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            doi = match.group(1).rstrip(".")
            return doi
    return None


def extract_year_from_text(text: str) -> Optional[int]:
    """Versucht Jahreszahl aus Text zu extrahieren."""
    # Suche nach typischen Jahresangaben (1990-2029)
    matches = re.findall(r'\b(19[9]\d|20[0-2]\d)\b', text[:3000])
    if matches:
        # Häufigste Jahreszahl nehmen
        from collections import Counter
        counter = Counter(matches)
        return int(counter.most_common(1)[0][0])
    return None


def extract_isbn_from_text(text: str) -> Optional[str]:
    """Extrahiert ISBN aus Text."""
    patterns = [
        r'ISBN[-:\s]*((?:97[89][-\s]?\d[-\s]?\d{2,4}[-\s]?\d{2,6}[-\s]?\d))',
        r'ISBN[-:\s]*([\d-]{10,17})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).replace("-", "").replace(" ", "")
    return None


def extract_arxiv_id_from_text(text: str) -> Optional[str]:
    """Extrahiert arXiv-ID aus Text (z.B. arxiv.org/abs/1803.10122 oder arXiv:1803.10122)."""
    patterns = [
        r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)',
        r'arXiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)',
        r'arxiv\.org/(?:abs|pdf)/([\w.-]+/\d{7}(?:v\d+)?)',
        r'arXiv[:\s]+([\w.-]+/\d{7}(?:v\d+)?)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def resolve_doi_from_arxiv(arxiv_id: str) -> Optional[str]:
    """Loest eine arXiv-ID zu einer DOI auf.

    Bevorzugt die arXiv DataCite DOI (10.48550/arXiv.{id}), da diese von
    OpenAlex und den meisten Datenbanken indexiert wird. Faellt zurueck auf
    die in der arXiv API hinterlegte DOI (oft Zenodo), falls vorhanden.
    """
    # Strategie 1: arXiv DataCite DOI (von OpenAlex indexiert)
    arxiv_doi = f"10.48550/arXiv.{arxiv_id}"
    logging.info(f"   arXiv {arxiv_id} → DataCite DOI: {arxiv_doi}")
    return arxiv_doi


def search_crossref_for_doi(title: str, authors: str = "") -> Optional[str]:
    """Sucht bei CrossRef nach DOI anhand von Titel und optionalen Autoren.

    Prueft ob der erste Treffer wirklich zum Titel passt (Aehnlichkeit > 0.7).
    """
    if not title or len(title) < 5:
        return None
    try:
        params = {
            "query.bibliographic": f"{title} {authors}".strip(),
            "rows": 3,
            "select": "DOI,title,author",
        }
        if Config.polite_mailto():
            params["mailto"] = Config.polite_mailto()
        response = requests.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": Config.user_agent()},
            timeout=15,
        )
        if response.status_code != 200:
            return None

        items = response.json().get("message", {}).get("items", [])
        if not items:
            return None

        # Aehnlichkeit pruefen: Titel normalisieren und vergleichen
        def _normalize(s):
            return re.sub(r'[^a-z0-9\s]', '', s.lower()).strip()

        query_norm = _normalize(title)
        for item in items:
            result_titles = item.get("title", [])
            if not result_titles:
                continue
            result_norm = _normalize(result_titles[0])
            # Einfacher Aehnlichkeitscheck: gemeinsame Woerter
            query_words = set(query_norm.split())
            result_words = set(result_norm.split())
            if not query_words:
                continue
            overlap = len(query_words & result_words)
            similarity = overlap / max(len(query_words), len(result_words))
            if similarity >= 0.6:
                doi = item.get("DOI", "")
                if doi:
                    logging.info(f"   CrossRef Suche: '{title[:50]}' → DOI: {doi}")
                    return doi

    except Exception as e:
        logging.warning(f"   CrossRef-Suche fehlgeschlagen: {e}")
    return None


def search_openalex_for_doi(title: str, authors: str = "", year: Optional[int] = None) -> Optional[str]:
    """Sucht bei OpenAlex nach DOI anhand von Titel und optional Autor/Jahr."""
    if not title or len(title) < 5:
        return None
    try:
        mailto = Config.polite_mailto()
        oa = OpenAlexClient(mailto)
        work = oa.fetch_work_by_title(title, authors=authors)
        if work and work.doi:
            logging.info(f"   OpenAlex Suche: '{title[:50]}' → DOI: {work.doi}")
            return work.doi
    except Exception as e:
        logging.warning(f"   OpenAlex-Suche fehlgeschlagen: {e}")
    return None


def discover_doi(text: str, title: str = "", authors: str = "", year: Optional[int] = None) -> Optional[str]:
    """Versucht ueber mehrere Strategien eine DOI zu finden:
    1. DOI direkt im Text
    2. arXiv-ID im Text → arXiv DataCite DOI
    3. OpenAlex Titelsuche
    4. CrossRef Titelsuche
    """
    # Strategie 1: DOI direkt im Text
    doi = extract_doi_from_text(text)
    if doi:
        return doi

    # Strategie 2: arXiv-ID → DataCite DOI (von OpenAlex indexiert)
    arxiv_id = extract_arxiv_id_from_text(text)
    if arxiv_id:
        doi = resolve_doi_from_arxiv(arxiv_id)
        if doi:
            return doi

    # Strategie 3: OpenAlex Titelsuche
    if title:
        doi = search_openalex_for_doi(title, authors, year)
        if doi:
            return doi

    # Strategie 4: CrossRef Titelsuche (nur wenn Titel vorhanden)
    if title:
        doi = search_crossref_for_doi(title, authors)
        if doi:
            return doi

    return None
