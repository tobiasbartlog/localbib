"""Router: PDF import — classic upload + the smart (SSE) pipeline.

Serves:
  POST /api/import/upload                       (synchronous classic import)
  POST /api/import/upload-smart                 (SSE stream, file from upload)
  POST /api/import/process-smart/{filename}     (SSE stream, file from INPUT_DIR)

Pure move from webapp.py (Backend-Modularisierung #87). HTTP contract (paths /
methods / shapes / status codes) is bit-identical to the previous webapp.py
handlers.

Duplicate resolved: ``/upload-smart`` and ``/process-smart`` used to carry two
near-word-for-word copies of a ~500-line pipeline. Both now build a
``target_path`` (one saves the upload to INPUT_DIR, the other validates an
existing INPUT_DIR file) and then call ONE shared generator,
``_run_smart_import``. The PURE metadata half of that flow lives in
``services.smart_import_pipeline`` (no DB/SSE/FS-mutation); this router keeps the
DB dedup LOOKUPS, the SSE framing, and all persistence
(add_paper / file copy / create_symlinks / abstract write / chunking).

Index-Hook (#153): Chunks + Paper-Embedding + Chunk-Embeddings entstehen ueber
den EINEN Aufruf ``import_indexing.index_paper_after_import`` — im Smart-Pfad
hier, im klassischen Pfad innerhalb von ``pipeline.process_paper`` (denselben
Hook teilen dadurch auch CLI-``import`` und Watcher).

Keyword note: this module's dotted path is ``routers.import`` and ``import`` is a
Python keyword, so ``from routers.import import router`` is a SyntaxError.
webapp.py mounts it via ``importlib.import_module("routers.import")``.

Shared resources come from context / literature_manager / the neutral
import_indexing + services.smart_import_pipeline modules. No import from webapp
or any other router.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

import bib_lookup
import collection_membership as membership
import library_events as events
import services.smart_import_pipeline as smart_import_pipeline
from context import get_conn
from literature_manager import (
    Config,
    Database,
    categorize_with_llm,
    compute_file_hash,
    create_symlinks,
    fetch_crossref_metadata,
    generate_filename,
    process_paper,
    unlock_pdf,
)
import metadata_validation
import paper_ingest
from filenames import stored_pdf_path, stored_pdf_target
from import_indexing import index_paper_after_import
from pipeline import restore_stored_pdf
from llm_client import llm_for
from paper_matcher import match as match_ref

router = APIRouter()

# Aliases so handler bodies relocated verbatim from webapp.py keep working.
_get_conn = get_conn
db = Database(Config.DB_PATH)


def _rematch_references_for_paper(paper_id: int):
    """Prueft alle ungematchten Referenzen ob sie zum neu importierten Paper passen.
    Wird nach jedem Paper-Import aufgerufen.

    Relocated from webapp.py together with the import handlers (#87). webapp.py
    re-exports this so its remaining import endpoints (process / process/{filename})
    keep calling the same implementation.
    """
    conn = _get_conn()
    try:
        if not conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone():
            return

        unmatched = conn.execute(
            "SELECT id, doi, title FROM paper_references WHERE matched_paper_id IS NULL"
        ).fetchall()

        matched_count = 0
        for ref in unmatched:
            result = match_ref({"doi": ref["doi"], "title": ref["title"]}, conn)
            if result.matched_paper_id == paper_id:
                conn.execute(
                    "UPDATE paper_references SET matched_paper_id = ?, match_confidence = ? WHERE id = ?",
                    (paper_id, result.match_confidence, ref["id"]),
                )
                matched_count += 1

        if matched_count > 0:
            conn.commit()
            logging.info(f"🔗 Rematch: {matched_count} Referenzen mit Paper {paper_id} verknuepft")
    except Exception as e:
        logging.warning(f"Rematch fehlgeschlagen fuer Paper {paper_id}: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Upload into the input folder (shared by both upload endpoints)
# ---------------------------------------------------------------------------

def _save_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Write an upload into the input folder under a name of its own; return
    ``(path, name)``.

    A taken name gets a counter (``_1``, ``_2`` …), whatever the file under it
    holds: every upload is its own copy. Two uploads of one file — one batch,
    or a retry while the first import still runs — must never share a path,
    or the first import removes the file under the second one (review
    2026-10-18). The name is claimed by an exclusive create, so not even a
    request between the check and the write can take it."""
    os.makedirs(Config.INPUT_DIR, exist_ok=True)
    base_name = os.path.basename(filename)
    stem, ext = os.path.splitext(base_name)
    safe_name, counter = base_name, 1
    while True:
        target_path = os.path.join(Config.INPUT_DIR, safe_name)
        if not os.path.lexists(target_path):
            try:
                with open(target_path, "xb") as f:
                    f.write(content)
                return target_path, safe_name
            except FileExistsError:
                pass
        safe_name = f"{stem}_{counter}{ext}"
        counter += 1


# ---------------------------------------------------------------------------
# Classic synchronous import
# ---------------------------------------------------------------------------

@router.post("/api/import/upload")
async def upload_and_import_pdf(file: UploadFile = File(...)):
    """Nimmt eine PDF-Datei per Upload entgegen und importiert sie direkt."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="error.only_pdf_accepted")

    # Datei in Input-Ordner speichern (Namenskollision: _1, _2 …)
    target_path, safe_name = _save_upload(file.filename, await file.read())

    # Direkt importieren. ``upload``: die Datei ist eine Kopie, das Original
    # liegt beim Nutzer — ein DOI-/Titel-Treffer auf einen Eintrag ohne PDF
    # entfernt sie wie jedes Duplikat, statt sie im Input-Ordner zu lassen.
    try:
        paper_id = process_paper(target_path, db, upload=True)
        if paper_id:
            # Rematch: Pruefen ob dieses Paper eine bekannte Referenz ist
            try:
                _rematch_references_for_paper(paper_id)
            except Exception as e:
                logging.warning(f"Rematch nach Upload fehlgeschlagen: {e}")
            # Chunks + Embeddings (Research-Chat / semantische Suche) erzeugt
            # ``process_paper`` selbst ueber ``import_indexing`` (#153) — damit
            # der klassische Web-Import, die CLI und der Watcher denselben
            # Index-Hook teilen statt ihn je Aufrufstelle zu wiederholen.
            return {"status": "ok", "paper_id": paper_id, "filename": safe_name}
        else:
            return {"status": "skipped", "reason": "Bereits in Datenbank oder Fehler", "filename": safe_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Smart pipeline (SSE) — ONE shared code path for both entry points
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _check_collection(collection_id: Optional[int]) -> None:
    """A target Collection (#203) must exist before the file is even saved."""
    if collection_id is None:
        return
    conn = _get_conn()
    try:
        membership.require(conn, collection_id)
    finally:
        conn.close()


def _assign_to_collection(collection_id: Optional[int], paper_id) -> Optional[dict]:
    """Put the imported — or the duplicate, already-present — Item into the
    target Collection. ``None`` without a target, so the frames of an upload
    without ``collection_id`` stay exactly what they were. Never raises: the
    Item is in the Library either way, a failed assignment is reported."""
    if collection_id is None or not paper_id:
        return None
    conn = _get_conn()
    try:
        found = membership.require(conn, collection_id)
        added = membership.add_items(conn, collection_id, [paper_id])
        conn.commit()
        if added["added_ids"]:
            events.collection_changed(events.MEMBERS_CHANGED, collection_id)
        return {"id": collection_id, "name": found["name"],
                "added": bool(added["added_ids"])}
    except Exception as e:
        logging.warning("Upload: Zuordnung zu Sammlung %s fehlgeschlagen: %s",
                        collection_id, e)
        return {"id": collection_id, "name": "", "added": False, "failed": True}
    finally:
        conn.close()


def _smart_import_response(target_path: str, original_filename: str, *,
                           uploaded: bool,
                           do_doi: bool, do_categories: bool,
                           do_abstract: bool, do_validate: bool,
                           collection_id: Optional[int] = None) -> StreamingResponse:
    """Build the SSE StreamingResponse for a smart import of ``target_path``.

    Both ``/upload-smart`` (file freshly written to INPUT_DIR) and
    ``/process-smart`` (file already in INPUT_DIR) funnel through here, so the
    pipeline exists exactly once.

    ``uploaded``: ``target_path`` is the copy of a browser upload, the user's
    original stays on their disk. "Never delete the only copy" does not hold
    for it — on a DOI/title match with an Item without a stored PDF it goes
    like any duplicate upload, where a file the user put in the input folder
    stays (review 2026-10-18).

    ``collection_id`` (#203): the target Collection of the upload dialog. The
    new Item lands in it, and so does an Item recognised as a duplicate —
    the Collection should be complete even when the Library already had the
    file. Frames gain a ``collection`` field only when a target was given.
    """

    def _with_collection(frame: dict, paper_id) -> dict:
        assigned = _assign_to_collection(collection_id, paper_id)
        if assigned is not None:
            frame["collection"] = assigned
        return frame

    async def generate():
        # -- Duplikat-Check (hash) --
        yield _sse({"type": "progress", "step": "hash", "message": "import.step.hash", "percent": 5})
        file_hash = compute_file_hash(target_path)
        if db.paper_exists(file_hash):
            # Find the existing paper for a helpful message
            dup_conn = _get_conn()
            try:
                dup_row = dup_conn.execute(
                    "SELECT id, title, filename FROM papers WHERE file_hash = ?", (file_hash,)
                ).fetchone()
            finally:
                dup_conn.close()
            duplicate_file = {"type": "error", "message": "import.step.duplicateFile"}
            if dup_row:
                duplicate_file.update(duplicate_paper_id=dup_row["id"],
                                      duplicate_title=dup_row["title"],
                                      duplicate_filename=dup_row["filename"])
            # Whether the row holds its PDF is the one rule
            # (``filenames.stored_pdf_path``), shared with ``process_paper``.
            if dup_row and stored_pdf_target(dup_row["filename"]) is None:
                # The row kept the hash but has no file (e.g. Diagnosis
                # "clear_pdf"): this input is the only copy. Attach it to that
                # row instead of deleting it.
                try:
                    await run_in_threadpool(paper_ingest.attach_pdf_from_path,
                                            dup_row["id"], target_path)
                except Exception as e:
                    logging.warning("Smart import: attaching to item %s failed: %s", dup_row["id"], e)
                    yield _sse(_with_collection(duplicate_file, dup_row["id"]))
                    return
                try:
                    os.remove(target_path)
                except OSError:
                    pass
                yield _sse(_with_collection({
                    "type": "error",
                    "message": "import.step.duplicateAttached",
                    "duplicate_paper_id": dup_row["id"],
                    "duplicate_title": dup_row["title"],
                    "attached": True,
                }, dup_row["id"]))
                return
            if dup_row and stored_pdf_path(dup_row["filename"]) is None:
                # The row names a file all/ does not hold (an earlier run
                # died between the two): this input is the only copy left —
                # restore it before the input is removed. If that fails, the
                # input stays.
                if not restore_stored_pdf(db, dup_row["id"], dup_row["filename"], target_path):
                    yield _sse(_with_collection(duplicate_file, dup_row["id"]))
                    return
            try:
                os.remove(target_path)
            except Exception:
                pass
            yield _sse(_with_collection(duplicate_file, dup_row["id"]) if dup_row
                       else duplicate_file)
            return

        # -- PDF-Schutz --
        if Config.UNLOCK_PDFS:
            yield _sse({"type": "progress", "step": "unlock", "message": "import.step.unlock", "percent": 8})
            unlock_pdf(target_path)

        # -- Pure metadata pipeline (text -> DOI/CrossRef -> fallbacks -> LLM validate) --
        pipeline = smart_import_pipeline.run_pipeline(
            target_path, original_filename, do_doi=do_doi, do_validate=do_validate,
        )
        result = None
        while True:
            try:
                event = next(pipeline)
            except StopIteration as stop:
                result = stop.value
                break
            yield _sse(event)

        text = result.text
        metadata = result.metadata
        # LLM steps that failed at the transport level (rate limit, key,
        # host): the pipeline reports validate, we add categories/abstract,
        # and the complete event ships the list so the UI can explain the
        # empty fields (a free-tier model rate-limited upstream is the
        # common case, not an app bug).
        llm_failures = list(result.llm_failures)

        # -- Duplikat-Check (DOI/Titel) --
        yield _sse({"type": "progress", "step": "dupcheck", "message": "import.step.dupcheck", "percent": 53})
        dup = db.find_duplicate_paper(doi=metadata.get("doi", ""), title=metadata.get("title", ""))
        if dup:
            frame = {
                "type": "error",
                "message": "import.step.duplicateItem",
                "duplicate_paper_id": dup["id"],
                "duplicate_title": dup.get("title", ""),
            }
            if stored_pdf_path(dup.get("filename")) is None:
                # A DOI/title match names the same work, not the same file:
                # only an Item that holds its PDF in all/ makes this input a
                # second copy. Without one (DOI intake, BibTeX import,
                # Diagnosis "clear_pdf") or with its file gone, a file the
                # user put in the input folder is the only copy: it stays and
                # is not attached automatically (only a hash match is). A
                # browser upload is a copy — its original stays on the user's
                # disk — so it goes like any duplicate upload. Either way the
                # message says how to attach the file by hand.
                no_file = not (dup.get("filename") or "").strip()
                frame["message"] = ("import.step.duplicateItemNoPdf" if no_file
                                    else "import.step.duplicateItemPdfMissing")
                frame["kept"] = not uploaded
            if frame.get("kept"):
                logging.warning("Smart import: item %s has no stored PDF, input kept: %s",
                                dup["id"], target_path)
            else:
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            yield _sse(_with_collection(frame, dup["id"]))
            return

        # -- Dateiname generieren & speichern --
        yield _sse({"type": "progress", "step": "save", "message": "import.step.save", "percent": 55})
        new_filename = generate_filename(metadata)
        target_all = os.path.join(Config.ALL_DIR, new_filename)
        c = 1
        while os.path.exists(target_all):
            nm, ext = os.path.splitext(new_filename)
            new_filename = f"{nm}_{c}{ext}"
            target_all = os.path.join(Config.ALL_DIR, new_filename)
            c += 1

        try:
            import fitz as _fitz
            _doc = _fitz.open(target_path)
            _page_count = len(_doc)
            _doc.close()
        except Exception:
            _page_count = 0

        paper_data = {
            "file_hash": file_hash,
            "filename": new_filename,
            "original_filename": original_filename,
            "title": metadata["title"],
            "authors": metadata["authors"],
            "year": metadata["year"],
            "doi": metadata["doi"],
            "isbn": metadata["isbn"],
            "abstract": metadata["abstract"][:5000] if metadata["abstract"] else "",
            "journal": metadata["journal"],
            "publisher": metadata["publisher"],
            "raw_metadata": json.dumps(metadata, ensure_ascii=False),
            "ocr_text": text[:10000],
            "page_count": _page_count,
            # The DOI record's bibliographic columns (type, volume, pages …).
            **bib_lookup.bib_columns(metadata),
        }
        # File first, then the DB row (same order as pipeline.process_paper):
        # a row without its file would make the next run treat the inbox PDF
        # as a "duplicate" and delete the only copy. A failed copy raises
        # before any row exists and leaves the inbox PDF in place; a failed
        # row removes the copy again.
        try:
            shutil.copy2(target_path, target_all)
        except Exception:
            try:
                os.remove(target_all)
            except OSError:
                pass
            raise
        try:
            paper_id = db.add_paper(paper_data)
        except Exception:
            try:
                os.remove(target_all)
            except OSError:
                pass
            raise
        yield _sse({"type": "progress", "step": "save", "message": {"code": "import.step.saved", "params": {"id": paper_id}}, "percent": 60})
        try:
            os.remove(target_path)
        except Exception:
            pass

        # -- Kategorisierung --
        assigned_categories = []
        if do_categories and Config.llm_ready("fast"):
            yield _sse({"type": "progress", "step": "categories", "message": "import.step.categorizing", "percent": 65})
            categories_json = db.get_categories()
            category_tree = db.get_category_tree()
            cat_llm = llm_for("categorize")
            assignments = categorize_with_llm(
                title=metadata["title"],
                abstract=metadata["abstract"],
                text_snippet=text[:2000],
                category_tree=category_tree,
                categories_json=categories_json,
                llm=cat_llm,
            )
            for a in assignments:
                cat_id = a.get("category_id")
                confidence = a.get("confidence", 0.0)
                if cat_id:
                    db.assign_category(paper_id, cat_id, confidence)
                    cat_name = next((c_item["name"] for c_item in categories_json if c_item["id"] == cat_id), "?")
                    assigned_categories.append({"id": cat_id, "name": cat_name, "confidence": confidence})

            if assigned_categories:
                cat_names = ", ".join(c_item["name"] for c_item in assigned_categories)
                yield _sse({"type": "progress", "step": "categories",
                            "message": {"code": "import.step.categories", "params": {"names": cat_names}}, "percent": 80})
            elif cat_llm.last_error is not None:
                failure = cat_llm.last_error.as_failure("categories")
                llm_failures.append(failure)
                yield _sse({"type": "progress", "step": "categories",
                            "message": smart_import_pipeline.llm_failure_message(failure), "percent": 80})
            else:
                yield _sse({"type": "progress", "step": "categories", "message": "import.noCategoriesAssigned", "percent": 80})
        else:
            yield _sse({"type": "progress", "step": "categories", "message": "import.step.categoriesSkipped", "percent": 80})

        # Category-folder entries only now: they are built from the assigned
        # categories (none existed before the step above). Never costs the import.
        try:
            create_symlinks(db, paper_id, new_filename)
        except Exception as e:
            logging.warning("Smart import: category folder entries failed for %s: %s", paper_id, e)

        # -- Abstract --
        abstract_source = ""
        if do_abstract and not metadata["abstract"]:
            yield _sse({"type": "progress", "step": "abstract", "message": "import.step.abstractSearch", "percent": 85})

            # CrossRef Abstract (falls DOI vorhanden und noch kein Abstract)
            if metadata["doi"]:
                try:
                    cr = fetch_crossref_metadata(metadata["doi"])
                    if cr and cr.get("abstract", "").strip():
                        metadata["abstract"] = cr["abstract"].strip()
                        abstract_source = "crossref"
                except Exception:
                    pass

            # LLM-Suche im PDF (metadata_validation owns prompt + KEIN_ABSTRACT sentinel)
            abstract_failure = None
            if not metadata["abstract"] and Config.llm_ready("fast") and text.strip():
                abs_llm = llm_for("abstract")
                yield _sse({"type": "progress", "step": "abstract", "message": "import.step.abstractInPdf", "percent": 88})
                found = metadata_validation.llm_find_abstract_in_text(
                    abs_llm,
                    metadata.get("title", ""), metadata.get("authors", ""), text[:6000],
                )
                if found:
                    metadata["abstract"] = found
                    abstract_source = "pdf"
                elif abs_llm.last_error is None:
                    # LLM generiert Zusammenfassung -- not after a transport
                    # failure: the second call would run into the same limit.
                    yield _sse({"type": "progress", "step": "abstract", "message": "import.step.abstractGenerate", "percent": 92})
                    generated = metadata_validation.llm_generate_abstract(
                        abs_llm,
                        metadata.get("title", ""), metadata.get("authors", ""), text[:6000],
                    )
                    if generated:
                        metadata["abstract"] = generated
                        abstract_source = "generated"
                if abs_llm.last_error is not None:
                    abstract_failure = abs_llm.last_error.as_failure("abstract")
                    llm_failures.append(abstract_failure)

            if metadata["abstract"]:
                conn_abs = _get_conn()
                try:
                    conn_abs.execute(
                        "UPDATE papers SET abstract = ?, abstract_source = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (metadata["abstract"][:5000], abstract_source, paper_id),
                    )
                    conn_abs.commit()
                finally:
                    conn_abs.close()
                yield _sse({"type": "progress", "step": "abstract",
                            "message": {"code": "import.step.abstractFound", "params": {"source": abstract_source}}, "percent": 95})
            elif abstract_failure is not None:
                yield _sse({"type": "progress", "step": "abstract",
                            "message": smart_import_pipeline.llm_failure_message(abstract_failure), "percent": 95})
            else:
                yield _sse({"type": "progress", "step": "abstract", "message": "import.step.abstractNotFound", "percent": 95})
        elif metadata["abstract"]:
            abstract_source = "crossref"
            yield _sse({"type": "progress", "step": "abstract",
                        "message": "import.step.abstractPresent", "percent": 95})
        else:
            yield _sse({"type": "progress", "step": "abstract", "message": "import.step.abstractSkipped", "percent": 95})

        # -- Rematch --
        try:
            _rematch_references_for_paper(paper_id)
        except Exception:
            pass

        # -- Abgeleitete Indizes: Chunks + Embeddings (#98/#102/#153) --------
        # EIN Hook fuer alle drei Indizes (paper_chunks, paper_embeddings,
        # chunk_embeddings); er wirft nie und degradiert jeden Schritt einzeln
        # (kein Modell / HTTP-Fehler), damit ein ausgefallenes Embedding-Gateway
        # den Import nicht blockiert (PRD Entscheidung 5) — Ausgefallenes ist
        # per Maintenance-Reindex nachholbar. Dieselbe Funktion nutzt
        # ``pipeline.process_paper`` fuer den klassischen Import.
        yield _sse({"type": "progress", "step": "chunking", "message": "import.step.chunking", "percent": 97})
        idx = index_paper_after_import(paper_id, target_all)
        if idx["chunks"] > 0:
            msg = f"{idx['chunks']} Chunks erstellt"
            if idx["chunk_vectors"] > 0:
                msg += f", {idx['chunk_vectors']} semantisch indexiert"
            yield _sse({"type": "progress", "step": "chunking", "message": msg, "percent": 98})

        # -- Fertig --
        yield _sse(_with_collection({
            "type": "complete",
            "paper_id": paper_id,
            "filename": new_filename,
            "title": metadata["title"],
            "authors": metadata["authors"],
            "year": metadata["year"],
            "doi": metadata["doi"],
            "isbn": metadata["isbn"],
            "abstract": (metadata["abstract"] or "")[:300],
            "abstract_source": abstract_source,
            "journal": metadata["journal"],
            "publisher": metadata["publisher"],
            "categories": assigned_categories,
            "llm_failures": llm_failures,
        }, paper_id))

    async def safe_generate():
        try:
            async for chunk in generate():
                yield chunk
        except Exception as e:
            logging.error(f"Smart-Import Fehler: {e}", exc_info=True)
            yield _sse({"type": "error", "message": {"code": "error.importFailed", "params": {"message": str(e)}}})

    return StreamingResponse(safe_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/import/upload-smart")
async def upload_smart(
    file: UploadFile = File(...),
    do_doi: bool = Query(True),
    do_categories: bool = Query(True),
    do_abstract: bool = Query(True),
    do_validate: bool = Query(True),
    collection_id: Optional[int] = Query(None),
):
    """Upload mit Schritt-fuer-Schritt Fortschritt via SSE.

    Query-Parameter steuern, welche Automatisierungen laufen:
    - do_doi: DOI suchen & CrossRef-Metadaten holen
    - do_categories: LLM-Kategorisierung
    - do_abstract: Abstract holen/generieren
    - do_validate: LLM-Metadaten-Validierung
    - collection_id: Zielsammlung (#203), auch fuer erkannte Dubletten
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="error.only_pdf_accepted")
    _check_collection(collection_id)

    # Datei in Input-Ordner speichern
    target_path, safe_name = _save_upload(file.filename, await file.read())

    return _smart_import_response(
        target_path, safe_name, uploaded=True,
        do_doi=do_doi, do_categories=do_categories,
        do_abstract=do_abstract, do_validate=do_validate,
        collection_id=collection_id,
    )


# ---------------------------------------------------------------------------
# Watch-folder import (INPUT_DIR batch / single) — relocated from webapp.py (#93)
# ---------------------------------------------------------------------------

@router.get("/api/import/pending")
async def list_pending_imports():
    """Listet PDF-Dateien im Input-Ordner die noch nicht importiert wurden."""
    input_dir = Config.INPUT_DIR
    if not os.path.exists(input_dir):
        return {"files": [], "count": 0}

    files = []
    for f in sorted(Path(input_dir).glob("*.pdf")):
        stat = f.stat()
        files.append({
            "filename": f.name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })

    return {"files": files, "count": len(files)}


@router.post("/api/import/process")
async def process_import():
    """Importiert alle PDFs aus dem Input-Ordner."""
    input_dir = Config.INPUT_DIR
    if not os.path.exists(input_dir):
        return {"processed": 0, "results": []}

    pdf_files = sorted(Path(input_dir).glob("*.pdf"))
    results = []

    for pdf_path in pdf_files:
        try:
            paper_id = process_paper(str(pdf_path), db)
            if paper_id:
                try:
                    _rematch_references_for_paper(paper_id)
                except Exception:
                    pass
                results.append({
                    "filename": pdf_path.name,
                    "status": "ok",
                    "paper_id": paper_id,
                })
            else:
                results.append({
                    "filename": pdf_path.name,
                    "status": "skipped",
                    "reason": "Bereits in Datenbank oder Fehler",
                })
        except Exception as e:
            results.append({
                "filename": pdf_path.name,
                "status": "error",
                "error": str(e),
            })

    return {"processed": len(results), "results": results}


@router.post("/api/import/process/{filename}")
async def process_single_import(filename: str):
    """Importiert eine einzelne PDF aus dem Input-Ordner."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(Config.INPUT_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail={"code": "error.file_not_in_input_folder", "params": {"name": safe_name}})

    try:
        paper_id = process_paper(filepath, db)
        if paper_id:
            try:
                _rematch_references_for_paper(paper_id)
            except Exception:
                pass
            return {"status": "ok", "paper_id": paper_id}
        else:
            return {"status": "skipped", "reason": "Bereits in Datenbank oder Fehler"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/import/process-smart/{filename}")
async def process_smart_from_input(
    filename: str,
    do_doi: bool = Query(True),
    do_categories: bool = Query(True),
    do_abstract: bool = Query(True),
    do_validate: bool = Query(True),
    collection_id: Optional[int] = Query(None),
):
    """Importiert eine Datei aus dem Input-Ordner mit dem Smart-Pipeline (SSE-Fortschritt).
    Identische Verarbeitung wie upload-smart, aber liest Datei aus dem Input-Ordner.
    Der Upload-Dialog startet auch diesen Weg, daher dieselbe optionale
    Zielsammlung; der automatische Watch-Ordner (``watcher``) bleibt davon unberuehrt."""
    safe_name = os.path.basename(filename)
    target_path = os.path.join(Config.INPUT_DIR, safe_name)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail={"code": "error.file_not_in_input_folder", "params": {"name": safe_name}})
    _check_collection(collection_id)

    return _smart_import_response(
        target_path, safe_name, uploaded=False,
        do_doi=do_doi, do_categories=do_categories,
        do_abstract=do_abstract, do_validate=do_validate,
        collection_id=collection_id,
    )
