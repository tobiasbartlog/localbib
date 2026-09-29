"""Router: library migration — analyze, commit (SSE), undo.

Serves:
  GET  /api/migration/sources
  POST /api/migration/analyze
  POST /api/migration/commit    (SSE stream)
  POST /api/migration/undo
  POST /api/migration/pdf-folder/import-unmatched

The one path a reader takes when they leave Zotero, Mendeley or Citavi behind
(PRD #173). Every source is a pure adapter that maps its dialect onto
``MigrationItem``; this router is the only place that writes (ADR-0006).

Three endpoints, three promises:

* **analyze** reads and never writes. It answers the questions the wizard's
  preview asks — how many entries, which of them the library already has, how
  many PDFs are actually on disk, which collections and tags would come into
  being — so the reader decides on facts, not on hope.
* **commit** streams the smart import's frame shape (``progress`` / ``error`` /
  ``complete``, i18n descriptors, ``llm_failures``) because the SPA already
  knows how to render it, and records every paper it creates under a
  ``run_id``.
* **undo** spends that record: the papers this run created are deleted with
  their files, files this run *attached* to papers that already existed are
  detached again, and matched papers are otherwise left alone. A migration a
  reader cannot take back is a migration they will not dare to run.

Adding a source means one entry in :data:`ADAPTERS` and one adapter module —
no endpoint changes. The one source that is not an export file is
``pdf_folder`` (#176, the Mendeley path): it brings *files*, not metadata, so
its analyze answers a match table (``kind: "pdf_folder"``) and its commit
attaches exactly the pairs the client confirmed. Its unmatched PDFs can be
handed to the smart import with one call — they are copied into the input
folder the import already watches, originals untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import bibtex_migration
import paper_ingest
import ris_import
import zotero_rdf_import
from context import get_conn
from literature_manager import Config, Database, _rebuild_all_symlinks
from migration_items import MigrationItem, MigrationOptions, MigrationUnreadable
from openalex_client import OpenAlexClient
from paper_matcher import match as match_ref
from services import pdf_folder_match

router = APIRouter()


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MigrationSource:
    """One registered source: what the wizard shows and what reads the export."""

    id: str
    label_key: str
    kind: str                       # "file" | "folder"
    extensions: tuple[str, ...]
    # ``None`` for a source that has no entries to read — the PDF-folder
    # abgleich brings files, not metadata (see ``result_kind``).
    read: Optional[Callable[[str], List[MigrationItem]]] = None
    # An export that *is* a folder holding its export file — a Zotero RDF
    # export is a directory with one .rdf and a files/ tree, and readers point
    # at either. The wizard still offers a file picker (``kind``).
    accepts_folder: bool = False
    # What ``analyze`` answers with: ``items`` (entry rows, every metadata
    # export) or ``pdf_folder`` (a match table of file ↔ item). The response
    # carries this as ``kind`` so a client never has to guess by source id.
    result_kind: str = "items"


ADAPTERS: dict[str, MigrationSource] = {
    "bibtex": MigrationSource(
        id="bibtex", label_key="migration.source.bibtex", kind="file",
        extensions=(".bib", ".bibtex"), read=bibtex_migration.read_items,
    ),
    "ris": MigrationSource(
        id="ris", label_key="migration.source.ris", kind="file",
        extensions=(".ris", ".txt"), read=ris_import.read_items,
    ),
    "zotero_rdf": MigrationSource(
        id="zotero_rdf", label_key="migration.source.zotero_rdf", kind="file",
        extensions=(".rdf",), read=zotero_rdf_import.read_items,
        accepts_folder=True,
    ),
    # The Mendeley path (#176): no export file at all, just the folder the old
    # manager kept its PDFs in. Also the repair step for any run that reported
    # missing attachments.
    "pdf_folder": MigrationSource(
        id="pdf_folder", label_key="migration.source.pdf_folder", kind="folder",
        extensions=(".pdf",), read=None, result_kind="pdf_folder",
    ),
}


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------

class MigrationOptionsIn(BaseModel):
    """The wizard's step-3 options. Mirrors ``MigrationOptions`` field for
    field; the dataclass owns the defaults and the value validation."""

    import_abstract: bool = True
    import_notes: bool = True
    import_collections: bool = True
    import_tags: bool = True
    import_date_added: bool = True
    keep_cite_keys: bool = True
    fill_missing: bool = True
    llm_categorize: bool = False
    attach_pdfs: str = "all"
    oa_fallback: bool = False
    duplicates: str = "skip"
    extract_references: str = "none"

    def to_options(self) -> MigrationOptions:
        return MigrationOptions(**self.model_dump())


class AnalyzeRequest(BaseModel):
    source: str
    path: str
    # pdf_folder only: walk subfolders. Opt-in — a reader's Downloads folder is
    # not a library, and walking it by accident costs minutes of extraction.
    recursive: bool = False


class PdfFolderMatchIn(BaseModel):
    """One confirmed file ↔ item pair, as the client ticked it off."""

    pdf_path: str
    paper_id: int


class CommitRequest(BaseModel):
    source: str
    path: str
    options: MigrationOptionsIn = MigrationOptionsIn()
    selected_keys: Optional[List[str]] = None      # None = every item
    attachment_keys: Optional[List[str]] = None    # used when attach_pdfs="selected"
    recursive: bool = False
    # pdf_folder only: exactly the matches the reader confirmed. Nothing else
    # is attached — a match the client unticked simply does not arrive.
    matches: Optional[List[PdfFolderMatchIn]] = None


class ImportUnmatchedRequest(BaseModel):
    paths: List[str]


class UndoRequest(BaseModel):
    run_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _adapter(source: str) -> MigrationSource:
    adapter = ADAPTERS.get((source or "").strip().lower())
    if adapter is None:
        raise HTTPException(status_code=400, detail="error.migration_source_unknown")
    return adapter


def _read_items(adapter: MigrationSource, path: str) -> List[MigrationItem]:
    """Read the export at ``path``. A path the reader typed is user input, so
    every failure below becomes an error code, never a traceback."""
    target = os.path.abspath(os.path.expanduser((path or "").strip()))
    if adapter.kind == "folder":
        exists = os.path.isdir(target)
    elif adapter.accepts_folder:
        exists = os.path.exists(target)
    else:
        exists = os.path.isfile(target)
    if not exists:
        raise HTTPException(status_code=400, detail="error.migration_path_not_found")
    try:
        return adapter.read(target)
    except MigrationUnreadable as e:
        # The file is there but is not what this source reads — a broken export
        # or the wrong adapter. The reader can act on that; a traceback not.
        logging.warning("Migration: %s ist kein lesbarer %s-Export: %s",
                        path, adapter.id, e)
        raise HTTPException(status_code=422, detail="error.migration_unreadable")
    except Exception as e:
        logging.warning("Migration: Quelle %s nicht lesbar (%s): %s", path, adapter.id, e)
        raise HTTPException(status_code=422, detail="error.migration_read_failed")


def _match_item(item: MigrationItem, conn) -> dict:
    """What the library already knows about this item (read-only)."""
    result = match_ref({"doi": item.doi, "title": item.title}, conn)
    if result.matched_paper_id is None:
        return {"matched_paper_id": None, "match_strategy": "none",
                "match_confidence": 0.0, "matched_has_pdf": False}
    row = conn.execute(
        "SELECT filename FROM papers WHERE id = ?", (result.matched_paper_id,)
    ).fetchone()
    return {
        "matched_paper_id": result.matched_paper_id,
        "match_strategy": result.match_strategy,
        "match_confidence": result.match_confidence,
        "matched_has_pdf": bool(row and row["filename"]),
    }


def _item_payload(item: MigrationItem, match: dict) -> dict:
    return {
        "key": item.key,
        "entry_type": item.entry_type,
        "title": item.title,
        "authors": item.authors,
        "year": item.year,
        "doi": item.doi,
        "journal": item.journal,
        "tags": list(item.tags),
        "collection_path": list(item.collection_path),
        "has_notes": bool(item.notes),
        "date_added": item.date_added,
        "attachments": list(item.attachments),
        "attachments_missing": list(item.attachments_missing),
        **match,
    }


def _selected(items: List[MigrationItem], keys: Optional[List[str]]) -> List[MigrationItem]:
    if keys is None:
        return items
    wanted = set(keys)
    return [i for i in items if i.key in wanted]


def _oa_pdf_url(item: MigrationItem) -> str:
    """Best-effort OA link for an item whose local PDF was missing. Never
    raises — the migration keeps running when OpenAlex does not answer."""
    try:
        oa = OpenAlexClient(Config.polite_mailto())
        work = None
        if item.doi:
            works = oa.fetch_works_by_doi([item.doi])
            work = works[0] if works else None
        if work is None and item.title:
            work = oa.fetch_work_by_title(item.title)
        return work.oa_pdf_url if work else ""
    except Exception as e:
        logging.info("Migration: OA-Suche fuer '%s' fehlgeschlagen: %s", item.title[:60], e)
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/migration/sources")
async def migration_sources():
    """The registered adapters — the wizard renders one card per entry."""
    return {
        "sources": [
            {"id": a.id, "label_key": a.label_key, "kind": a.kind,
             "extensions": list(a.extensions), "result_kind": a.result_kind}
            for a in ADAPTERS.values()
        ]
    }


@router.post("/api/migration/analyze")
async def migration_analyze(payload: AnalyzeRequest):
    """Read the export and report what a commit *would* do. Writes nothing."""
    adapter = _adapter(payload.source)
    if adapter.result_kind == "pdf_folder":
        return _analyze_pdf_folder(adapter, payload)
    items = _read_items(adapter, payload.path)

    conn = get_conn()
    try:
        entries = [_item_payload(item, _match_item(item, conn)) for item in items]
    finally:
        conn.close()

    collections: list[list[str]] = []
    tags: list[str] = []
    for item in items:
        if item.collection_path and item.collection_path not in collections:
            collections.append(list(item.collection_path))
        for tag in item.tags:
            if tag not in tags:
                tags.append(tag)

    return {
        "kind": adapter.result_kind,
        "source": adapter.id,
        "path": payload.path,
        "total": len(items),
        "matched": sum(1 for e in entries if e["matched_paper_id"]),
        "new": sum(1 for e in entries if not e["matched_paper_id"]),
        "attachments_found": sum(len(e["attachments"]) for e in entries),
        "attachments_missing": sum(len(e["attachments_missing"]) for e in entries),
        "collections": collections,
        "tags": tags,
        "items": entries,
    }


@router.post("/api/migration/commit")
async def migration_commit(payload: CommitRequest):
    """Import the selected items, streaming progress in the smart-import frame
    shape. Every created paper is booked under one ``run_id`` so the whole run
    can be taken back in one call."""
    adapter = _adapter(payload.source)
    if adapter.result_kind == "pdf_folder":
        return _commit_pdf_folder(adapter, payload)
    items = _selected(_read_items(adapter, payload.path), payload.selected_keys)
    options = payload.options.to_options()
    source_name = os.path.basename(os.path.abspath(payload.path))
    attach_only = set(payload.attachment_keys or [])

    async def safe_generate():
        try:
            async for chunk in _commit_events(items, options, source_name, attach_only,
                                              adapter.id, payload.path):
                yield chunk
        except Exception as e:
            logging.error("Migration-Commit Fehler: %s", e, exc_info=True)
            yield _sse({"type": "error",
                        "message": {"code": "error.migration_failed",
                                    "params": {"message": str(e)}}})

    return StreamingResponse(safe_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _commit_events(items: List[MigrationItem], options: MigrationOptions,
                         source_name: str, attach_only: set, source_id: str, path: str):
    """The SSE body of a metadata-export commit (see ``migration_commit``).

    Module-level (not a closure) so a test can drive it directly — advance it
    a few steps and call ``aclose()`` — the same shape the disconnect test for
    the Marketplace install uses (``routers/marketplace.py:_install_events``).

    The run is recorded in a ``finally`` around the per-item loop: a client
    that goes away mid-stream closes this generator (``GeneratorExit``)
    between two ``yield``s, and without the ``finally`` the papers already
    created here would never be booked under ``run_id`` — orphaned, with no
    way to undo them.
    """
    run_id = uuid.uuid4().hex
    db = _get_db()
    created_ids: list[int] = []
    created_rows: list[dict] = []
    attached_rows: list[dict] = []
    results: list[dict] = []
    counts = {"created": 0, "matched": 0, "attached": 0, "failed": 0}
    # Part of the frame shape the SPA already renders (smart import). The
    # category pass runs inside paper_ingest, which swallows its transport
    # errors, so nothing fills this list yet — Slice 5 surfaces them.
    llm_failures: list[dict] = []
    total = len(items)

    yield _sse({"type": "progress", "step": "start", "percent": 0,
                "message": {"code": "migration.step.start",
                            "params": {"total": total}}})
    if total == 0:
        yield _sse({"type": "complete", "run_id": run_id, "results": [],
                    "llm_failures": [], **counts})
        return

    try:
        for index, item in enumerate(items, 1):
            percent = int((index - 1) / total * 90)
            yield _sse({"type": "progress", "step": "item", "percent": percent,
                        "message": {"code": "migration.step.item",
                                    "params": {"index": index, "total": total,
                                               "title": (item.title or item.key)[:80]}}})
            try:
                outcome = _import_item(db, item, options, source_name, attach_only)
            except Exception as e:
                logging.warning("Migration: Eintrag '%s' fehlgeschlagen: %s", item.key, e)
                counts["failed"] += 1
                results.append({"key": item.key, "title": item.title,
                                "status": "failed", "error": str(e)})
                continue

            results.append(outcome)
            if outcome["status"] == "created":
                counts["created"] += 1
                created_ids.append(outcome["paper_id"])
                created_rows.append({"paper_id": outcome["paper_id"], "doi": item.doi})
            elif outcome["status"] == "matched":
                counts["matched"] += 1
            if outcome.get("attached"):
                counts["attached"] += 1
                # Only a PDF hung onto an already-existing (matched) paper is
                # bookable for undo — a created paper's file is undone by
                # deleting the whole row, so it never needs an entry here.
                if outcome["status"] == "matched" and outcome.get("attached_filename"):
                    attached_rows.append({"paper_id": outcome["paper_id"],
                                          "filename": outcome["attached_filename"]})
                yield _sse({"type": "progress", "step": "attach", "percent": percent,
                            "message": {"code": "migration.step.attached",
                                        "params": {"title": (item.title or item.key)[:80]}}})
    finally:
        _record_run(run_id, source_id, path, created_ids, attached_rows)

    if options.fill_missing and created_rows:
        yield _sse({"type": "progress", "step": "enrich", "percent": 93,
                    "message": "migration.step.enrich"})
        try:
            paper_ingest.enrich_imported_papers(created_rows)
        except Exception as e:
            logging.warning("Migration: Anreicherung fehlgeschlagen: %s", e)

    yield _sse({"type": "complete", "run_id": run_id, "results": results,
                "llm_failures": llm_failures, **counts})


@router.post("/api/migration/undo")
async def migration_undo(payload: UndoRequest):
    """Take back one run: delete the papers it created and their files, and
    detach the files it hung onto papers that already existed.

    Matched papers are untouched — they were the library's before the run
    started. A paper the reader has since deleted by hand is simply skipped.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT run_id, created_paper_ids, attached_paper_ids "
            "FROM migration_runs WHERE run_id = ?",
            (payload.run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="error.migration_run_not_found")
        try:
            paper_ids = json.loads(row["created_paper_ids"] or "[]")
        except ValueError:
            paper_ids = []
        try:
            attached = json.loads(row["attached_paper_ids"] or "[]")
        except ValueError:
            attached = []

        detached = _detach_all(conn, attached)
        deleted = 0
        for paper_id in paper_ids:
            paper = conn.execute(
                "SELECT filename FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if not paper:
                continue
            conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
            filename = paper["filename"]
            if filename:
                filepath = os.path.join(Config.ALL_DIR, filename)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except OSError as e:
                        logging.warning("Migration-Undo: Datei nicht entfernbar: %s", e)
            deleted += 1
        conn.execute("DELETE FROM migration_runs WHERE run_id = ?", (payload.run_id,))
        conn.commit()
    finally:
        conn.close()
    if detached or deleted:
        # Ein abgehaengtes PDF oder ein geloeschtes Paper laesst
        # Kategorie-Verknuepfungen (Symlinks) ins Leere zeigen.
        _rebuild_all_symlinks(_get_db())
    return {"run_id": payload.run_id, "deleted": deleted, "created": len(paper_ids),
            "detached": detached, "attached": len(attached)}


# ---------------------------------------------------------------------------
# The per-item import (the only place this router writes a paper)
# ---------------------------------------------------------------------------

def _import_item(db: Database, item: MigrationItem, options: MigrationOptions,
                 source_name: str, attach_only: set) -> dict:
    """One item → one paper row (or a match). Returns the result entry."""
    conn = get_conn()
    try:
        match = _match_item(item, conn)
    finally:
        conn.close()

    matched_id = match["matched_paper_id"]
    if matched_id is None:
        duplicate = db.find_duplicate_paper(doi=item.doi, title=item.title)
        matched_id = duplicate["id"] if duplicate else None

    wants_pdf = _wants_pdf(item, options, attach_only)

    if matched_id is not None:
        attached_filename = None
        if options.duplicates == "attach" and wants_pdf:
            attached_filename = _attach_first(matched_id, item, options)
        return {"key": item.key, "title": item.title, "status": "matched",
                "paper_id": matched_id, "attached": bool(attached_filename),
                "attached_filename": attached_filename}

    paper_id = db.add_paper(_paper_data(item, options, source_name))

    if options.import_collections and item.collection_path:
        category_id = db.get_or_create_category_path(item.collection_path)
        if category_id:
            db.assign_category(paper_id, category_id, confidence=1.0,
                               assigned_by="import")

    attached = bool(_attach_first(paper_id, item, options)) if wants_pdf else False
    if not attached and options.oa_fallback and options.attach_pdfs != "none":
        # "PDFs anhaengen: keine" beantwortet auch den OA-Versuch — wer keine
        # Dateien will, will erst recht keine heruntergeladenen.
        attached = paper_ingest.try_fetch_oa_pdf(paper_id, _oa_pdf_url(item))

    return {"key": item.key, "title": item.title, "status": "created",
            "paper_id": paper_id, "attached": attached}


def _wants_pdf(item: MigrationItem, options: MigrationOptions, attach_only: set) -> bool:
    if not item.attachments or options.attach_pdfs == "none":
        return False
    if options.attach_pdfs == "selected":
        return item.key in attach_only
    return True


def _paper_data(item: MigrationItem, options: MigrationOptions, source_name: str) -> dict:
    """The row a migrated item becomes — before any PDF lands on it.

    ``file_hash`` is the placeholder every PDF-less intake uses (BibTeX commit,
    RIS import): a hash over title+authors, replaced by the real SHA256 the
    moment a PDF is attached.
    """
    file_hash = hashlib.sha256(
        f"{item.title}_{item.authors}".encode("utf-8")).hexdigest()
    return {
        "file_hash": file_hash,
        "filename": "",
        "original_filename": f"Migration: {item.key or item.title[:60]}",
        "title": item.title,
        "authors": item.authors,
        "year": item.year,
        "doi": item.doi,
        "isbn": item.isbn,
        "abstract": (item.abstract or "")[:5000] if options.import_abstract else "",
        "journal": item.journal,
        "publisher": item.publisher,
        "raw_metadata": json.dumps(item.raw, ensure_ascii=False, default=str),
        "ocr_text": "",
        "cite_key": item.key if options.keep_cite_keys else "",
        "import_source": source_name,
        "notes": item.notes if options.import_notes else "",
        "tags": item.tags if options.import_tags else [],
        "date_added": (item.date_added or "") if options.import_date_added else "",
    }


def _attach_first(paper_id: int, item: MigrationItem,
                  options: MigrationOptions) -> Optional[str]:
    """Attach the first existing PDF of the item. Never raises: a broken file
    costs its attachment, not the paper that was just created.

    Returns the stored filename (so a caller can book it under the run for
    undo — needed when this lands on a paper that already existed) or
    ``None`` when nothing could be attached."""
    for path in item.attachments:
        try:
            result = paper_ingest.attach_pdf_from_path(paper_id, path)
        except Exception as e:
            logging.info("Migration: PDF %s nicht anhaengbar: %s", path, e)
            continue
        if options.llm_categorize:
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT title, abstract FROM papers WHERE id = ?", (paper_id,)
                ).fetchone()
            finally:
                conn.close()
            if row:
                paper_ingest.categorize_attached_paper(
                    paper_id, row["title"], row["abstract"], result.get("text", ""))
        return result["filename"]
    return None


