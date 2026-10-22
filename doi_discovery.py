#!/usr/bin/env python3
"""DOI-Entdeckung: Regex-Extraktion (DOI/Jahr/ISBN/arXiv) + Online-Suche."""

import re
import logging
from typing import Optional

import requests

from config import Config
from openalex_client import OpenAlexClient


_DOI_CORE = r'10\.\d{4,9}/[^\s"<>]+'

# A DOI the text names as one ("doi:", "doi.org/"), as opposed to any
# 10.xxxx/... string, which may just as well be a cited work.
_LABELED_DOI = re.compile(
    rf'(?:https?://(?:dx\.)?doi\.org/|\bdoi[:\s]\s*)({_DOI_CORE})', re.IGNORECASE)

# Where the bibliography starts, nothing after it names the document itself.
_REFERENCE_HEADING = re.compile(
    r'\n\s*(?:\d+\.?\s*)?(?:references|bibliography|literature cited|works cited'
    r'|literatur(?:verzeichnis)?|quellenverzeichnis)\s*\n', re.IGNORECASE)

_CLOSERS = {")": "(", "]": "[", "}": "{"}


def _clean_doi(doi: str) -> str:
    """Trailing punctuation the regex swallowed (``…123726.``, ``(doi:…)``)
    does not belong to the DOI; a closing bracket only when it is unbalanced
    — ``10.1016/0002-9149(83)90013-1`` keeps its own."""
    while doi:
        last = doi[-1]
        if last in ".,;:'":
            doi = doi[:-1]
        elif last in _CLOSERS and doi.count(last) > doi.count(_CLOSERS[last]):
            doi = doi[:-1]
        else:
            break
    return doi


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
            doi = _clean_doi(match.group(1))
            if doi:
                return doi
    return None


def extract_labeled_doi_from_text(text: str, max_chars: int = 12000) -> Optional[str]:
    """The first DOI the text *names* as one (``https://doi.org/…``, ``doi:…``)
    before its bibliography, within ``max_chars``.

    Wider than :func:`extract_doi_from_text`: a journal's first page often
    carries its DOI in the footer, after title, abstract and keywords — in a
    large publisher's PDF well past the first 3000 characters. Only a labelled DOI
    counts out there, and never one after the reference heading, so a cited
    work's DOI is not taken for the document's own.
    """
    search_text = text[:max_chars]
    heading = _REFERENCE_HEADING.search(search_text)
    if heading:
        search_text = search_text[:heading.start()]
    match = _LABELED_DOI.search(search_text)
    if not match:
        return None
    return _clean_doi(match.group(1)) or None


def doi_from_pdf_metadata(info: Optional[dict], xmp: str = "") -> Optional[str]:
    """The DOI a PDF's own metadata names: the XMP's ``prism:doi`` /
    ``crossmark:DOI`` / ``pdfx:doi`` first, then any DOI in the Info
    dictionary (e.g. ``subject = "Engineering Structures, 368 (2026)
    123726. doi:10.1016/…"``). The publisher wrote these about this very
    file, so they beat anything read from the page text."""
    if xmp:
        tagged = re.search(
            r'<(?:prism:doi|crossmark:DOI|pdfx:doi)>\s*(?:doi:|https?://(?:dx\.)?doi\.org/)?'
            r'(10\.\d{4,9}/[^\s"<>]+)\s*<', xmp, re.IGNORECASE)
        if tagged:
            return _clean_doi(tagged.group(1)) or None
    for key in ("doi", "subject", "keywords", "title"):
        value = (info or {}).get(key) or ""
        match = re.search(_DOI_CORE, value)
        if match:
            doi = _clean_doi(match.group(0))
            if doi:
                return doi
    return None


def read_pdf_metadata(filepath: str) -> tuple[dict, str]:
    """The PDF's Info dictionary and XMP packet; ``({}, "")`` when unreadable."""
    try:
        import fitz  # PyMuPDF
        with fitz.open(filepath) as doc:
            return dict(doc.metadata or {}), doc.get_xml_metadata() or ""
    except Exception as e:
        logging.debug("PDF metadata unreadable (%s): %s", filepath, e)
        return {}, ""


_UNUSABLE_TITLE = re.compile(r'(?i)^(untitled|microsoft word|document\d*)\b|\.(pdf|docx?|tex|indd)$')


def title_from_pdf_metadata(info: Optional[dict]) -> str:
    """The Info dictionary's title when it reads like one — not empty, not a
    file name (``paper.docx``), not a word processor's placeholder."""
    title = ((info or {}).get("title") or "").strip()
    if len(title) < 10 or len(title.split()) < 2 or _UNUSABLE_TITLE.search(title):
        return ""
    return title


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


_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)


def canonical_arxiv_doi(doi: str) -> Optional[str]:
    """Die versionslose Form einer arXiv-DOI mit Versionsendung
    (``10.48550/arXiv.1803.10122v4`` → ``…10122``), sonst ``None``."""
    doi = (doi or "").strip()
    if not doi.lower().startswith("10.48550/arxiv.") or not _ARXIV_VERSION.search(doi):
        return None
    return _ARXIV_VERSION.sub("", doi)


def resolve_doi_from_arxiv(arxiv_id: str) -> Optional[str]:
    """Loest eine arXiv-ID zu einer DOI auf.

    Bevorzugt die arXiv DataCite DOI (10.48550/arXiv.{id}), da diese von
    OpenAlex und den meisten Datenbanken indexiert wird. Faellt zurueck auf
    die in der arXiv API hinterlegte DOI (oft Zenodo), falls vorhanden.
    """
    # Strategie 1: arXiv DataCite DOI (von OpenAlex indexiert). Die DOI
    # benennt das Werk, nicht eine Fassung: ``v2`` gehoert nicht hinein.
    arxiv_doi = f"10.48550/arXiv.{_ARXIV_VERSION.sub('', arxiv_id)}"
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


def discover_doi(text: str, title: str = "", authors: str = "", year: Optional[int] = None,
                 pdf_metadata: Optional[tuple[dict, str]] = None) -> Optional[str]:
    """Versucht ueber mehrere Strategien eine DOI zu finden:
    0. DOI in den PDF-Metadaten (``pdf_metadata`` = :func:`read_pdf_metadata`)
    1. DOI direkt im Text (Anfang)
    2. arXiv-ID im Text → arXiv DataCite DOI
    3. als DOI ausgezeichnete DOI bis zum Literaturverzeichnis
    4. OpenAlex Titelsuche
    5. CrossRef Titelsuche
    """
    # Strategie 0: die Metadaten, die der Verlag in genau diese Datei schrieb
    if pdf_metadata:
        doi = doi_from_pdf_metadata(*pdf_metadata)
        if doi:
            return doi

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

    # Strategie 3: Fusszeile der ersten Seite (grosse Verlage), nach Strategie
    # 2, damit ein arXiv-Preprint nicht die DOI eines zitierten Werks bekommt
    doi = extract_labeled_doi_from_text(text)
    if doi:
        return doi

    # Strategie 4+5: OpenAlex, dann CrossRef Titelsuche
    return search_doi_by_title(title, authors, year)


def search_doi_by_title(title: str, authors: str = "", year: Optional[int] = None) -> Optional[str]:
    """OpenAlex Titelsuche, dann CrossRef — ``None`` ohne Titel."""
    if not title:
        return None
    return search_openalex_for_doi(title, authors, year) or search_crossref_for_doi(title, authors)
