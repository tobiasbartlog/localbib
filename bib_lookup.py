"""Bibliographic lookup by DOI or ISBN — the one seam.

Every path that completes an Item's bibliographic data from an outside
source asks here: the DOI intake and the BibTeX import (``paper_ingest``), the
PDF pipeline (``pipeline``), the single-Item lookup
(``POST /api/papers/{id}/metadata/lookup``) and the Refresh Review scope
``bibliographic``.

* :func:`fetch` — ``fetch(doi=…, isbn=…)`` → ``{"fields": {…}, "sources":
  {field: source}, "status": {source: found|not_found|not_asked|no_identifier}}``.
  A DOI goes to CrossRef, then to DataCite (arXiv, Zenodo …); without a DOI
  hit an ISBN goes to Open Library. One source per call, so every field
  names the source it came from. Never raises.
* :func:`compare` — the found record against the Item: ``fills`` (fields the
  Item has empty) and ``conflicts`` (fields it has with a different value).
  The abstract is fill-only (:data:`FILL_ONLY`): never a conflict.
  Nothing is ever overwritten here; the caller writes fills (user decision:
  empty fields fill automatically) and offers conflicts for review. ``year``
  is never filled against a stored year of its own: a differing year is a
  conflict, and a date of another year is dropped (the two never disagree).

The network sits behind the three clients (``crossref``, ``datacite``,
``openlibrary_client``); this module adds only the order and the rules. The
fetchers are injectable so a caller's own patch target (``paper_ingest``,
``pipeline`` …) keeps working, and a test runs the rules offline.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

import crossref
import datacite
import openlibrary_client
from author_names import normalize_people
from bib_fields import (BIB_COLUMNS, DATE_FIELDS, ITEM_TYPES, WRITABLE_FIELDS, is_empty,
                        normalize_date, year_of)
from bib_fields import year_value as _year

Fetcher = Callable[[str], Optional[Dict[str, Any]]]

SOURCE_CROSSREF = "crossref"
SOURCE_DATACITE = "datacite"
SOURCE_OPENLIBRARY = "openlibrary"

STATUS_FOUND = "found"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_ASKED = "not_asked"
STATUS_NO_IDENTIFIER = "no_identifier"

ABSTRACT_MAX = 5000

# Fields a lookup only ever fills: an abstract the Item has is never set
# against the source's (maintainer decision) — it is no conflict either.
FILL_ONLY = ("abstract",)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _clean(record: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The writable, filled fields of a client's record; ``year`` as int.
    Never an invalid value: a date the write rules would refuse
    (``bib_fields.normalize_date``) and an unknown ``item_type`` are dropped,
    a date is kept in its ISO form, and a date of another year than the
    record's own ``year`` is dropped (the two never disagree)."""
    out: Dict[str, Any] = {}
    for field, value in (record or {}).items():
        if field not in WRITABLE_FIELDS or is_empty(field, value):
            continue
        if field == "year":
            year = _year(value)
            if year:
                out["year"] = year
            continue
        value = _text(value)
        if field in DATE_FIELDS:
            value = normalize_date(value)
            if not value:
                continue
        if field == "item_type" and value not in ITEM_TYPES:
            continue
        out[field] = value[:ABSTRACT_MAX] if field == "abstract" else value
    if out.get("pub_date") and out.get("year") and year_of(out["pub_date"]) != out["year"]:
        del out["pub_date"]
    return out