def _record_run(run_id: str, source: str, path: str, created_ids: list[int],
                attached: Optional[list[dict]] = None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO migration_runs (run_id, source, source_path,
                                           created_paper_ids, attached_paper_ids)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, source, path, json.dumps(created_ids),
             json.dumps(attached or [])),
        )
        conn.commit()
    finally:
        conn.close()


def _detach_all(conn, attached: list) -> int:
    """Undo of an attachment: the paper goes back to being metadata-only.

    Nothing is deleted that the run did not create — the row survives, only
    the file this run hung onto it goes: filename/hash/text reset (the hash
    back to the title+authors placeholder every PDF-less intake uses, because
    ``file_hash`` is NOT NULL and unique), the copy in ``ALL_DIR`` removed, the
    RAG chunks dropped. A paper whose file the reader has since replaced is
    left alone: the filename no longer matches what this run wrote.
    """
    detached = 0
    for entry in attached:
        if not isinstance(entry, dict):
            continue
        paper_id = entry.get("paper_id")
        filename = entry.get("filename") or ""
        if not paper_id:
            continue
        row = conn.execute(
            "SELECT filename, title, authors FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if not row or not row["filename"] or row["filename"] != filename:
            continue
        placeholder = hashlib.sha256(
            f"{row['title']}_{row['authors']}".encode("utf-8")).hexdigest()
        conn.execute(
            """UPDATE papers SET filename = '', file_hash = ?, ocr_text = '',
               page_count = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (placeholder, paper_id),
        )
        conn.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
        filepath = os.path.join(Config.ALL_DIR, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError as e:
                logging.warning("Migration-Undo: Datei nicht entfernbar: %s", e)
        detached += 1
    return detached


# ---------------------------------------------------------------------------
# PDF-folder abgleich (the Mendeley path, #176)
# ---------------------------------------------------------------------------

def _folder(path: str) -> str:
    """The folder a reader typed, or an error code. Never a traceback."""
    target = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="error.migration_path_not_found")
    return target


def _match_payload(result: pdf_folder_match.FolderMatchResult, folder: str,
                   recursive: bool) -> dict:
    return {
        "kind": "pdf_folder",
        "source": "pdf_folder",
        "path": folder,
        "recursive": recursive,
        "scanned": result.scanned,
        "total": result.scanned,
        "matched": len(result.matches),
        "unmatched_count": len(result.unmatched),
        "already_owned_count": len(result.already_owned),
        "matches": [
            {"pdf_path": m.pdf_path, "pdf_name": m.pdf_name,
             "paper_id": m.paper_id, "paper_title": m.paper_title,
             "strategy": m.strategy, "confidence": m.confidence}
            for m in result.matches
        ],
        "unmatched": [
            {"pdf_path": p, "pdf_name": os.path.basename(p)}
            for p in result.unmatched
        ],
        "already_owned": [
            {"pdf_path": o.pdf_path, "pdf_name": o.pdf_name,
             "paper_id": o.paper_id, "paper_title": o.paper_title}
            for o in result.already_owned
        ],
    }


def _analyze_pdf_folder(adapter: MigrationSource, payload: AnalyzeRequest) -> dict:
    """The match table: which PDF belongs to which item without a file."""
    folder = _folder(payload.path)
    conn = get_conn()
    try:
        result = pdf_folder_match.scan(folder, conn, recursive=payload.recursive)
    finally:
        conn.close()
    return _match_payload(result, folder, payload.recursive)


def _commit_pdf_folder(adapter: MigrationSource, payload: CommitRequest):
    """Attach exactly the matches the client sent back, streaming the same
    frames as every other commit. Re-scanning here would attach what the
    reader unticked, so the confirmed list *is* the work order — every pair is
    only re-checked against the folder and the library."""
    folder = _folder(payload.path)
    pairs = payload.matches or []

    async def generate():
        run_id = uuid.uuid4().hex
        results: list[dict] = []
        attached_rows: list[dict] = []
        counts = {"created": 0, "matched": 0, "attached": 0, "failed": 0}
        total = len(pairs)

        yield _sse({"type": "progress", "step": "start", "percent": 0,
                    "message": {"code": "migration.step.pdfStart",
                                "params": {"total": total}}})
        if total == 0:
            yield _sse({"type": "complete", "run_id": run_id, "results": [],
                        "llm_failures": [], **counts})
            return

        for index, pair in enumerate(pairs, 1):
            name = os.path.basename(pair.pdf_path)
            percent = int((index - 1) / total * 95)
            yield _sse({"type": "progress", "step": "item", "percent": percent,
                        "message": {"code": "migration.step.item",
                                    "params": {"index": index, "total": total,
                                               "title": name[:80]}}})
            error = _pdf_in_folder(pair.pdf_path, folder)
            if error is None:
                try:
                    outcome = paper_ingest.attach_pdf_from_path(
                        pair.paper_id, os.path.abspath(
                            os.path.expanduser(pair.pdf_path)))
                except Exception as e:
                    error = str(e)
                else:
                    counts["matched"] += 1
                    counts["attached"] += 1
                    attached_rows.append({"paper_id": pair.paper_id,
                                          "filename": outcome["filename"]})
                    results.append({"key": pair.pdf_path, "title": name,
                                    "status": "attached", "paper_id": pair.paper_id,
                                    "attached": True})
                    yield _sse({"type": "progress", "step": "attach", "percent": percent,
                                "message": {"code": "migration.step.attached",
                                            "params": {"title": name[:80]}}})
            if error is not None:
                logging.info("PDF-Ordner: %s nicht anhaengbar: %s", pair.pdf_path, error)
                counts["failed"] += 1
                results.append({"key": pair.pdf_path, "title": name,
                                "status": "failed", "paper_id": pair.paper_id,
                                "error": error})

        _record_run(run_id, adapter.id, folder, [], attached_rows)
        yield _sse({"type": "complete", "run_id": run_id, "results": results,
                    "llm_failures": [], **counts})

    async def safe_generate():
        try:
            async for chunk in generate():
                yield chunk
        except Exception as e:
            logging.error("PDF-Ordner-Commit Fehler: %s", e, exc_info=True)
            yield _sse({"type": "error",
                        "message": {"code": "error.migration_failed",
                                    "params": {"message": str(e)}}})

    return StreamingResponse(safe_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _pdf_in_folder(pdf_path: str, folder: str) -> Optional[str]:
    """``None`` when the path is a PDF inside the analysed folder, an error
    text otherwise. The client sends paths back, so the folder it analysed is
    the boundary — a commit never reads outside it."""
    target = os.path.abspath(os.path.expanduser((pdf_path or "").strip()))
    try:
        inside = os.path.commonpath([target, folder]) == folder
    except ValueError:
        inside = False      # different drives on Windows
    if not inside:
        return "outside the analysed folder"
    if not os.path.isfile(target):
        return "file not found"
    return None


@router.post("/api/migration/pdf-folder/import-unmatched")
async def migration_import_unmatched(payload: ImportUnmatchedRequest):
    """Hand unmatched PDFs to the smart import: copy them into the input
    folder the import already watches (``/api/import/pending`` lists it,
    ``/api/import/process-smart/{filename}`` runs one). The originals stay
    where they are — a migration never moves the reader's own files."""
    os.makedirs(Config.INPUT_DIR, exist_ok=True)
    copied: list[dict] = []
    failed: list[dict] = []
    for raw in payload.paths:
        source = os.path.abspath(os.path.expanduser((raw or "").strip()))
        if not os.path.isfile(source) or not source.lower().endswith(".pdf"):
            failed.append({"pdf_path": raw, "error": "error.migration_path_not_found"})
            continue
        name = os.path.basename(source)
        target = os.path.join(Config.INPUT_DIR, name)
        counter = 1
        while os.path.exists(target):
            stem, ext = os.path.splitext(os.path.basename(source))
            name = f"{stem}_{counter}{ext}"
            target = os.path.join(Config.INPUT_DIR, name)
            counter += 1
        try:
            shutil.copy2(source, target)
        except OSError as e:
            logging.warning("PDF-Ordner: %s nicht in den Input-Ordner kopierbar: %s",
                            source, e)
            failed.append({"pdf_path": raw, "error": "error.migration_read_failed"})
            continue
        copied.append({"pdf_path": source, "filename": name})
    return {"copied": copied, "failed": failed,
            "count": len(copied)}
