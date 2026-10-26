"""DataCite REST API: metadata for a DOI CrossRef does not know.

arXiv (``10.48550/``), Zenodo, figshare, institutional repositories and most
data and software DOIs are registered with DataCite, not CrossRef — before
this module the library had nothing for them. ``fetch_datacite_metadata(doi)``
asks ``https://api.datacite.org/dois/{doi}`` (no key) and returns the record
in the library's columns, the same shape as ``crossref.fetch_crossref_metadata``:
title, authors, editors, year (and the date when more precise), publisher,
abstract, Item type (dataset, software, preprint …), version, landing URL,
language and, for arXiv, the arXiv id as ``eprint``.

:func:`parse_datacite` is the pure half, tested with recorded answers. The
request goes through httpx with the one trust store (``ca_trust``) and the
polite User-Agent — its mailto only when the user set one.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

import ca_trust
from author_names import is_organization, join_people, organization
from bib_fields import normalize_date, year_of
from config import Config

BASE_URL = "https://api.datacite.org/dois/"

# ``types.resourceTypeGeneral`` → Item type. ``Text`` says nothing on its own
# (an arXiv record is ``Text``/``Article`` in older answers): no type then,
# unless the DOI is an arXiv one.
DATACITE_TYPES = {
    "dataset": "dataset", "software": "software", "computationalnotebook": "software",
    "preprint": "preprint", "journalarticle": "article", "book": "book",
    "bookchapter": "chapter", "conferencepaper": "conference",
    "dissertation": "thesis", "report": "report", "standard": "standard",
}

_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _people(entries) -> str:
    """DataCite creators/contributors in the storage form: "Family, Given"
    for a person, an organization (``nameType: Organizational``, or a
    untyped ``name`` that reads as one) by its name in braces — the
    library's mark for one (``author_names``), so a separator in it splits
    nobody."""
    out = []
    for entry in entries or []:
        family, given = _text(entry.get("familyName")), _text(entry.get("givenName"))
        name = _text(entry.get("name"))
        kind = _text(entry.get("nameType")).lower()
        if kind == "organizational" and name:
            out.append(organization(name))
        elif family and given:
            out.append(f"{family}, {given}")
        elif name:
            out.append(organization(name) if kind != "personal" and is_organization(name) else name)
        elif family or given:
            out.append(family or given)
    return join_people(out)


def _title(titles) -> str:
    """The main title (no ``titleType``), else the first one."""
    titles = [t for t in titles or [] if _text(t.get("title"))]
    main = [t for t in titles if not t.get("titleType")]
    return _text((main or titles)[0]["title"]) if titles else ""


def _publisher(value) -> str:
    # A plain string, or an object since the 2024 schema (``?publisher=true``).
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def _abstract(descriptions) -> str:
    for entry in descriptions or []:
        if (entry.get("descriptionType") or "").lower() == "abstract" and _text(entry.get("description")):
            return _text(re.sub(r"<[^>]+>", " ", entry["description"]))
    return ""


def _pub_date(dates, year: Optional[int]) -> str:
    """The ``Issued`` (else ``Available``) date when more precise than the
    year and of the same year — the two never disagree."""
    by_type = {(d.get("dateType") or "").lower(): d.get("date") for d in dates or []}
    for kind in ("issued", "available"):
        value = normalize_date(by_type.get(kind))
        if len(value) > 4 and (year is None or year_of(value) == year):
            return value
    return ""


def _arxiv_id(doi: str, identifiers) -> str:
    for entry in identifiers or []:
        if (entry.get("identifierType") or "").lower() == "arxiv" and _text(entry.get("identifier")):
            return re.sub(r"^arxiv:", "", _text(entry["identifier"]), flags=re.IGNORECASE)
    match = _ARXIV_DOI.match(doi or "")
    return match.group(1) if match else ""


def parse_datacite(data: dict, doi: str) -> Dict[str, Any]:
    """One DataCite ``data`` object (JSON:API) → the record; only filled keys."""
    attrs = (data or {}).get("attributes") or {}
    doi = _text(attrs.get("doi")) or doi
    try:
        year = int(attrs.get("publicationYear")) if attrs.get("publicationYear") else None
    except (TypeError, ValueError):
        year = None
    general = _text((attrs.get("types") or {}).get("resourceTypeGeneral")).lower()
    eprint = _arxiv_id(doi, attrs.get("identifiers"))
    item_type = DATACITE_TYPES.get(general, "")
    if not item_type and eprint:
        item_type = "preprint"
    editors = [c for c in attrs.get("contributors") or []
               if (c.get("contributorType") or "").lower() == "editor"]
    container = attrs.get("container") or {}
    record = {
        "title": _title(attrs.get("titles")),
        "authors": _people(attrs.get("creators")),
        "editors": _people(editors),
        "year": year,
        "pub_date": _pub_date(attrs.get("dates"), year),
        "doi": doi,
        "publisher": _publisher(attrs.get("publisher")),
        "abstract": _abstract(attrs.get("descriptions")),
        "item_type": item_type,
        "journal": _text(container.get("title")) if item_type == "article" else "",
        "version": _text(attrs.get("version")),
        "url": _text(attrs.get("url")),
        "language": _text(attrs.get("language")),
        "eprint": eprint,
    }
    return {k: v for k, v in record.items() if v}


def fetch_datacite_metadata(doi: str) -> Optional[Dict[str, Any]]:
    """The DataCite record of ``doi`` or ``None`` (unknown, unreachable) —
    never raises."""
    doi = (doi or "").strip()
    if not doi:
        return None
    try:
        response = httpx.get(
            f"{BASE_URL}{quote(doi, safe='/')}",
            headers={"User-Agent": Config.user_agent("LocalBib"),
                     "Accept": "application/vnd.api+json"},
            timeout=10, follow_redirects=True, verify=ca_trust.httpx_verify(),
        )
        if response.status_code != 200:
            if response.status_code != 404:
                logging.warning("DataCite: Status %s fuer DOI %s", response.status_code, doi)
            return None
        return parse_datacite(response.json().get("data") or {}, doi) or None
    except Exception as e:
        logging.warning("DataCite-Abruf fuer %s fehlgeschlagen: %s", doi, e)
        return None
