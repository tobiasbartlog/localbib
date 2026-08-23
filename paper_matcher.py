from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

FUZZY_THRESHOLD = 85.0


@dataclass
class MatchResult:
    matched_paper_id: int | None
    match_confidence: float
    match_strategy: Literal["doi", "fuzzy", "none"]


def match(ref: dict, conn: sqlite3.Connection) -> MatchResult:
    """Match a reference dict to a paper in the library.

    Strategy order: exact DOI → fuzzy title (rapidfuzz token_sort_ratio).
    Returns MatchResult with strategy="none" when nothing qualifies.
    """
    ref_doi = (ref.get("doi") or "").strip()
    ref_title = (ref.get("title") or "").strip()

    if ref_doi:
        norm = _normalize_doi(ref_doi)
        row = conn.execute(
            "SELECT id FROM papers WHERE LOWER(doi) = ? OR LOWER(doi) = ?",
            (norm, f"https://doi.org/{norm}"),
        ).fetchone()
        if row:
            return MatchResult(
                matched_paper_id=row["id"],
                match_confidence=1.0,
                match_strategy="doi",
            )

    if ref_title and len(ref_title) >= 10:
        try:
            from rapidfuzz import fuzz
        except ImportError:
            return MatchResult(None, 0.0, "none")

        rows = conn.execute(
            "SELECT id, title FROM papers WHERE title IS NOT NULL AND title != ''"
        ).fetchall()
        best_score = 0.0
        best_id: int | None = None
        ref_title_lower = ref_title.lower()
        for row in rows:
            lib_title = (row["title"] or "").lower()
            if not lib_title:
                continue
            score = fuzz.token_sort_ratio(ref_title_lower, lib_title)
            if score > best_score:
                best_score = score
                best_id = row["id"]

        if best_score >= FUZZY_THRESHOLD and best_id is not None:
            return MatchResult(
                matched_paper_id=best_id,
                match_confidence=round(best_score / 100.0, 3),
                match_strategy="fuzzy",
            )

    return MatchResult(None, 0.0, "none")


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if doi.startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.lower().rstrip(".")
