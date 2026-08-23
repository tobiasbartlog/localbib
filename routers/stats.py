"""Router: library stats.

Serves GET /api/stats — paper count, category count, pending imports, year
range and DOI coverage.  No I/O at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from literature_manager import Config, Database

router = APIRouter()


def _get_db() -> Database:
    return Database(Config.DB_PATH)


@router.get("/api/stats")
async def get_stats():
    db = _get_db()
    papers = db.get_all_papers()
    categories = db.get_categories()

    # Pending imports zaehlen
    pending_count = 0
    if os.path.exists(Config.INPUT_DIR):
        pending_count = len(list(Path(Config.INPUT_DIR).glob("*.pdf")))

    stats = {
        "paper_count": len(papers),
        "category_count": len(categories),
        "pending_imports": pending_count,
        "year_min": None,
        "year_max": None,
        "with_doi": 0,
    }

    if papers:
        years = [p["year"] for p in papers if p["year"]]
        if years:
            stats["year_min"] = min(years)
            stats["year_max"] = max(years)
        stats["with_doi"] = sum(1 for p in papers if p["doi"])

    return stats
