"""Metadata-validation policy: the propose/apply pair, transport-free.

Holds the two core Metadata Validation operations that both the validate router
AND the maintenance router need:

  * ``validate_propose(paper_id, options)`` — compute a Metadata Proposal for one
    Paper without persisting anything.
  * ``validate_apply(paper_id, data)`` — persist accepted changes from a reviewed
    Proposal (writes fields, renames the PDF, rebuilds symlinks, assigns
    categories).

These previously lived in ``routers/validate.py``. They were relocated here
(Backend-Modularisierung #92) because ``routers/maintenance.py``'s ``full_refresh``
calls them, and a router importing another router would violate the
``routers-independent`` import-linter contract. ``validation_policy`` is a NEUTRAL
top-level module — neither a router nor a service — so it MAY persist / do I/O and
both routers may import it freely.

Canonical home for test patching: ``patch.object(validation_policy,
"validate_propose"/"validate_apply", ...)``. Both ``routers/validate.py`` and
``routers/maintenance.py`` reference these via this module so there is exactly one
patch target.

Behaviour is bit-identical to the previous ``routers/validate.py`` handlers.
"""

from __future__ import annotations

import logging
import os
from typing import List

import metadata_validation
from context import get_conn
from fastapi import HTTPException
from literature_manager import (
    Config,
    Database,
    _rebuild_all_symlinks,
    create_symlinks,
    extract_text_from_pdf,
    generate_filename,
)
from llm_client import llm_for
from pydantic import BaseModel

# Backwards-compatible alias so handler bodies copied verbatim keep their
# existing ``_get_conn()`` calls without modification.
_get_conn = get_conn

VALIDATE_PAGES = 15  # Seiten fuer Metadaten-Validierung


# ---------------------------------------------------------------------------
# Pydantic models (moved verbatim from routers/validate.py)
# ---------------------------------------------------------------------------

class ValidateOptions(BaseModel):
    use_llm: bool = True
    pages: int = VALIDATE_PAGES


class ValidateApplyRequest(BaseModel):
    """Accepted changes from a Metadata Proposal review popup.

    Sent to POST /api/papers/{id}/validate/apply after the user has
    reviewed (and possibly edited) a Proposal returned by
    POST /api/papers/{id}/validate/propose.
    """
    changes: dict = {}
    category_assignments: List[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


# ---------------------------------------------------------------------------
# Policy operations
# ---------------------------------------------------------------------------

async def validate_propose(paper_id: int, options: ValidateOptions = None):
    """Compute a Metadata Proposal for one Paper without persisting anything.

    Loads the Paper + its PDF, calls ``metadata_validation.propose()``, and
    returns the resulting Proposal as JSON. Paired with the apply operation.
    """
    if options is None:
        options = ValidateOptions()

    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()

    filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="PDF-Datei nicht gefunden")

    pdf_text = extract_text_from_pdf(filepath, max_pages=options.pages)

    llm_client = None
    if options.use_llm and Config.KICONNECT_API_KEY:
        llm_client = llm_for("metadata_extract")

    category_tree = None
    categories_json = None
    if options.use_llm:
        db = _get_db()
        category_tree = db.get_category_tree()
        categories_json = db.get_categories()

    hints = {
        "filename": paper.get("filename", "") or "",
        "original_filename": paper.get("original_filename", "") or "",
    }

    proposal = metadata_validation.propose(
        paper=paper,
        pdf_text=pdf_text,
        llm_client=llm_client,
        hints=hints,
        category_tree=category_tree,
        categories_json=categories_json,
    )

    return {
        "paper_id": paper_id,
        "changes": proposal.changes,
        "current": proposal.current,
        "source_per_field": proposal.source_per_field,
        "category_suggestions": proposal.category_suggestions,
        "warnings": proposal.warnings,
        "confidence": proposal.confidence,
        "diagnostics": proposal.diagnostics,
    }


async def validate_apply(paper_id: int, data: ValidateApplyRequest):
    """Persist accepted changes from a reviewed Metadata Proposal.

    Writes accepted metadata fields, renames the PDF file if title/authors/
    year changed, rebuilds symlinks, persists accepted Category assignments,
    and returns the updated Paper.
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()

    changes = data.changes or {}

    # 1. Write metadata field updates
    update_fields = []
    update_values = []
    for fld in ("title", "authors", "year", "doi", "isbn", "abstract",
                "journal", "publisher"):
        if fld in changes and changes[fld] is not None:
            val = changes[fld]
            if fld == "abstract" and isinstance(val, str):
                val = val[:5000]
            update_fields.append(f"{fld} = ?")
            update_values.append(val)

    if update_fields:
        update_values.append(paper_id)
        conn = _get_conn()
        try:
            conn.execute(
                f"UPDATE papers SET {', '.join(update_fields)}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                update_values,
            )
            conn.commit()
        finally:
            conn.close()

    # 2. Rename the PDF file if title/authors/year changed
    new_filename = None
    filepath = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
    rename_needed = (
        (changes.get("title") or changes.get("authors") or changes.get("year"))
        and os.path.exists(filepath)
    )
    if rename_needed:
        merged = {
            "title": changes.get("title", paper["title"]),
            "authors": changes.get("authors", paper["authors"]),
            "year": changes.get("year", paper["year"]),
        }
        candidate = generate_filename(merged)
        if candidate != paper["filename"]:
            target = os.path.join(Config.ALL_DIR, candidate)
            counter = 1
            base_candidate = candidate
            while os.path.exists(target) and target != filepath:
                name, ext = os.path.splitext(base_candidate)
                candidate = f"{name}_{counter}{ext}"
                target = os.path.join(Config.ALL_DIR, candidate)
                counter += 1
            try:
                os.rename(filepath, target)
                new_filename = candidate
                conn = _get_conn()
                try:
                    conn.execute(
                        "UPDATE papers SET filename = ? WHERE id = ?",
                        (new_filename, paper_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
                db = _get_db()
                _rebuild_all_symlinks(db)
            except OSError as e:
                logging.warning(f"Rename failed: {e}")

    # 3. Persist accepted Category assignments
    db = _get_db()
    for assignment in (data.category_assignments or []):
        cat_id = assignment.get("category_id")
        confidence = assignment.get("confidence", 0.0)
        if cat_id:
            db.assign_category(
                paper_id, cat_id, confidence, assigned_by="validate-apply"
            )
    if data.category_assignments:
        create_symlinks(db, paper_id, new_filename or paper["filename"])

    # 4. Return the updated Paper
    conn = _get_conn()
    try:
        updated = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        result = dict(updated)
        cats = conn.execute(
            "SELECT c.id, c.name FROM paper_categories pc "
            "JOIN categories c ON pc.category_id = c.id WHERE pc.paper_id = ?",
            (paper_id,),
        ).fetchall()
        result["categories"] = [dict(c) for c in cats]
    finally:
        conn.close()
    return result
