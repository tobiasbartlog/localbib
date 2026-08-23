"""Service: duplicate detection.

Pure functions — no DB writes, no logging config, no filesystem ops.
Duplicate grouping logic extracted from ``webapp.py`` (Backend-Modularisierung
#91).  The router is responsible for loading papers from the DB and computing
the ``has_file`` flag before passing the list here.

API
---
normalize_title(title: str) -> str
    NFKD unicode decomposition → lowercase → strip →
    remove non-word / non-space chars → collapse whitespace.

find_duplicate_groups(papers: list[dict]) -> list[dict]
    Three-strategy duplicate detection over a pre-loaded paper list.
    Returns a list of group dicts ``{"reason": str, "papers": list[dict]}``.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_title(title: str) -> str:
    """Normalisiert einen Titel fuer den Vergleich.

    NFKD unicode decomposition → lowercase → strip →
    remove non-word / non-space chars → collapse whitespace.
    """
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title)
    t = t.lower().strip()
    # Satzzeichen und Sonderzeichen entfernen
    t = re.sub(r"[^\w\s]", "", t)
    # Mehrfach-Leerzeichen
    t = re.sub(r"\s+", " ", t)
    return t


def find_duplicate_groups(papers: list[dict]) -> list[dict]:
    """Findet potentielle Duplikate in der uebergebenen Papier-Liste.

    Strategien:
    1. Gleiche DOI
    2. Gleicher Titel (normalisiert)
    3. Aehnlicher Titel (Substring oder Wort-Jaccard >= 0.8)

    Parameters
    ----------
    papers:
        List of paper dicts, each with at least the keys
        ``id``, ``title``, ``doi``.  The ``has_file`` flag should be
        pre-computed by the caller (router) before passing the list.

    Returns
    -------
    list[dict]
        Each entry: ``{"reason": str, "papers": list[dict]}``.
    """
    groups: list[dict] = []

    # --- Strategie 1: Gleiche DOI ---
    doi_map: dict[str, list] = {}
    for p in papers:
        doi = (p.get("doi") or "").strip().lower()
        if doi:
            doi_map.setdefault(doi, []).append(p)

    for doi, group in doi_map.items():
        if len(group) >= 2:
            ids = frozenset(p["id"] for p in group)
            groups.append({
                "reason": "Gleiche DOI: " + doi,
                "papers": group,
                "_ids": ids,  # internal — stripped before return
            })

    # --- Strategie 2: Gleicher normalisierter Titel ---
    title_map: dict[str, list] = {}
    for p in papers:
        nt = normalize_title(p.get("title", ""))
        if nt and len(nt) > 10:  # Zu kurze Titel ignorieren
            title_map.setdefault(nt, []).append(p)

    for nt, group in title_map.items():
        if len(group) >= 2:
            ids = frozenset(p["id"] for p in group)
            # Pruefen ob diese Gruppe nicht schon via DOI gefunden wurde
            already = any(g["_ids"] == ids for g in groups)
            if not already:
                groups.append({
                    "reason": "Gleicher Titel",
                    "papers": group,
                    "_ids": ids,
                })

    # --- Strategie 3: Aehnlicher Titel (einfacher Vergleich) ---
    norm_papers = [(p, normalize_title(p.get("title", ""))) for p in papers]
    for i in range(len(norm_papers)):
        p1, t1 = norm_papers[i]
        if not t1 or len(t1) < 15:
            continue
        for j in range(i + 1, len(norm_papers)):
            p2, t2 = norm_papers[j]
            if not t2 or len(t2) < 15:
                continue
            # Bereits in einer Gruppe?
            pair = frozenset([p1["id"], p2["id"]])
            already_grouped = any(pair.issubset(g["_ids"]) for g in groups)
            if already_grouped:
                continue
            # Aehnlichkeit: Einer ist Substring des anderen oder Wort-Ueberlappung
            if t1 in t2 or t2 in t1:
                groups.append({
                    "reason": "Titel enthalten",
                    "papers": [p1, p2],
                    "_ids": pair,
                })
                continue
            # Wort-Jaccard-Aehnlichkeit
            words1 = set(t1.split())
            words2 = set(t2.split())
            if len(words1) < 3 or len(words2) < 3:
                continue
            intersection = words1 & words2
            union = words1 | words2
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard >= 0.8:
                groups.append({
                    "reason": f"Aehnlicher Titel ({int(jaccard * 100)}%)",
                    "papers": [p1, p2],
                    "_ids": pair,
                })

    # Strip internal tracking key before returning
    for g in groups:
        g.pop("_ids", None)

    return groups
