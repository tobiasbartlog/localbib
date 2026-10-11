#!/usr/bin/env python3
"""CrossRef API: Metadaten-Abruf anhand DOI."""

import re
import logging
from typing import Optional, Dict

import requests

from config import Config


ARXIV_DOI_PREFIX = "10.48550/"


def is_arxiv_doi(doi: str) -> bool:
    return (doi or "").strip().lower().startswith(ARXIV_DOI_PREFIX)


def format_crossref_authors(entries) -> str:
    """CrossRef's author list as the library stores it: "Family, Given" per
    person, joined with ", " (unchanged), an organization (only ``name``) by
    its name. An entry without any name is dropped, so a list without one is
    "" — never a lone separator (#227)."""
    parts = []
    for entry in entries or []:
        family = (entry.get("family") or "").strip()
        given = (entry.get("given") or "").strip()
        if family and given:
            parts.append(f"{family}, {given}")
        elif family or given:
            parts.append(family or given)
        elif (entry.get("name") or "").strip():
            parts.append(entry["name"].strip())
    return ", ".join(parts)


def fetch_crossref_metadata(doi: str) -> Optional[Dict]:
    """Holt Metadaten von CrossRef anhand DOI."""
    if is_arxiv_doi(doi):
        # arXiv-DOIs registriert DataCite, nicht CrossRef: die Anfrage endet
        # immer mit 404 und fuellt nur das Log.
        return None
    try:
        url = f"{Config.CROSSREF_API_URL}{doi}"
        headers = {"User-Agent": Config.user_agent()}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logging.warning(f"⚠️  CrossRef: Status {response.status_code} für DOI {doi}")
            return None

        data = response.json()
        message = data.get("message", {})

        # Autoren extrahieren
        authors = format_crossref_authors(message.get("author", []))

        # Jahr extrahieren
        date_parts = message.get("published-print", message.get("published-online", {}))
        year = None
        if date_parts and "date-parts" in date_parts:
            parts = date_parts["date-parts"]
            if parts and len(parts[0]) > 0:
                year = parts[0][0]

        # Titel
        title_list = message.get("title", [])
        title = title_list[0] if title_list else ""

        # Abstract
        abstract = message.get("abstract", "")
        # HTML-Tags entfernen
        abstract = re.sub(r'<[^>]+>', '', abstract)

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "abstract": abstract,
            "journal": message.get("container-title", [""])[0] if message.get("container-title") else "",
            "publisher": message.get("publisher", ""),
            "isbn": ", ".join(message.get("ISBN", [])),
        }

    except Exception as e:
        logging.error(f"❌ CrossRef-Fehler: {e}")
        return None
