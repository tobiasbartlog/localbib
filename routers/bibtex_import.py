"""Router: BibTeX import (preview, commit, oa-check) and PDF attach / fetch.

Serves:
  POST /api/import/bibtex/preview
  POST /api/import/bibtex/commit
  POST /api/import/bibtex/oa-check
  POST /api/papers/{paper_id}/attach-pdf
  POST /api/papers/{paper_id}/attach-finalize
  POST /api/papers/{paper_id}/fetch-oa-pdf

Pure move from webapp.py (Backend-Modularisierung #84). No behaviour change.
Output is byte-identical to the previous webapp.py handlers.

Naming note: the top-level pure module ``bibtex_import.py`` is imported here as
``bibtex_import_core`` to avoid shadowing this router module (routers/bibtex_import.py).
Shared state (Config, Database) comes from literature_manager; raw sqlite3
connections come from context.get_conn.  No import from webapp.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

# Import the top-level pure module under an alias so the name does not shadow
# this router module (which Python knows as routers.bibtex_import).
import bibtex_import as bibtex_import_core  # noqa: E402

import metadata_validation
from cite_key_generator import dedupe as _dedupe_cite_key
from context import get_conn
# Chunking trio relocated to the neutral top-level module pdf_chunking.py (#87)
# so the smart-import router can reuse it without a router-to-router import.
from pdf_chunking import chunk_paper_to_db, _chunk_text, _extract_pages_text  # noqa: F401
# Post-Import-Index-Hook (#153): chunken + Paper-/Chunk-Embeddings in einem Aufruf.
from import_indexing import index_paper_after_import
from literature_manager import (
    Config,
    Database,
    categorize_with_llm,
    create_symlinks,
    extract_text_from_pdf,
    fetch_crossref_metadata,
    generate_filename,
    unlock_pdf,
)
from openalex_client import OpenAlexClient
from paper_matcher import match as match_ref

router = APIRouter()

# Alias so copied code from webapp.py works without modification.
_get_conn = get_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _oa_mailto() -> str:
    return Config.polite_mailto()


# ---------------------------------------------------------------------------
# Pydantic models (bibtex-import domain)
# ---------------------------------------------------------------------------

class BibtexCommitEntry(BaseModel):
    key: str
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: str = ""
    isbn: str = ""
    abstract: str = ""
    journal: str = ""
    publisher: str = ""
    entry_type: str = "misc"
    matched_paper_id: Optional[int] = None
    raw: dict = {}


class BibtexCommitRequest(BaseModel):
    entries: List[BibtexCommitEntry]
    source_name: str = ""  # concrete origin (the uploaded .bib filename), stored as import_source


class OaCheckItem(BaseModel):
    paper_id: int
    doi: str = ""
    title: str = ""


class OaCheckRequest(BaseModel):
    items: List[OaCheckItem]


class FetchOaPdfRequest(BaseModel):
    url: str = ""  # leer → OA-URL via OpenAlex anhand der Paper-DOI suchen


class AttachFinalizeRequest(BaseModel):
    trim: dict | None = None          # {start_page, end_page, keep_cover} oder None
    do_categories: bool = True
    do_chunks: bool = True


# ---------------------------------------------------------------------------
# BibTeX-import helpers
# ---------------------------------------------------------------------------

def _enrich_imported_papers(created: list[dict]) -> int:
    """Nicht-destruktive Anreicherung frisch importierter Papers: Abstract via
    CrossRef (Fallback OpenAlex) sowie fehlende Jahr/Autoren via OpenAlex.
    Nur leere Felder werden gefuellt. Gibt die Zahl angereicherter Papers."""
    if not created:
        return 0
    # OpenAlex-Works im Batch (DOI) fuer Jahr/Autoren (+ ggf. Abstract-Fallback).
    works: dict = {}
    dois = [bibtex_import_core.normalize_doi(r["doi"]).lower()
            for r in created if (r.get("doi") or "").strip()]
    if dois:
        try:
            for w in OpenAlexClient(_oa_mailto()).fetch_works_by_doi(dois):
                works[w.doi] = w
        except Exception as e:
            logging.warning("OpenAlex-Batch fuer Import-Anreicherung fehlgeschlagen: %s", e)

    enriched = 0
    for r in created:
        pid = r.get("paper_id")
        if not pid:
            continue
        doi = (r.get("doi") or "").strip()
        doi_norm = bibtex_import_core.normalize_doi(doi).lower() if doi else ""
        work = works.get(doi_norm)
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT abstract, year, authors FROM papers WHERE id = ?", (pid,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            continue
        updates: dict = {}
        if not (row["abstract"] or "").strip():
            abstract = ""
            if doi:
                try:
                    abstract = metadata_validation.fetch_crossref_abstract(
                        doi, fetch=lambda d: fetch_crossref_metadata(d)
                    ) or ""
                except Exception:
                    abstract = ""
            if not abstract and work and (work.abstract or "").strip():
                abstract = work.abstract
            if abstract:
                updates["abstract"] = abstract[:5000]
        if work:
            if not row["year"] and work.year:
                updates["year"] = work.year
            if not (row["authors"] or "").strip() and work.authors:
                updates["authors"] = "; ".join(work.authors)
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn = _get_conn()
            try:
                conn.execute(
                    f"UPDATE papers SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (*updates.values(), pid),
                )
                conn.commit()
            finally:
                conn.close()
            enriched += 1
    return enriched


# ---------------------------------------------------------------------------
# PDF-attach helpers
# ---------------------------------------------------------------------------

def _attach_pdf_to_paper(paper_id: int, data: bytes, original_name: str,
                         do_chunks: bool = True) -> dict:
    """Haengt PDF-Bytes an ein Paper ohne Datei: echter SHA256 als file_hash,
    Datei in ALL_DIR, Textextraktion + Seitenzahl, Symlinks. Die kuratierten
    Metadaten bleiben unangetastet (CONTEXT.md "PDF Download"); die
    LLM-Kategorisierung macht der Aufrufer."""
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="Datei ist kein PDF")

    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        if row["filename"]:
            raise HTTPException(status_code=409, detail="Paper hat bereits ein PDF")
        real_hash = hashlib.sha256(data).hexdigest()
        other = conn.execute(
            "SELECT id, title FROM papers WHERE file_hash = ? AND id != ?",
            (real_hash, paper_id),
        ).fetchone()
        if other:
            raise HTTPException(
                status_code=409,
                detail=f"Dieses PDF gehoert bereits zu Paper {other['id']}: {other['title']}",
            )
        paper = dict(row)
    finally:
        conn.close()

    new_filename = generate_filename(
        {"title": paper["title"], "authors": paper["authors"], "year": paper["year"]}
    )
    target = os.path.join(Config.ALL_DIR, new_filename)
    c = 1
    while os.path.exists(target):
        nm, ext = os.path.splitext(new_filename)
        new_filename = f"{nm}_{c}{ext}"
        target = os.path.join(Config.ALL_DIR, new_filename)
        c += 1
    with open(target, "wb") as f:
        f.write(data)

    if Config.UNLOCK_PDFS:
        try:
            unlock_pdf(target)
        except Exception:
            pass

    text = ""
    page_count = 0
    try:
        text = extract_text_from_pdf(target, Config.MAX_OCR_PAGES)
    except Exception as e:
        logging.warning("Textextraktion beim PDF-Anhaengen fehlgeschlagen: %s", e)
    try:
        import fitz as _fitz
        _doc = _fitz.open(target)
        page_count = len(_doc)
        _doc.close()
    except Exception:
        pass

    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE papers SET file_hash = ?, filename = ?, original_filename = ?,
               ocr_text = ?, page_count = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (real_hash, new_filename, original_name or new_filename,
             text[:10000], page_count, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    create_symlinks(_get_db(), paper_id, new_filename)

    # Research-Chat: ein angehaengtes/geladenes PDF muss gechunkt werden, sonst
    # taucht es nicht im RAG auf (gleiches Verhalten wie der normale Import).
    # Seit #153 derselbe Hook wie im Import-Pfad, damit auch die Embeddings
    # (Paper- UND Chunk-Ebene) sofort entstehen statt erst beim Maintenance-Reindex.
    n_chunks = 0
    if do_chunks:
        n_chunks = index_paper_after_import(paper_id, target)["chunks"]

    return {"paper_id": paper_id, "filename": new_filename,
            "page_count": page_count, "chunks": n_chunks, "text": text}


def _categorize_attached_paper(paper_id: int, title: str, abstract: str, text: str) -> list:
    """LLM-Kategorisierung nach PDF-Anhang (Entscheidung: Anhaengen + Kategorisierung)."""
    if not Config.KICONNECT_API_KEY:
        return []
    assigned = []
    try:
        _db = _get_db()
        categories_json = _db.get_categories()
        category_tree = _db.get_category_tree()
        assignments = categorize_with_llm(
            title=title, abstract=abstract or "", text_snippet=text[:2000],
            category_tree=category_tree, categories_json=categories_json,
        )
        for a in assignments:
            cat_id = a.get("category_id")
            if cat_id:
                _db.assign_category(paper_id, cat_id, a.get("confidence", 0.0))
                cat_name = next(
                    (c["name"] for c in categories_json if c["id"] == cat_id), "?"
                )
                assigned.append({"id": cat_id, "name": cat_name})
        if assigned:
            # Symlinks erneut, jetzt mit Kategorie-Ordnern
            conn = _get_conn()
            try:
                fname = conn.execute(
                    "SELECT filename FROM papers WHERE id = ?", (paper_id,)
                ).fetchone()["filename"]
            finally:
                conn.close()
            create_symlinks(_db, paper_id, fname)
    except Exception as e:
        logging.warning("Kategorisierung nach PDF-Anhang fehlgeschlagen: %s", e)
    return assigned


def _parse_printed_range(pages_field: str):
    """'153--165' / '153-165' -> ('153', '165', span) oder (None, None, None)."""
    import re as _re
    nums = _re.findall(r"\d+", pages_field or "")
    if len(nums) >= 2:
        start, end = nums[0], nums[-1]
        try:
            span = int(end) - int(start) + 1
        except ValueError:
            span = None
        return start, end, span
    return None, None, None


def detect_book_structure(pdf_path: str, title: str, pages_field: str = "") -> dict | None:
    """Erkennt, ob ein angehaengtes PDF ein ganzes Buch ist (statt nur des
    Kapitels) und schlaegt den Kapitel-Seitenbereich vor.

    Rueckgabe:
      * None                         -> kein Buch, normaler Ablauf
      * {detected, range: {...}}     -> Buch + sicherer Bereich (Vorschau)
      * {detected, range: None}      -> Buch, aber Bereich unklar (manuell)

    Seitenangaben in der Rueckgabe sind 1-basierte PDF-Seiten. Pure read-only:
    kein DB-Write, keine FS-Mutation.
    """
    import fitz
    from rapidfuzz import fuzz

    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc)
        toc = doc.get_toc() or []
        l1 = [(p, t) for (lvl, t, p) in toc if lvl == 1]  # p = 1-basierte PDF-Seite
        printed_start, printed_end, span = _parse_printed_range(pages_field)

        is_book = page_count >= 60 and (
            len(l1) >= 4 or (span is not None and page_count >= 2.5 * span)
        )
        if not is_book:
            return None

        base = {
            "detected": True,
            "page_count": page_count,
            "printed_start": printed_start,
            "printed_end": printed_end,
        }

        # 1) TOC-Titelabgleich (liefert PDF-Seiten direkt). TOC-Titel enthalten
        # oft geschuetzte Leerzeichen (\xa0) -> Whitespace normalisieren.
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip().lower()

        start_idx = end_idx = None
        method = None
        toc_score = 0
        if title and l1:
            tnorm = _norm(title)
            scored = [
                (fuzz.token_set_ratio(tnorm, _norm(t)), i, p)
                for i, (p, t) in enumerate(l1)
            ]
            toc_score, bi, bp = max(scored, key=lambda x: x[0])
            if toc_score >= 80:
                s = l1[bi][0]
                e = l1[bi + 1][0] - 1 if bi + 1 < len(l1) else page_count
                start_idx, end_idx, method = s - 1, e - 1, "toc"

        # 2) Seitenlabels aus dem gedruckten Bereich (Cross-Check / Fallback)
        label_start = label_end = None
        if printed_start and printed_end:
            seen = {}
            for k in range(page_count):
                try:
                    lab = doc[k].get_label()
                except Exception:
                    lab = ""
                if lab and lab not in seen:
                    seen[lab] = k
            if printed_start in seen and printed_end in seen:
                label_start, label_end = seen[printed_start], seen[printed_end]

        confidence = "low"
        if method == "toc" and label_start is not None:
            if abs(label_start - start_idx) <= 1 and abs(label_end - end_idx) <= 1:
                method, confidence = "toc+labels", "high"
            else:
                confidence = "medium"
        elif method == "toc":
            confidence = "high" if toc_score >= 90 else "medium"
        elif label_start is not None and label_end >= label_start:
            start_idx, end_idx, method, confidence = label_start, label_end, "labels", "medium"

        if start_idx is None or end_idx is None or end_idx < start_idx:
            return {**base, "range": None}

        return {
            **base,
            "range": {
                "start_page": start_idx + 1,
                "end_page": end_idx + 1,
                "keep_cover": True,
            },
            "chapter_pages": end_idx - start_idx + 1,
            "method": method,
            "confidence": confidence,
        }
    finally:
        doc.close()


def _trim_pdf_file(path: str, start_page: int, end_page: int, keep_cover: bool = True) -> int:
    """Schneidet das PDF auf [start_page, end_page] (1-basierte PDF-Seiten) zu;
    das Cover (Seite 1) bleibt bei keep_cover erhalten. Ueberschreibt die Datei.
    Gibt die Seitenzahl des Ergebnisses zurueck."""
    import fitz
    src = fitz.open(path)
    try:
        n = len(src)
        s = max(1, int(start_page))
        e = min(n, int(end_page))
        if e < s:
            raise HTTPException(status_code=422, detail="Ungueltiger Seitenbereich")
        keep = []
        if keep_cover and s > 1:
            keep.append(0)  # Cover = PDF-Index 0
        keep.extend(range(s - 1, e))
        seen = set()
        ordered = [i for i in keep if not (i in seen or seen.add(i))]
        out = fitz.open()
        try:
            for i in ordered:
                out.insert_pdf(src, from_page=i, to_page=i)
            tmp = path + ".trim.pdf"
            out.save(tmp)
        finally:
            out.close()
    finally:
        src.close()
    os.replace(tmp, path)
    return len(ordered)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.post("/api/import/bibtex/preview")
async def bibtex_import_preview(
    bib_file: UploadFile = File(...),
    tex_file: Optional[UploadFile] = File(None),
):
    """Parst eine .bib (optional gefiltert auf in der .tex zitierte Keys) und
    matcht jeden Eintrag gegen die Bibliothek. Schreibt nichts."""
    bib_text = (await bib_file.read()).decode("utf-8", errors="replace")
    try:
        entries = bibtex_import_core.parse_bib(bib_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"BibTeX nicht lesbar: {e}")

    cited_keys = None
    if tex_file is not None:
        tex_text = (await tex_file.read()).decode("utf-8", errors="replace")
        cited_keys = bibtex_import_core.extract_cited_keys(tex_text)

    conn = _get_conn()
    try:
        existing_keys = {
            r[0] for r in conn.execute("SELECT cite_key FROM papers WHERE cite_key != ''")
        }
        proposed_keys: set = set()
        results = []
        for entry in entries:
            m = match_ref({"doi": entry["doi"], "title": entry["title"]}, conn)
            match_info = None
            if m.matched_paper_id is not None:
                row = conn.execute(
                    "SELECT id, title, cite_key, filename FROM papers WHERE id = ?",
                    (m.matched_paper_id,),
                ).fetchone()
                if row:
                    match_info = {
                        "paper_id": row["id"],
                        "title": row["title"],
                        "cite_key": row["cite_key"],
                        "has_pdf": bool(row["filename"]),
                        "strategy": m.match_strategy,
                        "confidence": m.match_confidence,
                    }
            if match_info:
                # Bestand gewinnt: the library paper keeps its Cite Key.
                proposed = match_info["cite_key"]
            else:
                proposed = _dedupe_cite_key(entry["key"], existing_keys | proposed_keys)
                proposed_keys.add(proposed)
            results.append({
                **{k: v for k, v in entry.items() if k != "raw"},
                "raw": entry["raw"],
                "cited": (entry["key"] in cited_keys) if cited_keys is not None else None,
                "match": match_info,
                "proposed_cite_key": proposed,
            })
    finally:
        conn.close()

    return {
        "total": len(results),
        "cited_count": sum(1 for r in results if r["cited"]) if cited_keys is not None else None,
        "matched_count": sum(1 for r in results if r["match"]),
        "entries": results,
    }


@router.post("/api/import/bibtex/commit")
async def bibtex_import_commit(payload: BibtexCommitRequest):
    """Legt die ausgewaehlten Eintraege als Papers ohne PDF an (Muster:
    RIS-Import). Gematchte Eintraege werden nicht dupliziert; zurueck kommt
    pro Eintrag der Cite Key fuer die refs der Veroeffentlichung."""
    results = []
    for entry in payload.entries:
        try:
            # Already matched in preview → reuse the library paper.
            if entry.matched_paper_id is not None:
                conn = _get_conn()
                try:
                    row = conn.execute(
                        "SELECT id, cite_key, filename, doi FROM papers WHERE id = ?",
                        (entry.matched_paper_id,),
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    results.append({
                        "key": entry.key, "status": "matched",
                        "paper_id": row["id"], "cite_key": row["cite_key"],
                        "doi": row["doi"] or entry.doi, "has_pdf": bool(row["filename"]),
                    })
                    continue
                # Matched paper vanished in the meantime — fall through to create.

            # Safety net against duplicates created since the preview.
            hash_input = f"{entry.title}_{entry.authors}".encode("utf-8")
            file_hash = hashlib.sha256(hash_input).hexdigest()
            _db = _get_db()
            duplicate = _db.find_duplicate_paper(
                doi=entry.doi, title=entry.title, file_hash=file_hash
            )
            if duplicate:
                conn = _get_conn()
                try:
                    row = conn.execute(
                        "SELECT id, cite_key, filename, doi FROM papers WHERE id = ?",
                        (duplicate["id"],),
                    ).fetchone()
                finally:
                    conn.close()
                results.append({
                    "key": entry.key, "status": "matched",
                    "paper_id": row["id"], "cite_key": row["cite_key"],
                    "doi": row["doi"] or entry.doi, "has_pdf": bool(row["filename"]),
                })
                continue

            paper_data = {
                "file_hash": file_hash,
                "filename": "",
                "original_filename": f"BibTeX-Import: {entry.key}",
                "title": entry.title,
                "authors": entry.authors,
                "year": entry.year,
                "doi": entry.doi,
                "isbn": entry.isbn,
                "abstract": (entry.abstract or "")[:5000],
                "journal": entry.journal,
                "publisher": entry.publisher,
                "raw_metadata": json.dumps(entry.raw, ensure_ascii=False, default=str),
                "ocr_text": "",
                "cite_key": entry.key,  # de-dupliziert in add_paper
                "import_source": payload.source_name or "",
            }
            paper_id = _db.add_paper(paper_data)
            conn = _get_conn()
            try:
                cite_key = conn.execute(
                    "SELECT cite_key FROM papers WHERE id = ?", (paper_id,)
                ).fetchone()["cite_key"]
            finally:
                conn.close()
            results.append({
                "key": entry.key, "status": "created",
                "paper_id": paper_id, "cite_key": cite_key,
                "doi": entry.doi, "has_pdf": False,
            })
        except Exception as e:
            results.append({"key": entry.key, "status": "error", "error": str(e)})

    # Frisch angelegte Eintraege nicht-destruktiv anreichern (Abstract + fehlende
    # Metadaten), damit Importe nicht "nackt" ohne Abstract in der Bibliothek landen.
    enriched = _enrich_imported_papers([r for r in results if r["status"] == "created"])

    return {
        "total": len(payload.entries),
        "created": sum(1 for r in results if r["status"] == "created"),
        "matched": sum(1 for r in results if r["status"] == "matched"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "enriched": enriched,
        "results": results,
    }


@router.post("/api/import/bibtex/oa-check")
async def bibtex_oa_check(payload: OaCheckRequest):
    """Prueft pro Paper, ob OpenAlex eine Open-Access-PDF-URL kennt (DOI-Batch,
    Titel-Fallback fuer DOI-lose Eintraege). Liefert ausserdem die manuellen
    Fallback-Links (DOI-Resolver, Google Scholar)."""
    from urllib.parse import quote_plus

    oa = OpenAlexClient(_oa_mailto())
    dois = [it.doi for it in payload.items if (it.doi or "").strip()]
    works_by_doi: dict = {}
    if dois:
        for work in oa.fetch_works_by_doi(dois):
            works_by_doi[work.doi] = work

    results = []
    for item in payload.items:
        doi_norm = bibtex_import_core.normalize_doi(item.doi).lower()
        work = works_by_doi.get(doi_norm) if doi_norm else None
        if work is None and not doi_norm and (item.title or "").strip():
            work = oa.fetch_work_by_title(item.title)
        results.append({
            "paper_id": item.paper_id,
            "oa_pdf_url": work.oa_pdf_url if work else "",
            "oa_landing_url": work.oa_landing_url if work else "",
            "doi_url": f"https://doi.org/{doi_norm}" if doi_norm else "",
            "scholar_url": "https://scholar.google.com/scholar?q="
                           + quote_plus((item.title or doi_norm or "").strip()),
        })
    return {"results": results}


@router.post("/api/papers/{paper_id}/attach-pdf")
async def attach_pdf(
    paper_id: int,
    file: UploadFile = File(...),
    do_categories: bool = Query(True),
    do_chunks: bool = Query(True),
    detect_book: bool = Query(False),
):
    """Haengt ein manuell heruntergeladenes PDF an ein Paper ohne Datei
    (Fallback des BibTeX-Imports, aber auch solo nutzbar).

    do_categories/do_chunks schalten die optionalen Nachverarbeitungsschritte;
    OCR und Referenz-Extraktion ziehen die jeweiligen Endpoints nach.
    detect_book=true (zweiphasiger Upload): Datei landen, ohne zu verarbeiten,
    und eine Buch-Erkennung (`book`) zurueckgeben; Verarbeitung + optionales
    Zuschneiden uebernimmt danach /attach-finalize."""
    data = await file.read()
    result = _attach_pdf_to_paper(paper_id, data, file.filename or "", do_chunks=do_chunks)
    text = result.pop("text")

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT title, abstract, raw_metadata FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
    finally:
        conn.close()

    book = None
    if detect_book:
        try:
            pages_field = ""
            if row["raw_metadata"]:
                pages_field = (json.loads(row["raw_metadata"]) or {}).get("pages", "") or ""
            book = detect_book_structure(
                os.path.join(Config.ALL_DIR, result["filename"]), row["title"], pages_field
            )
        except Exception as e:
            logging.warning("Buch-Erkennung beim PDF-Anhaengen fehlgeschlagen: %s", e)

    categories = []
    if do_categories:
        categories = _categorize_attached_paper(paper_id, row["title"], row["abstract"], text)
    return {**result, "categories": categories, "book": book}


@router.post("/api/papers/{paper_id}/attach-finalize")
async def attach_finalize(paper_id: int, payload: AttachFinalizeRequest):
    """Schliesst den zweiphasigen Upload ab: optionales Zuschneiden eines Buches
    auf das Kapitel (Cover bleibt), danach Kategorisierung/Chunking gemaess Wahl.
    OCR und Referenzen zieht das Frontend ueber die eigenen Endpoints nach."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()
    if not paper["filename"]:
        raise HTTPException(status_code=409, detail="Paper hat kein PDF")

    path = os.path.join(Config.ALL_DIR, os.path.basename(paper["filename"]))
    trimmed_pages = None
    text = paper["ocr_text"] or ""

    if payload.trim:
        t = payload.trim
        _trim_pdf_file(
            path, t.get("start_page"), t.get("end_page"), bool(t.get("keep_cover", True))
        )
        # Datei hat sich geaendert: Hash, Text, Seitenzahl neu bestimmen.
        with open(path, "rb") as f:
            new_hash = hashlib.sha256(f.read()).hexdigest()
        try:
            text = extract_text_from_pdf(path, Config.MAX_OCR_PAGES) or ""
        except Exception:
            text = ""
        try:
            import fitz as _fitz
            _doc = _fitz.open(path)
            trimmed_pages = len(_doc)
            _doc.close()
        except Exception:
            trimmed_pages = None
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE papers SET file_hash = ?, ocr_text = ?, page_count = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_hash, text[:10000], trimmed_pages, paper_id),
            )
            conn.commit()
        finally:
            conn.close()
        create_symlinks(_get_db(), paper_id, paper["filename"])

    n_chunks = 0
    if payload.do_chunks:
        # Chunks + Embeddings in einem Hook (#153) — siehe _attach_pdf_to_paper.
        n_chunks = index_paper_after_import(paper_id, path)["chunks"]

    categories = []
    if payload.do_categories:
        categories = _categorize_attached_paper(paper_id, paper["title"], paper["abstract"], text)

    return {
        "paper_id": paper_id,
        "trimmed_pages": trimmed_pages,
        "chunks": n_chunks,
        "categories": categories,
    }


