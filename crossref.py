#!/usr/bin/env python3
"""CrossRef API: Metadaten-Abruf anhand DOI.

``fetch_crossref_metadata(doi)`` returns the record in the library's columns:
the eight keys every caller has always read (``title``, ``authors``,
``year``, ``doi``, ``abstract``, ``journal``, ``publisher``, ``isbn``) plus
the bibliographic columns CrossRef knows (``bib_fields.BIB_COLUMNS``): Item
type, volume, issue, pages, the date as precise as given, editors, the
container as journal, book title or series (by type), place, ISSN, edition,
event, institution, thesis type, URL, language. People come in the storage
form ``"Family, Given; Family, Given"`` (``author_names.join_people``).

:func:`parse_crossref_message` is the pure half, tested with recorded
answers. The request goes through httpx with the one trust store
(``ca_trust``) and the polite User-Agent — its mailto only when the user set
one (``Config.user_agent``).
"""

import html
import re
import logging
from typing import Optional, Dict
from urllib.parse import quote

import httpx

import ca_trust
from author_names import join_people, organization
from bib_fields import normalize_date
from config import Config


ARXIV_DOI_PREFIX = "10.48550/"


def is_arxiv_doi(doi: str) -> bool:
    return (doi or "").strip().lower().startswith(ARXIV_DOI_PREFIX)


def format_crossref_authors(entries) -> str:
    """CrossRef's person list (authors, editors) in the storage form: "Family,
    Given" per person, an organization (only ``name``) by its name in braces
    — the library's mark for one (``author_names``), so "Food and Agriculture
    Organization" stays one name — joined with "; ". An entry without any
    name is dropped, so a list without one is "" — never a lone separator
    (#227)."""
    parts = []
    for entry in entries or []:
        family = (entry.get("family") or "").strip()
        given = (entry.get("given") or "").strip()
        if family and given:
            parts.append(f"{family}, {given}")
        elif family or given:
            parts.append(family or given)
        elif (entry.get("name") or "").strip():
            parts.append(organization(entry["name"]))
    return join_people(parts)


# CrossRef's ``type`` → Item type (``bib_fields.ITEM_TYPES``). A type not
# listed gives no ``item_type``: the library keeps its own guess.
CROSSREF_TYPES = {
    "journal-article": "article",
    "book": "book", "monograph": "book", "edited-book": "book", "reference-book": "book",
    "book-chapter": "chapter", "book-section": "chapter", "book-part": "chapter",
    "reference-entry": "chapter",
    "proceedings-article": "conference",
    "dissertation": "thesis",
    "report": "report", "report-component": "report",
    "posted-content": "preprint",
    "dataset": "dataset",
    "standard": "standard",
}

# The dates CrossRef gives, in the order the library has always read them
# (print before online), then the two summary dates.
_DATE_KEYS = ("published-print", "published-online", "issued", "published")

# A container title that names a book series, not the book: CrossRef lists
# both for a chapter in, e.g., "Lecture Notes in Computer Science".
_SERIES = re.compile(r"lecture notes|notes in |\bseries\b|studies in |communications in |"
                     r"springer proceedings", re.IGNORECASE)
_DOI_LINK = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