def fetch(doi: str = "", isbn: str = "", *,
          crossref_fetch: Optional[Fetcher] = None,
          datacite_fetch: Optional[Fetcher] = None,
          openlibrary_fetch: Optional[Fetcher] = None) -> Dict[str, Dict[str, Any]]:
    """The record of ``doi`` (CrossRef, else DataCite) or, without a DOI
    hit, of ``isbn`` (Open Library) — ``{"fields": {}, "sources": {}}`` when
    nothing is known. The default fetchers are read at call time, so a patch
    on the client module reaches them."""
    doi, isbn = _text(doi), _text(isbn)
    steps: List[tuple] = []
    if doi:
        steps.append((SOURCE_CROSSREF, crossref_fetch or crossref.fetch_crossref_metadata, doi))
        steps.append((SOURCE_DATACITE, datacite_fetch or datacite.fetch_datacite_metadata, doi))
    if isbn:
        steps.append((SOURCE_OPENLIBRARY,
                      openlibrary_fetch or openlibrary_client.fetch_openlibrary_metadata, isbn))
    # What each source said (the SPA's "Sources of this lookup"): found,
    # not_found, not_asked (an earlier one answered) or no_identifier.
    status = {SOURCE_CROSSREF: STATUS_NOT_ASKED if doi else STATUS_NO_IDENTIFIER,
              SOURCE_DATACITE: STATUS_NOT_ASKED if doi else STATUS_NO_IDENTIFIER,
              SOURCE_OPENLIBRARY: STATUS_NOT_ASKED if isbn else STATUS_NO_IDENTIFIER}
    for source, fetcher, key in steps:
        try:
            fields = _clean(fetcher(key))
        except Exception:  # a client never raises; a patched one might
            fields = {}
        if fields:
            status[source] = STATUS_FOUND
            return {"fields": fields, "sources": {f: source for f in fields}, "status": status}
        status[source] = STATUS_NOT_FOUND
    return {"fields": {}, "sources": {}, "status": status}


# ---------------------------------------------------------------------------
# Found record vs. Item
# ---------------------------------------------------------------------------

def _dashes(text: str) -> str:
    return re.sub(r"\s*(?:--|[‐-―−-])\s*", "-", text)


def _key(field: str, value: Any) -> Any:
    """The comparable form of a value: same work, same key."""
    if field == "year":
        return _year(value)
    text = re.sub(r"\s+", " ", _text(value))
    if field in ("authors", "editors"):
        return normalize_people(text).casefold()
    if field == "doi":
        return re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE).casefold()
    if field in ("isbn", "issn"):
        return frozenset(re.sub(r"[^0-9X]", "", p.upper()) for p in re.split(r"[,;\s]+", text) if p)
    if field == "pages":
        return _dashes(text)
    if field == "url":
        return text.rstrip("/")
    return text.casefold().rstrip(" .")


def same(field: str, stored: Any, found: Any) -> bool:
    """Whether a stored and a found value say the same (case, spacing, dash
    style, people separators, ISBN hyphens aside; one shared ISBN/ISSN of
    several is the same book or journal)."""
    a, b = _key(field, stored), _key(field, found)
    if isinstance(a, frozenset):
        return bool(a & b)
    return a == b


def compare(paper: Mapping[str, Any], found: Mapping[str, Any],
            sources: Optional[Mapping[str, str]] = None,
            fields: Iterable[str] = WRITABLE_FIELDS) -> Dict[str, Any]:
    """``{"fills": {field: value}, "conflicts": [{field, current, found,
    source}], "sources": {field: source}}`` for one Item.

    A field the Item has empty is a fill, one it has with another value a
    conflict, an equal one nothing. The date follows the year: a found
    ``pub_date`` of another year than the stored one is dropped — the year
    conflict carries the disagreement."""
    sources = dict(sources or {})
    fills: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []
    stored_year = _year(paper.get("year"))
    for field in fields:
        if field not in found or is_empty(field, found[field]):
            continue
        value = found[field]
        if field in DATE_FIELDS:
            value = normalize_date(value)   # never an invalid fill
            if not value:
                continue
        if field == "item_type" and _text(value) not in ITEM_TYPES:
            continue
        if field == "pub_date":
            found_year = year_of(value)
            if stored_year is not None and found_year != stored_year:
                continue
        current = paper.get(field)
        if is_empty(field, current):
            fills[field] = value
        elif field in FILL_ONLY:
            continue
        elif not same(field, current, value):
            conflicts.append({"field": field, "current": current, "found": value,
                              "source": sources.get(field, "")})
    if "pub_date" in fills and stored_year is None and "year" not in fills:
        fills["year"] = year_of(fills["pub_date"])  # the year follows the date
    used = set(fills) | {c["field"] for c in conflicts}
    return {"fills": fills, "conflicts": conflicts,
            "sources": {f: s for f, s in sources.items() if f in used}}


def bib_columns(record: Mapping[str, Any]) -> Dict[str, str]:
    """The bibliographic columns (``bib_fields.BIB_COLUMNS``) a record fills
    — what an intake passes to ``Database.add_paper`` beside the core fields."""
    return {c: _text(record.get(c)) for c in BIB_COLUMNS if _text(record.get(c))}
