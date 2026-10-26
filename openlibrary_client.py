"""Open Library Books API: metadata for a book by its ISBN.

Most books have no DOI, so neither CrossRef nor DataCite knows them.
``fetch_openlibrary_metadata(isbn)`` asks
``https://openlibrary.org/api/books?bibkeys=ISBN:…&jscmd=data&format=json``
(no key) and returns the record in the library's columns: title (with its
subtitle), authors, publisher, place, year (and the date when more
precise), edition, series, ISBN — Item type ``book``.

:func:`parse_openlibrary` is the pure half, tested with recorded answers.
The request goes through httpx with the one trust store (``ca_trust``) and
the polite User-Agent — its mailto only when the user set one.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import httpx

import ca_trust
from author_names import join_people, organization, parse_name
from bib_fields import month_number, normalize_date
from config import Config

BASE_URL = "https://openlibrary.org/api/books"

_ISBN_CHARS = re.compile(r"[^0-9Xx]")


def normalize_isbn(isbn: str) -> str:
    """The first ISBN-10/13 in ``isbn`` (a list may be comma separated),
    digits and ``X`` only, else ``""``."""
    for part in re.split(r"[,;/]|\s{2,}", str(isbn or "")):
        digits = _ISBN_CHARS.sub("", part).upper()
        if len(digits) in (10, 13):
            return digits
    return ""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _names(entries) -> list:
    out = []
    for entry in entries or []:
        name = _text(entry.get("name") if isinstance(entry, dict) else entry)
        if name:
            out.append(name)
    return out


def _person(name: str) -> str:
    """"Given Family" (Open Library's form) → "Family, Given"; a single word
    stays as it is; an organization (``author_names.is_organization``) in
    braces, the library's mark for one."""
    parsed = parse_name(name)
    if parsed.org:
        return organization(parsed.last or name)
    if not parsed.given:
        return parsed.last or name
    return f"{parsed.last}, {parsed.given}"


def _date(text: str) -> tuple:
    """``publish_date`` ("2009", "July 2009", "Sep 15, 2009", "2009-07-31")
    → ``(year, pub_date)``; the date only when more precise than the year.
    A month counts only as a whole month name or abbreviation ("Marine" is
    no March), and the date goes through ``bib_fields.normalize_date`` — an
    impossible day ("Feb 30") falls back to the month, never to a date the
    write rules would refuse."""
    text = _text(text)
    match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", text)
    if not match:
        return None, ""
    year = int(match.group(1))
    iso = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", text)
    if iso:
        month, day = int(iso.group(2)), int(iso.group(3) or 0)
    else:
        words = re.findall(r"[A-Za-zÄÖÜäöü]+\.?", text)
        month = next((m for m in (month_number(w) for w in words) if m), None) or 0
        day_match = re.search(r"\b([0-3]?\d)\b(?!\d)", text.replace(match.group(1), ""))
        day = int(day_match.group(1)) if day_match and month else 0
    if not 1 <= month <= 12:
        return year, ""
    if day:
        full = normalize_date(f"{year:04d}-{month:02d}-{day:02d}")
        if full:
            return year, full
    return year, normalize_date(f"{year:04d}-{month:02d}")


def _edition(text: str) -> str:
    """"3rd ed.", "2. Auflage" → "3", "2" (biblatex wants the number); a
    named edition ("Revised edition") stays as written."""
    text = _text(text)
    match = re.match(r"^(\d+)\s*(?:st|nd|rd|th|\.)?\s*(?:ed|edition|aufl|auflage)\b", text, re.IGNORECASE)
    return match.group(1) if match else text


def parse_openlibrary(book: dict, isbn: str) -> Dict[str, Any]:
    """One Open Library ``jscmd=data`` book → the record; only filled keys."""
    book = book or {}
    title = _text(book.get("title"))
    subtitle = _text(book.get("subtitle"))
    if title and subtitle:
        title = f"{title}: {subtitle}"
    identifiers = book.get("identifiers") or {}
    found_isbn = _text((identifiers.get("isbn_13") or identifiers.get("isbn_10") or [isbn])[0])
    year, pub_date = _date(book.get("publish_date"))
    record = {
        "item_type": "book",
        "title": title,
        "authors": join_people(_person(n) for n in _names(book.get("authors"))),
        "publisher": (_names(book.get("publishers")) or [""])[0],
        "place": (_names(book.get("publish_places")) or [""])[0],
        "year": year,
        "pub_date": pub_date,
        "edition": _edition(book.get("edition_name")),
        "series": (_names(book.get("series")) or [""])[0],
        "isbn": found_isbn,
    }
    return {k: v for k, v in record.items() if v}


def fetch_openlibrary_metadata(isbn: str) -> Optional[Dict[str, Any]]:
    """The Open Library record of ``isbn`` or ``None`` (unknown, unreachable,
    no valid ISBN) — never raises."""
    key = normalize_isbn(isbn)
    if not key:
        return None
    try:
        response = httpx.get(
            BASE_URL,
            params={"bibkeys": f"ISBN:{key}", "jscmd": "data", "format": "json"},
            headers={"User-Agent": Config.user_agent("LocalBib")},
            timeout=10, follow_redirects=True, verify=ca_trust.httpx_verify(),
        )
        if response.status_code != 200:
            logging.warning("Open Library: Status %s fuer ISBN %s", response.status_code, key)
            return None
        book = (response.json() or {}).get(f"ISBN:{key}")
        return parse_openlibrary(book, key) if book else None
    except Exception as e:
        logging.warning("Open-Library-Abruf fuer ISBN %s fehlgeschlagen: %s", key, e)
        return None
