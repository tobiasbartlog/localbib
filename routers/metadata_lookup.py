"""Router: complete one Item's bibliographic data by DOI or ISBN.

Serves:
  POST /api/papers/{paper_id}/metadata/lookup

Asks the Item's DOI (CrossRef, else DataCite) or ISBN (Open Library) through
the one seam ``bib_lookup`` and compares the record with the Item. **Writes
nothing**: the answer is ``{fills, conflicts, sources, found, source_status}``
(``source_status``: per source found / not_found / not_asked /
no_identifier, the SPA's "Sources of this lookup") — ``fills``
are the fields the Item has empty (the SPA takes them over at once with
``PUT /api/papers/{id}``, user decision), ``conflicts`` the filled fields the
record names differently (``{field, current, found, source}``, offered for
review, never applied here). A stored ``year`` is never filled over; a
differing one is a conflict. Works without a PDF.

The body may name the identifiers to ask (``{doi, isbn}``) — a DOI typed into
the form but not saved yet; without one the stored values are used.

Its own module because ``routers/papers.py`` holds the Item's CRUD and stays
within the line budget. No import from ``webapp`` or any other router.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import bib_lookup
from context import get_conn

router = APIRouter()


class LookupRequest(BaseModel):
    doi: Optional[str] = None
    isbn: Optional[str] = None


def _identifier(given: Optional[str], stored) -> str:
    return (given if given is not None else (stored or "")).strip()


@router.post("/api/papers/{paper_id}/metadata/lookup")
async def lookup_metadata(paper_id: int, data: Optional[LookupRequest] = None):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="error.item_not_found")
    paper = dict(row)
    data = data or LookupRequest()
    doi = _identifier(data.doi, paper.get("doi"))
    isbn = _identifier(data.isbn, paper.get("isbn"))
    if not (doi or isbn):
        raise HTTPException(status_code=422, detail="error.lookup_no_identifier")

    record = await run_in_threadpool(bib_lookup.fetch, doi=doi, isbn=isbn)
    result = bib_lookup.compare(paper, record["fields"], record["sources"])
    return {"paper_id": paper_id, "found": bool(record["fields"]),
            "source_status": record.get("status", {}), **result}
