"""Router: create library papers from a DOI plus metadata (ADR-0016).

Serves:
  POST /api/papers/by-doi          one paper
  POST /api/papers/by-doi/batch    a list, one result per item
  POST /api/papers/by-doi/exists   DOIs -> paper id or null

The second path into the library that needs no PDF (the BibTeX import was
the first, ADR-0003) and the one a plugin's views call from the SPA (PRD
#137 Teil B). An Add-on's *Python* reaches the same intake through
``LibraryApi.create_by_doi`` (``library.write``); both transports call
``paper_ingest.intake_by_doi``, so the flow exists once. This handler is the
write point of the REST transport (ADR-0006: only routers persist).

Contract of a creation: dedup by DOI (an existing paper answers with its id
and ``created=false``, nothing is touched), ``add_paper`` with the origin
marked (``import_source``), then the same non-destructive enrichment as the
BibTeX commit (CrossRef/OpenAlex, only empty fields) and a **best-effort**
Open-Access PDF: ``pdf`` is ``fetched`` or ``none``, and a failed OA attempt
never undoes the creation. Handlers are plain ``def`` on purpose — the
enrichment is blocking network I/O, so FastAPI runs them in its threadpool.

Route-order note: the literal ``/api/papers/by-doi…`` paths are mounted before
the parametric ``/api/papers/{paper_id}`` family in ``webapp.py``.
"""

from __future__ import annotations

from typing import List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import paper_ingest
from context import get_conn

router = APIRouter()

DEFAULT_SOURCE = paper_ingest.DOI_SOURCE
_EXISTS_CHUNK = 400  # SQLite's default variable limit is 999


class PaperByDoi(BaseModel):
    """One paper worth having. The DOI is identity; the rest is what the
    caller already knows — enrichment fills what it left empty."""

    doi: str
    title: str = ""
    # A list (plugin rows carry authors as JSON) or the library's own
    # "; "-joined string — both land as the joined string.
    authors: Union[List[str], str, None] = None
    year: Optional[int] = None
    journal: str = ""
    abstract: str = ""
    # Origin marker written to ``papers.import_source`` (e.g. a plugin name).
    source: str = DEFAULT_SOURCE


class PaperByDoiBatch(BaseModel):
    items: List[PaperByDoi] = Field(default_factory=list)


class ExistsRequest(BaseModel):
    dois: List[str] = Field(default_factory=list)


def _intake(items: List[PaperByDoi]) -> List[dict]:
    return paper_ingest.intake_by_doi([item.model_dump() for item in items])


@router.post("/api/papers/by-doi")
def create_paper_by_doi(payload: PaperByDoi):
    """Legt ein Paper aus DOI + Metadaten an (oder findet das vorhandene)."""
    result = _intake([payload])[0]
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/papers/by-doi/batch")
def create_papers_by_doi(payload: PaperByDoiBatch):
    """Dasselbe fuer eine Liste; ein Fehler betrifft nur seinen Eintrag."""
    results = _intake(payload.items)
    return {
        "total": len(results),
        "created": sum(1 for r in results if r["created"]),
        "existing": sum(1 for r in results if not r["created"] and r["paper_id"]),
        "errors": sum(1 for r in results if r.get("error")),
        "results": results,
    }


@router.post("/api/papers/by-doi/exists")
def papers_exist_by_doi(payload: ExistsRequest):
    """DOIs -> Paper-ID oder null, gekeyt nach der DOI wie gesendet."""
    wanted = {d: paper_ingest.normalize_doi(d) for d in payload.dois if (d or "").strip()}
    found: dict = {}
    norms = sorted({n for n in wanted.values() if n})
    conn = get_conn()
    try:
        for i in range(0, len(norms), _EXISTS_CHUNK):
            chunk = norms[i:i + _EXISTS_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id, LOWER(TRIM(doi)) AS doi FROM papers WHERE LOWER(TRIM(doi)) IN ({marks})",
                chunk,
            ).fetchall()
            for row in rows:
                found[row["doi"]] = row["id"]
    finally:
        conn.close()
    return {"papers": {raw: found.get(norm) for raw, norm in wanted.items()}}