@router.post("/api/papers/{paper_id}/fetch-oa-pdf")
async def fetch_oa_pdf(paper_id: int, payload: FetchOaPdfRequest):
    """Laedt das Open-Access-PDF eines Papers herunter und haengt es an.
    URL kommt vom oa-check (oder wird hier per DOI nachgeschlagen)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper nicht gefunden")
        paper = dict(row)
    finally:
        conn.close()

    url = (payload.url or "").strip()
    if not url:
        oa = OpenAlexClient(_oa_mailto())
        work = None
        if paper["doi"]:
            works = oa.fetch_works_by_doi([paper["doi"]])
            work = works[0] if works else None
        if work is None and paper["title"]:
            work = oa.fetch_work_by_title(paper["title"])
        url = work.oa_pdf_url if work else ""
    if not url:
        raise HTTPException(status_code=404, detail="Keine Open-Access-PDF-URL gefunden")

    try:
        import httpx
        resp = httpx.get(
            url, timeout=60, follow_redirects=True,
            headers={"User-Agent": Config.user_agent("LocalBib")},
        )
        resp.raise_for_status()
        data = resp.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Download fehlgeschlagen: {e}")

    content_type = ""
    try:
        content_type = resp.headers.get("content-type", "")
    except Exception:
        pass
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise HTTPException(
            status_code=422,
            detail=f"URL lieferte kein PDF (Content-Type: {content_type or 'unbekannt'})",
        )

    result = _attach_pdf_to_paper(paper_id, data, os.path.basename(url.split("?")[0]) or "oa.pdf")
    categories = _categorize_attached_paper(
        paper_id, paper["title"], paper["abstract"], result.pop("text")
    )
    return {**result, "categories": categories, "source_url": url}
