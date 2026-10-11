"""Router: Library Diagnosis — find problems, then fix them one by one.

Serves:
  GET  /api/diagnosis       the Findings (``services/library_diagnosis.py``)
  POST /api/diagnosis/fix   carry out one fix action for one Finding

The service decides *what* is wrong from a snapshot; this router gathers the
snapshot (DB rows, the files in ``all/``, the Startup check's LLM probe, the
backup verdict of ``backup.health``) and is the write point for every fix.
Errors are codes (ADR-0018).

No import from ``webapp`` or any other router.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

import backup
import collection_membership as membership
import library_events as events
import llm_client
import paper_ingest
from context import get_conn
from doi_discovery import canonical_arxiv_doi
from literature_manager import Config, Database, _rebuild_all_symlinks
from services import library_diagnosis, llm_check

router = APIRouter()


class FixRequest(BaseModel):
    action: str
    paper_id: Optional[int] = None
    file: Optional[str] = None


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _pdf_files() -> list[str]:
    try:
        return [f for f in os.listdir(Config.ALL_DIR)
                if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(Config.ALL_DIR, f))]
    except OSError:
        return []


def _backup_health() -> Optional[dict]:
    """``backup.health`` for the configured folder and schedule: no backup
    set up yet, or an automatic backup that is overdue."""
    folder = Config.backup_dir()
    return backup.health(
        folder_set=bool(folder), interval=Config.auto_backup(),
        include_pdfs=bool(Config.AUTO_BACKUP_PDFS),
        backups=backup.list_backups(folder)["backups"] if folder else [],
        last_auto=backup.runner.status()["last_auto"], now=datetime.now())


def _probe_llm() -> list[dict]:
    """The LLM findings come from the Startup check's one path (#209): one
    ``/models`` request per connection in use, classified by
    ``services.llm_check``. A rejected key, an unreachable host and a model
    the list no longer offers are problems — a provider without a model list
    (404 & co.) is not one."""
    return llm_check.diagnosis_probes(llm_client.check_roles())


@router.get("/api/diagnosis")
def get_diagnosis(llm: bool = True):
    conn = get_conn()
    try:
        papers = [dict(r) for r in conn.execute(
            "SELECT id, title, filename, doi, "
            "EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id = papers.id) AS categorized "
            "FROM papers ORDER BY id").fetchall()]
    finally:
        conn.close()
    findings = library_diagnosis.diagnose(papers, _pdf_files(), _probe_llm() if llm else [],
                                          backup_health=_backup_health())
    return {"findings": findings, "counts": library_diagnosis.counts(findings)}


# ---------------------------------------------------------------------------
# Fixes
# ---------------------------------------------------------------------------

def _paper(paper_id: Optional[int]) -> dict:
    if paper_id is None:
        raise HTTPException(status_code=400, detail="error.diagnosis.paper_required")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="error.item_not_found")
    return dict(row)


def _orphan_path(name: Optional[str]) -> str:
    """The path of a stray PDF in ``all/`` — refused if it is not a bare file
    name there, or if an item already points at it."""
    name = (name or "").strip()
    if not name or os.path.basename(name) != name:
        raise HTTPException(status_code=400, detail="error.diagnosis.file_invalid")
    path = os.path.join(Config.ALL_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="error.diagnosis.file_missing")
    conn = get_conn()
    try:
        taken = conn.execute("SELECT id FROM papers WHERE filename = ?", (name,)).fetchone()
    finally:
        conn.close()
    if taken:
        raise HTTPException(status_code=409, detail="error.diagnosis.file_taken")
    return path


def _has_pdf(paper: dict) -> bool:
    name = (paper.get("filename") or "").strip()
    return bool(name) and os.path.isfile(os.path.join(Config.ALL_DIR, os.path.basename(name)))


def _set_filename(paper_id: int, filename: str) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE papers SET filename = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (filename, paper_id))
        conn.commit()
    finally:
        conn.close()


def _assign_file(paper: dict, name: Optional[str]) -> dict:
    """Hang a stray PDF on an item whose own file is gone (or that never had
    one). The attach path does the rest — hash, text, page count, chunks,
    links — under the item's generated name; the stray copy is removed once
    the item holds its own."""
    if _has_pdf(paper):
        raise HTTPException(status_code=409, detail="error.diagnosis.has_pdf")
    path = _orphan_path(name)
    old = paper.get("filename") or ""
    if old:
        _set_filename(paper["id"], "")  # attach only takes items without a PDF
    try:
        result = paper_ingest.attach_pdf_from_path(paper["id"], path)
    except Exception:
        if old:
            _set_filename(paper["id"], old)
        raise
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(
            os.path.abspath(os.path.join(Config.ALL_DIR, result["filename"]))):
        try:
            os.remove(path)
        except OSError as e:
            logging.warning("Diagnose: Streu-PDF %s nicht entfernbar: %s", path, e)
    return {"filename": result["filename"]}


def _fetch_oa(paper: dict) -> dict:
    if _has_pdf(paper) or paper.get("filename"):
        raise HTTPException(status_code=409, detail="error.diagnosis.has_pdf")
    doi = (paper.get("doi") or "").strip()
    if not doi:
        raise HTTPException(status_code=400, detail="error.diagnosis.no_doi")
    work = paper_ingest.fetch_openalex_works([doi]).get(paper_ingest.normalize_doi(doi))
    url = work.oa_pdf_url if work else ""
    if not url:
        raise HTTPException(status_code=404, detail="error.diagnosis.no_oa_pdf")
    if not paper_ingest.try_fetch_oa_pdf(paper["id"], url):
        raise HTTPException(status_code=502, detail="error.diagnosis.oa_download_failed")
    return {"fetched": True}


@router.post("/api/diagnosis/fix")
async def fix(data: FixRequest):
    action = data.action
    if action == "clear_pdf":
        paper = _paper(data.paper_id)
        if _has_pdf(paper):
            raise HTTPException(status_code=409, detail="error.diagnosis.has_pdf")
        _set_filename(paper["id"], "")
        return {"status": "ok"}

    if action == "delete_item":
        paper = _paper(data.paper_id)
        if _has_pdf(paper):
            # Deleting an item with its file belongs in the list's own delete,
            # where the user sees what goes. Here only broken entries go.
            raise HTTPException(status_code=409, detail="error.diagnosis.has_pdf")
        conn = get_conn()
        try:
            touched = membership.holding(conn, [paper["id"]])
            conn.execute("DELETE FROM papers WHERE id = ?", (paper["id"],))
            conn.commit()
        finally:
            conn.close()
        events.collection_changed(events.MEMBERS_CHANGED, *touched)
        await run_in_threadpool(_rebuild_all_symlinks, _get_db())
        return {"status": "ok"}

    if action == "fix_doi":
        paper = _paper(data.paper_id)
        fixed = canonical_arxiv_doi(paper.get("doi") or "")
        if not fixed:
            raise HTTPException(status_code=409, detail="error.diagnosis.nothing_to_fix")
        conn = get_conn()
        try:
            conn.execute("UPDATE papers SET doi = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (fixed, paper["id"]))
            conn.commit()
        finally:
            conn.close()
        return {"status": "ok", "doi": fixed}

    if action == "assign_file":
        return {"status": "ok", **_assign_file(_paper(data.paper_id), data.file)}

    if action == "fetch_oa":
        return {"status": "ok", **_fetch_oa(_paper(data.paper_id))}

    if action == "to_inbox":
        path = _orphan_path(data.file)
        os.makedirs(Config.INPUT_DIR, exist_ok=True)
        target = os.path.join(Config.INPUT_DIR, os.path.basename(path))
        if os.path.exists(target):
            raise HTTPException(status_code=409, detail="error.diagnosis.inbox_taken")
        shutil.move(path, target)
        return {"status": "ok"}

    raise HTTPException(status_code=400, detail="error.diagnosis.unknown_action")
