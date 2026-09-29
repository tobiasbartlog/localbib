#!/usr/bin/env python3
"""LLM-Kategorisierung über RWTH-GPT / KI Connect NRW."""

import logging
import math
from typing import List, Dict

from config import Config
from llm_client import llm_for


def categorize_with_llm(title: str, abstract: str, text_snippet: str,
                        category_tree: str, categories_json: List[Dict],
                        llm=None) -> List[Dict]:
    """
    Kategorisiert Paper über RWTH-GPT (KI Connect NRW API).
    Gibt Liste von {category_id, confidence} zurück.

    ``llm`` is an optional pre-built client (``llm_for("categorize")``): a
    caller that degrades on failure passes its own so it can read
    ``client.last_error`` afterwards and tell the user why nothing was
    assigned. Without it the client is built here as before.
    """
    if not Config.llm_ready("fast"):
        logging.warning("⚠️  Fast-Rolle nicht gebunden (Einstellungen -> LLM). Überspringe LLM-Kategorisierung.")
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

    client = llm if llm is not None else llm_for("categorize")
    raw = client.complete_json(
        [{"role": "user", "content": prompt}], expect=list, default=[], timeout=60
    )
    assignments = valid_assignments(raw, categories_json)
    if len(assignments) < len(raw):
        logging.warning(f"⚠️  LLM-Kategorien verworfen: {len(raw) - len(assignments)} ungültige Einträge")
    if assignments:
        logging.info(f"🏷️  LLM hat {len(assignments)} Kategorien zugewiesen")
    return assignments


def valid_assignments(raw: list, categories_json: List[Dict]) -> List[Dict]:
    """Keep only the assignments that name an existing category.

    The LLM's answer is untrusted: an invented id violates the
    ``paper_categories`` foreign key and used to abort an import halfway
    (row written, file not yet copied). Every caller writes what this returns
    straight into the DB, so the check lives here, once."""
    known = {c["id"] for c in categories_json}
    seen: set = set()
    out: List[Dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            cat_id = int(item.get("category_id"))
        except (TypeError, ValueError):
            continue
        if cat_id not in known or cat_id in seen:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        seen.add(cat_id)
        out.append({"category_id": cat_id, "confidence": min(max(confidence, 0.0), 1.0)})
    return out
