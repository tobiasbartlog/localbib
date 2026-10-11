"""Router: Refresh Review — a library refresh the user reviews before it writes.

Serves:
  POST /api/maintenance/refresh-review/propose  (SSE stream — computes rows, writes nothing)
  POST /api/maintenance/refresh-review/apply    (writes only the ticked rows)

This pair is the one way contents the user curates are refreshed (Settings →
Refresh, block ①): it re-evaluates every selected item against the *current*
category list, and may also propose removing a category; nothing is persisted
until the user ticks the row. Derived data is recomputed without review by
``POST /api/maintenance/recompute`` (block ②, #213). The row shape and the
staleness check live in ``services/refresh_review.py``.

No import from ``webapp`` or any other router.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import List, Optional

import metadata_validation
import validation_policy
from context import get_conn
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    categorize_with_llm,
    create_symlinks,
    extract_text_from_pdf,
    fetch_crossref_metadata,
)
from llm_client import llm_for
from pydantic import BaseModel
from services import refresh_review
from validation_policy import VALIDATE_PAGES, ValidateApplyRequest

router = APIRouter()


class ProposeRequest(BaseModel):
    scopes: List[str]
    paper_ids: Optional[List[int]] = None
    only_gaps: bool = True


class ApplyRequest(BaseModel):
    rows: List[dict]


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _load_papers(paper_ids: Optional[List[int]]) -> List[dict]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM papers ORDER BY id").fetchall()
    finally:
        conn.close()
    papers = [dict(r) for r in rows]
    if paper_ids is not None:
        wanted = set(paper_ids)
        papers = [p for p in papers if p["id"] in wanted]
    return papers


def _selection(data: ProposeRequest) -> tuple[set, list]:
    """``(scopes, [(paper, applicable scopes)])`` — shared by propose and count,
    so the number shown before the start is the number the run works through."""
    scopes = {s for s in data.scopes if s in refresh_review.SCOPES}
    if not scopes:
        raise HTTPException(status_code=400, detail="error.refresh_review_no_scope")
    papers = _load_papers(data.paper_ids)
    conn = get_conn()
    try:
        categorised = {r["paper_id"] for r in conn.execute(
            "SELECT DISTINCT paper_id FROM paper_categories").fetchall()}
    finally:
        conn.close()
    return scopes, refresh_review.select_items(papers, categorised, scopes, data.only_gaps)


def _find_abstract(paper: dict, pdf_text: str) -> tuple[str, str]:
    """CrossRef first, then the LLM looking for the abstract in the PDF text —
    computed only; the write happens on apply."""
    if paper.get("doi"):
        cr = fetch_crossref_metadata(paper["doi"])
        if cr and (cr.get("abstract") or "").strip():
            return cr["abstract"].strip()[:5000], "crossref"
    if pdf_text.strip() and Config.llm_ready("fast"):
        found = metadata_validation.llm_find_abstract_in_text(
            llm_for("abstract"),
            paper.get("title", "") or "", paper.get("authors", "") or "",
            pdf_text[:4000],
        )
        if len(found) > 50:
            return found[:5000], "llm"
    return "", ""


def _propose_one(paper: dict, scopes: set, db: Database, categories_json: list,
                 category_tree: str, names: dict) -> tuple[list, list]:
    """Rows for one item plus the problems met on the way (never raises for
    an LLM/network failure — the item simply yields fewer rows)."""
    rows: list = []
    problems: list = []
    filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper.get("filename") or ""))
    has_pdf = bool(paper.get("filename")) and os.path.isfile(filepath)
    pdf_text = extract_text_from_pdf(filepath, max_pages=VALIDATE_PAGES) if has_pdf else ""
    if not pdf_text.strip():
        pdf_text = paper.get("ocr_text") or ""

    if "metadata" in scopes:
        if pdf_text.strip():
            llm = llm_for("metadata_extract") if Config.llm_ready("fast") else None
            proposal = metadata_validation.propose(
                paper=paper, pdf_text=pdf_text, llm_client=llm,
                hints={"filename": paper.get("filename", "") or "",
                       "original_filename": paper.get("original_filename", "") or ""},
            )
            rows += refresh_review.field_rows(paper, proposal.changes, proposal.source_per_field)
        else:
            problems.append("no_text")

    proposed_abstract = next((r["after"] for r in rows if r.get("field") == "abstract"), None)
    if "abstracts" in scopes and not (paper.get("abstract") or "").strip() and proposed_abstract is None:
        abstract, source = _find_abstract(paper, pdf_text)
        if abstract:
            rows += refresh_review.field_rows(paper, {"abstract": abstract}, {"abstract": source})
            proposed_abstract = abstract

    if "categories" in scopes and Config.llm_ready("fast"):
        client = llm_for("categorize")
        suggestions = categorize_with_llm(
            title=paper.get("title") or "",
            abstract=paper.get("abstract") or proposed_abstract or "",
            text_snippet=pdf_text[:2000],
            category_tree=category_tree,
            categories_json=categories_json,
            llm=client,
        )
        if not suggestions and getattr(client, "last_error", None):
            problems.append("llm_failed")
        rows += refresh_review.category_rows(
            paper["id"], db.get_paper_categories(paper["id"]), suggestions, names)
    return rows, problems


@router.post("/api/maintenance/refresh-review/propose")
async def propose_refresh(data: ProposeRequest):
    """Stream progress while computing before/after rows; the final ``done``
    event carries every row. Writes nothing."""
    scopes, selection = _selection(data)
    db = _get_db()
    categories_json = db.get_categories()
    category_tree = db.get_category_tree()
    names = {c["id"]: c["name"] for c in categories_json}
    warnings = []
    if scopes & {"categories"} and not Config.llm_ready("fast"):
        warnings.append("llm_not_ready")
    if "categories" in scopes and not categories_json:
        warnings.append("no_categories")

    async def generate():
        total = len(selection)
        all_rows: list = []
        stats = {"items": total, "errors": 0, "no_text": 0}
        for i, (paper, item_scopes) in enumerate(selection):
            try:
                rows, problems = await asyncio.to_thread(
                    _propose_one, paper, item_scopes, db, categories_json, category_tree, names)
                for r in rows:
                    r["paper_title"] = paper.get("title") or paper.get("filename") or ""
                all_rows += rows
                stats["no_text"] += problems.count("no_text")
                stats["errors"] += problems.count("llm_failed")
            except Exception as e:
                logging.warning(f"Refresh-Review fehlgeschlagen für Paper {paper['id']}: {e}")
                stats["errors"] += 1
            pct = int((i + 1) / total * 100) if total else 100
            yield ("data: " + json.dumps({
                "type": "progress", "current": i + 1, "total": total, "percent": pct,
                "title": paper.get("title") or "", "rows": len(all_rows),
            }) + "\n\n")
        yield ("data: " + json.dumps({
            "type": "done", "rows": all_rows, "stats": stats, "warnings": warnings,
        }) + "\n\n")

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/maintenance/refresh-review/count")
async def count_refresh(data: ProposeRequest):
    """How many items a ``propose`` with the same input would work through.
    Database only — no LLM, no network."""
    _scopes, selection = _selection(data)
    return {"count": len(selection)}


@router.post("/api/maintenance/refresh-review/apply")
async def apply_refresh(data: ApplyRequest):
    """Persist the ticked rows. Each row is checked against the item's current
    state first; a stale row is skipped and reported, never forced."""
    ids = set()
    for r in data.rows:
        try:
            ids.add(int(r.get("paper_id")))
        except (TypeError, ValueError):
            pass
    papers = {p["id"]: p for p in _load_papers(sorted(ids))}
    conn = get_conn()
    try:
        assigned: dict = {}
        for r in conn.execute("SELECT paper_id, category_id FROM paper_categories").fetchall():
            if r["paper_id"] in ids:
                assigned.setdefault(r["paper_id"], set()).add(r["category_id"])
        known = {r["id"] for r in conn.execute("SELECT id FROM categories").fetchall()}
    finally:
        conn.close()

    plan, skipped = refresh_review.plan_apply(data.rows, papers, assigned, known)

    db = _get_db()
    applied = 0
    rebuild_all = False
    for pid, entry in plan.items():
        try:
            if entry["fields"]:
                await validation_policy.validate_apply(
                    pid, ValidateApplyRequest(changes=entry["fields"]))
                if "abstract" in entry["fields"]:
                    conn = get_conn()
                    try:
                        conn.execute("UPDATE papers SET abstract_source = ? WHERE id = ?",
                                     ("refresh", pid))
                        conn.commit()
                    finally:
                        conn.close()
            for cid, confidence in entry["add"]:
                db.assign_category(pid, cid, confidence, assigned_by="refresh-review")
            if entry["remove"]:
                conn = get_conn()
                try:
                    conn.executemany(
                        "DELETE FROM paper_categories WHERE paper_id = ? AND category_id = ?",
                        [(pid, cid) for cid in entry["remove"]],
                    )
                    conn.commit()
                finally:
                    conn.close()
                rebuild_all = True
            applied += len(entry["fields"]) + len(entry["add"]) + len(entry["remove"])
            if entry["add"] and not rebuild_all:
                conn = get_conn()
                try:
                    filename = conn.execute(
                        "SELECT filename FROM papers WHERE id = ?", (pid,)).fetchone()["filename"]
                finally:
                    conn.close()
                if filename:
                    create_symlinks(db, pid, filename)
        except Exception as e:
            logging.warning(f"Refresh-Review: Übernehmen fehlgeschlagen für Paper {pid}: {e}")
            skipped.append({"id": f"{pid}:*", "reason": "error"})
    if rebuild_all:
        # A removed category leaves a stale link in its folder; only a full
        # rebuild drops it (same as DELETE /api/papers/{id}/categories/{cid}).
        await run_in_threadpool(_rebuild_all_symlinks, db)
    return {"applied": applied, "skipped": skipped}
