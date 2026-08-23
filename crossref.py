#!/usr/bin/env python3
"""CrossRef API: Metadaten-Abruf anhand DOI."""

import re
import logging
from typing import Optional, Dict

import requests

from config import Config


def fetch_crossref_metadata(doi: str) -> Optional[Dict]:
    """Holt Metadaten von CrossRef anhand DOI."""
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
        authors_list = message.get("author", [])
        authors = ", ".join(
            f"{a.get('family', '')}, {a.get('given', '')}" for a in authors_list
        )

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