def _clean(text) -> str:
    """Markup-free text: JATS/HTML tags out, entities resolved, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _first(values) -> str:
    for value in values or []:
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _date_parts(message: dict) -> list:
    """``[year]``, ``[year, month]`` or ``[year, month, day]`` of the first
    date CrossRef fills, else ``[]``."""
    for key in _DATE_KEYS:
        parts = ((message.get(key) or {}).get("date-parts") or [[]])[0] or []
        numbers = []
        for part in parts:
            try:
                numbers.append(int(part))
            except (TypeError, ValueError):
                break
        if numbers:
            return numbers
    return []


def _pub_date(parts: list) -> str:
    """The ISO date when CrossRef knows more than the year (``bib_fields``
    keeps a bare year in ``year`` only)."""
    if len(parts) < 2:
        return ""
    return normalize_date("-".join(str(p) for p in parts[:3]))


def _abstract(message: dict) -> str:
    text = _clean(message.get("abstract") or "")
    # A JATS abstract opens with its own heading.
    return re.sub(r"^abstract\s*[:.]?\s+", "", text, flags=re.IGNORECASE)


def _issn(message: dict) -> str:
    typed = {(t.get("type") or ""): (t.get("value") or "") for t in message.get("issn-type") or []}
    return (typed.get("print") or typed.get("electronic") or _first(message.get("ISSN"))).strip()


def _containers(message: dict, item_type: str) -> dict:
    """The container titles in the right column: a chapter's or conference
    paper's book is its ``booktitle`` (a listed series its ``series``), a
    book's or report's container its ``series``, anything else's its
    ``journal`` — as before."""
    titles = [_clean(t) for t in message.get("container-title") or [] if _clean(t)]
    if not titles:
        return {}
    if item_type in ("chapter", "conference"):
        series = [t for t in titles if _SERIES.search(t)]
        books = [t for t in titles if t not in series]
        return {"booktitle": books[0] if books else titles[0],
                "series": series[0] if series and books else ""}
    if item_type in ("book", "report"):
        return {"series": titles[0]}
    return {"journal": titles[0]}


def _institution(message: dict) -> str:
    for key in ("institution", "standards-body"):
        name = _first((entry or {}).get("name") for entry in message.get(key) or [])
        if name:
            return name
    return ""


def _thesis_type(message: dict) -> str:
    degree = _first(message.get("degree"))
    if re.search(r"ph\.?\s?d|doctor|dr\.", degree, re.IGNORECASE):
        return "phdthesis"
    if re.search(r"master|m\.?\s?sc|m\.?\s?a\b", degree, re.IGNORECASE):
        return "mastersthesis"
    return degree


def parse_crossref_message(message: dict, doi: str) -> Dict:
    """One CrossRef ``message`` → the record (see the module docstring).

    The eight historic keys are always present (``year`` may be ``None``);
    the bibliographic ones only when CrossRef fills them."""
    message = message or {}
    parts = _date_parts(message)
    item_type = CROSSREF_TYPES.get((message.get("type") or "").lower(), "")
    containers = _containers(message, item_type)
    url = (message.get("URL") or "").strip()
    record = {
        "title": _clean(_first(message.get("title"))),
        "authors": format_crossref_authors(message.get("author", [])),
        "year": parts[0] if parts else None,
        "doi": doi,
        "abstract": _abstract(message),
        "journal": containers.get("journal", ""),
        "publisher": (message.get("publisher") or "").strip(),
        "isbn": ", ".join(message.get("ISBN", [])),
    }
    extra = {
        "item_type": item_type,
        "volume": str(message.get("volume") or "").strip(),
        "issue": str(message.get("issue") or "").strip(),
        "pages": str(message.get("page") or "").strip(),
        "pub_date": _pub_date(parts),
        "editors": format_crossref_authors(message.get("editor", [])),
        "booktitle": containers.get("booktitle", ""),
        "series": containers.get("series", ""),
        "edition": str(message.get("edition-number") or "").strip(),
        "place": (message.get("publisher-location") or "").strip(),
        "issn": _issn(message),
        "event": _clean((message.get("event") or {}).get("name") or ""),
        "institution": _institution(message) if item_type in ("thesis", "report", "standard") else "",
        "thesis_type": _thesis_type(message) if item_type == "thesis" else "",
        # CrossRef's URL is the DOI link itself — the doi field says that already.
        "url": "" if _DOI_LINK.match(url) else url,
        "language": (message.get("language") or "").strip(),
    }
    record.update({k: v for k, v in extra.items() if v})
    return record


def fetch_crossref_metadata(doi: str) -> Optional[Dict]:
    """Holt Metadaten von CrossRef anhand DOI (``None``: unbekannt oder Fehler)."""
    if is_arxiv_doi(doi):
        # arXiv-DOIs registriert DataCite, nicht CrossRef: die Anfrage endet
        # immer mit 404 und fuellt nur das Log.
        return None
    try:
        url = f"{Config.CROSSREF_API_URL}{quote(doi.strip(), safe='/')}"
        headers = {"User-Agent": Config.user_agent()}
        response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True,
                             verify=ca_trust.httpx_verify())

        if response.status_code != 200:
            logging.warning(f"⚠️  CrossRef: Status {response.status_code} für DOI {doi}")
            return None

        return parse_crossref_message(response.json().get("message", {}), doi)

    except Exception as e:
        logging.error(f"❌ CrossRef-Fehler: {e}")
        return None
