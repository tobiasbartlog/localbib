"""Router: Refresh Review — a library refresh the user reviews before it writes.

Serves:
  POST /api/maintenance/refresh-review/propose  (SSE stream — computes rows, writes nothing
                                                 but the bibliographic scope's gap fills)
  POST /api/maintenance/refresh-review/count    (how many items a propose works through)
  POST /api/maintenance/refresh-review/apply    (writes only the ticked rows)

The scope ``bibliographic`` completes an item's bibliographic data from its
DOI's or ISBN's record (``bib_lookup``: CrossRef, DataCite, Open Library) and
needs no PDF. **It writes during the run** — maintainer decision (ADR-0027):
fields the item has *empty* are filled directly (:func:`_write_fills`, this
router is the write point; ``services/refresh_review`` stays pure), differing
values come back as ordinary field rows for review. A fill never overwrites a
stored value (``item_type`` included), follows the write rules
(``bib_fields.valid_changes``) and renames no file and rebuilds no symlink:
title, authors and year are only ever filled where they were empty, and the
PDF keeps its name until a deliberate edit. Every progress event carries
``filled``/``filled_items`` so far, so a cancelled run still reports what it
wrote.

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

import bib_fields
import bib_lookup
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


def _selection(data: ProposeRequest) -> tuple[set, list, list]:
    """``(scopes, [(paper, applicable scopes)], papers)`` — shared by propose
    and count, so the number shown before the start is the number the run
    works through."""
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
    return scopes, refresh_review.select_items(papers, categorised, scopes, data.only_gaps), papers


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


def _bibliographic(paper: dict) -> tuple[dict, list]:
    """The *bibliographic* scope for one item: ``(fills, rows)`` from the
    DOI's or ISBN's record — fills for its empty fields (computed here,
    written by the run through :func:`_write_fills`), review rows for the
    differing ones. No PDF text needed."""
    record = bib_lookup.fetch(doi=paper.get("doi") or "", isbn=paper.get("isbn") or "",
                              crossref_fetch=fetch_crossref_metadata)
    found = bib_lookup.compare(paper, record["fields"], record["sources"])
    return found["fills"], refresh_review.bibliographic_rows(paper, found["conflicts"])


def _propose_one(paper: dict, scopes: set, db: Database, categories_json: list,
                 category_tree: str, names: dict) -> tuple[list, list, dict]:
    """Rows for one item, the problems met on the way and the *bibliographic*
    gap fills the caller writes (never raises for an LLM/network failure —
    the item simply yields fewer rows)."""
    rows: list = []
    problems: list = []
    fills: dict = {}
    if "bibliographic" in scopes:
        fills, rows = _bibliographic(paper)
        # The other scopes see the item as it is once the fills are written.
        paper = {**paper, **fills}

    pdf_text = ""
    if scopes & {"metadata", "abstracts", "categories"}:
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
    # Two scopes may propose the same field: the first row stands (the
    # record's, then the PDF's), one row per field.
    seen: set = set()
    unique = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    return unique, problems, fills


async def _write_fields(paper_id: int, fields: dict) -> None:
    """The one write of refreshed metadata fields: ``validate_apply`` (fields,
    file rename, symlinks); a refreshed abstract is marked as such."""
    await validation_policy.validate_apply(paper_id, ValidateApplyRequest(changes=fields))
    if "abstract" in fields:
        conn = get_conn()
        try:
            conn.execute("UPDATE papers SET abstract_source = ? WHERE id = ?",
                         ("refresh", paper_id))
            conn.commit()
        finally:
            conn.close()


def _write_fills(paper: dict, fills: dict) -> dict:
    """The *bibliographic* scope's gap fills for one item, written during the
    run (maintainer decision, ADR-0027). Returns what was written.

    * only fields the item has empty (``bib_fields.is_empty``) — a stored
      value, ``item_type`` included, is never overwritten here;
    * by the rules of every metadata write (``bib_fields.valid_changes``: a
      known type, ISO dates, the year in step with the date; a refused value
      is dropped);
    * no file rename and no symlink rebuild: a fill only ever sets title,
      authors or year where they were empty, and renaming each item's PDF
      (with a full rebuild each time) mid-run is exactly what a fill must not
      cost. The name follows at the next deliberate edit."""
    empty = {f: v for f, v in fills.items() if bib_fields.is_empty(f, paper.get(f))}
    if (paper.get("item_type") or "").strip():
        empty.pop("item_type", None)
    if not bib_fields.is_empty("pub_date", paper.get("pub_date")):
        empty.pop("year", None)   # the stored date names the year; a fill must not clear it
    changes = bib_fields.valid_changes(empty, paper)
    if not changes:
        return {}
    if "abstract" in changes:
        changes["abstract"] = str(changes["abstract"])[:5000]
    sets = ", ".join(f"{f} = ?" for f in changes)
    extra = ", abstract_source = 'refresh'" if "abstract" in changes else ""
    conn = get_conn()
    try:
        conn.execute(f"UPDATE papers SET {sets}{extra}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*changes.values(), paper["id"]))
        conn.commit()
    finally:
        conn.close()
    return changes


@router.post("/api/maintenance/refresh-review/propose")
async def propose_refresh(data: ProposeRequest):
    """Stream progress while computing before/after rows; the final ``done``
    event carries every row. Writes nothing — except the *bibliographic*
    scope's fills of empty fields (maintainer decision, :func:`_write_fills`),
    counted in ``stats.filled``/``filled_items`` and in every progress event,
    so a cancelled run still reports them; a differing value is a row like
    any other."""
    scopes, selection, _papers = _selection(data)
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
        stats = {"items": total, "errors": 0, "no_text": 0, "filled": 0, "filled_items": 0}
        for i, (paper, item_scopes) in enumerate(selection):
            try:
                rows, problems, fills = await asyncio.to_thread(
                    _propose_one, paper, item_scopes, db, categories_json, category_tree, names)
                # Written here, between two progress events (no await between
                # the write and the count, so the next event reports it).
                written = _write_fills(paper, fills) if fills else {}
                if written:
                    stats["filled"] += len([f for f in written if f in fills])
                    stats["filled_items"] += 1
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
                "filled": stats["filled"], "filled_items": stats["filled_items"],
            }) + "\n\n")
        yield ("data: " + json.dumps({
            "type": "done", "rows": all_rows, "stats": stats, "warnings": warnings,
        }) + "\n\n")

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/maintenance/refresh-review/count")
async def count_refresh(data: ProposeRequest):
    """How many items a ``propose`` with the same input would work through.
    Database only — no LLM, no network. With the *bibliographic* scope also
    ``no_identifier``: the items it skips for want of a DOI or an ISBN."""
    scopes, selection, papers = _selection(data)
    out = {"count": len(selection)}
    if "bibliographic" in scopes:
        out["no_identifier"] = refresh_review.without_identifier(papers, data.only_gaps)
    return out


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
                await _write_fields(pid, entry["fields"])
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
