"""Router: library stats.

Serves GET /api/stats — Item count, category count, pending imports, year
range and DOI coverage. With ``collection_id`` (#202) the same figures cover
only that Collection's Items, plus the breakdowns a Collection page shows
(PDF coverage, years, venues, categories); an unknown or tombstoned
Collection is a 404 ``error.collection_not_found``. No I/O at import time.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from context import get_conn
from literature_manager import Config, Database
from services import library_collections

router = APIRouter()

_TOP = 10


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _top(counter: Counter) -> list[dict]:
    return [{"name": k, "count": n} for k, n in
            sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]).lower()))[:_TOP]]


def _collection_stats(collection_id: int) -> dict:
    conn = get_conn()
    try:
        collection = library_collections.get_collection(conn, collection_id)
        if not collection:
            raise HTTPException(status_code=404, detail="error.collection_not_found")
        ids = set(library_collections.member_ids(conn, collection_id))
        cats = Counter()
        for r in conn.execute(
            "SELECT pc.paper_id, c.name FROM paper_categories pc "
            "JOIN categories c ON c.id = pc.category_id"
        ).fetchall():
            if r["paper_id"] in ids:
                cats[r["name"]] += 1
    finally:
        conn.close()
    papers = [p for p in _get_db().get_all_papers() if p["id"] in ids]
    years = [p["year"] for p in papers if p.get("year")]
    venues = Counter((p.get("journal") or "").strip() for p in papers)
    venues.pop("", None)
    return {
        "scope": {"type": "collection", "id": collection["id"], "name": collection["name"]},
        "paper_count": len(papers),
        "category_count": len(cats),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "with_doi": sum(1 for p in papers if p.get("doi")),
        "with_pdf": sum(1 for p in papers if p.get("filename")),
        "by_year": [{"year": y, "count": n} for y, n in sorted(Counter(years).items())],
        "top_venues": _top(venues),
        "top_categories": _top(cats),
    }


@router.get("/api/stats")
async def get_stats(collection_id: Optional[int] = Query(None)):
    if collection_id is not None:
        return _collection_stats(collection_id)

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
