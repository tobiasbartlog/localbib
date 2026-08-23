#!/usr/bin/env python3
"""LLM-Kategorisierung über RWTH-GPT / KI Connect NRW."""

import logging
from typing import List, Dict

from config import Config
from llm_client import llm_for


def categorize_with_llm(title: str, abstract: str, text_snippet: str,
                        category_tree: str, categories_json: List[Dict]) -> List[Dict]:
    """
    Kategorisiert Paper über RWTH-GPT (KI Connect NRW API).
    Gibt Liste von {category_id, confidence} zurück.
    """
    if not Config.KICONNECT_API_KEY:
        logging.warning("⚠️  Kein KICONNECT_API_KEY gesetzt. Überspringe LLM-Kategorisierung.")
        return []

    # Kategorie-Info für Prompt aufbereiten
    cat_info = []
    for c in categories_json:
        info = f"ID={c['id']}: {c['name']}"
        if c.get("description"):
            info += f" ({c['description']})"
        if c.get("keywords"):
            info += f" [Keywords: {c['keywords']}]"
        cat_info.append(info)

    prompt = f"""Du bist ein Experte für die Klassifikation wissenschaftlicher Paper.

Ordne das folgende Paper den passenden Kategorien zu.
Gib NUR Kategorien zurück, die wirklich passen (mindestens 1, maximal 5).

VERFÜGBARE KATEGORIEN:
{chr(10).join(cat_info)}

KATEGORIE-BAUM:
{category_tree}

PAPER:
Titel: {title}
Abstract: {abstract[:1500] if abstract else 'Nicht verfügbar'}
Text-Auszug: {text_snippet[:1000] if text_snippet else 'Nicht verfügbar'}

ANTWORT-FORMAT (NUR JSON, kein anderer Text):
[{{"category_id": <id>, "confidence": <0.0-1.0>}}]

Antworte NUR mit dem JSON-Array, nichts anderes."""

    assignments = llm_for("categorize").complete_json(
        [{"role": "user", "content": prompt}], expect=list, default=[], timeout=60
    )
    if assignments:
        logging.info(f"🏷️  LLM hat {len(assignments)} Kategorien zugewiesen")
    return assignments
