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

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

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
from import_indexing import index_paper_after_import
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
# Classic synchronous import
# ---------------------------------------------------------------------------

@router.post("/api/import/upload")
async def upload_and_import_pdf(file: UploadFile = File(...)):
    """Nimmt eine PDF-Datei per Upload entgegen und importiert sie direkt."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="error.only_pdf_accepted")

    # Datei in Input-Ordner speichern
    os.makedirs(Config.INPUT_DIR, exist_ok=True)
    safe_name = os.path.basename(file.filename)
    target_path = os.path.join(Config.INPUT_DIR, safe_name)

    # Bei Namenskollision umbenennen
    counter = 1
    base_name = safe_name
    while os.path.exists(target_path):
        name, ext = os.path.splitext(base_name)
        safe_name = f"{name}_{counter}{ext}"
        target_path = os.path.join(Config.INPUT_DIR, safe_name)
        counter += 1

    content = await file.read()
    with open(target_path, "wb") as f:
        f.write(content)

    # Direkt importieren
    try:
        paper_id = process_paper(target_path, db)
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


def _smart_import_response(target_path: str, original_filename: str, *,
                           do_doi: bool, do_categories: bool,
                           do_abstract: bool, do_validate: bool) -> StreamingResponse:
    """Build the SSE StreamingResponse for a smart import of ``target_path``.

    Both ``/upload-smart`` (file freshly written to INPUT_DIR) and
    ``/process-smart`` (file already in INPUT_DIR) funnel through here, so the
    pipeline exists exactly once.
    """

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
            try:
                os.remove(target_path)
            except Exception:
                pass
            if dup_row:
                yield _sse({
                    "type": "error",
                    "message": "import.step.duplicateFile",
                    "duplicate_paper_id": dup_row["id"],
                    "duplicate_title": dup_row["title"],
                    "duplicate_filename": dup_row["filename"],
                })
            else:
                yield _sse({"type": "error", "message": "import.step.duplicateFile"})
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
            try:
                os.remove(target_path)
            except Exception:
                pass
            yield _sse({
                "type": "error",
                "message": "import.step.duplicateItem",
                "duplicate_paper_id": dup["id"],
                "duplicate_title": dup.get("title", ""),
            })
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
        }
        paper_id = db.add_paper(paper_data)
        yield _sse({"type": "progress", "step": "save", "message": {"code": "import.step.saved", "params": {"id": paper_id}}, "percent": 60})

        # Datei kopieren
        shutil.copy2(target_path, target_all)
        create_symlinks(db, paper_id, new_filename)
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
        yield _sse({
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
        })

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
):
    """Upload mit Schritt-fuer-Schritt Fortschritt via SSE.

    Query-Parameter steuern, welche Automatisierungen laufen:
    - do_doi: DOI suchen & CrossRef-Metadaten holen
    - do_categories: LLM-Kategorisierung
    - do_abstract: Abstract holen/generieren
    - do_validate: LLM-Metadaten-Validierung
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="error.only_pdf_accepted")

    # Datei in Input-Ordner speichern
    os.makedirs(Config.INPUT_DIR, exist_ok=True)
    safe_name = os.path.basename(file.filename)
    target_path = os.path.join(Config.INPUT_DIR, safe_name)

    counter = 1
    base_name = safe_name
    while os.path.exists(target_path):
        name, ext = os.path.splitext(base_name)
        safe_name = f"{name}_{counter}{ext}"
        target_path = os.path.join(Config.INPUT_DIR, safe_name)
        counter += 1

    content = await file.read()
    with open(target_path, "wb") as f:
        f.write(content)

    return _smart_import_response(
        target_path, safe_name,
        do_doi=do_doi, do_categories=do_categories,
        do_abstract=do_abstract, do_validate=do_validate,
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
):
    """Importiert eine Datei aus dem Input-Ordner mit dem Smart-Pipeline (SSE-Fortschritt).
    Identische Verarbeitung wie upload-smart, aber liest Datei aus dem Input-Ordner."""
    safe_name = os.path.basename(filename)
    target_path = os.path.join(Config.INPUT_DIR, safe_name)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail={"code": "error.file_not_in_input_folder", "params": {"name": safe_name}})

    return _smart_import_response(
        target_path, safe_name,
        do_doi=do_doi, do_categories=do_categories,
        do_abstract=do_abstract, do_validate=do_validate,
    )
