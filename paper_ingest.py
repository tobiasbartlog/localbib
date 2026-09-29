"""Neutral helper module: what happens to a paper created *without* a PDF.

Three steps every "Paper ohne PDF anlegen" path shares — the BibTeX import
(``routers/bibtex_import.py``) since ADR-0003 and the DOI intake
(``routers/paper_by_doi.py``, ADR-0016) since PRD #137:

1. ``enrich_imported_papers`` — non-destructive enrichment of fresh rows:
   abstract via CrossRef (OpenAlex fallback), missing year/authors via
   OpenAlex. Only empty fields are filled.
2. ``attach_pdf_to_paper`` — PDF bytes onto a paper without a file: real
   SHA256, file in ``ALL_DIR``, text extraction, symlinks, the post-import
   index hook (#153). Curated metadata stays untouched (CONTEXT.md "PDF
   Download"). ``attach_pdf_from_path`` is the same step for a file that is
   already on disk — what a migration (PRD #173) has instead of an upload.
3. ``categorize_attached_paper`` — the LLM category pass after a PDF landed.

Plus the two OA seams the DOI intake adds on top: ``fetch_openalex_works`` (one
batch lookup, swallowed errors) and ``try_fetch_oa_pdf`` (download + attach +
categorise, **never raises** — a failed OA attempt must not undo a creation),
and the DOI intake itself, ``intake_by_doi`` (dedup, ``add_paper``, those
two seams), shared by the REST handler and the Add-on host adapter
``host_services._CoreLibraryApi.create_by_doi`` so it exists only once.

Like ``pdf_chunking``/``import_indexing`` this is neither a router nor a
``services/`` module: it persists on the import path (the router handler is
still the one that decides *that* a paper is created), but does no HTTP
routing. It exists because a router never imports another router (ADR-0006,
``.importlinter``), and the alternative was a ~200-line copy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

from fastapi import HTTPException

import bibtex_import as bibtex_import_core
import ca_trust
import metadata_validation
from context import get_conn
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


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _oa_mailto() -> str:
    return Config.polite_mailto()


def normalize_doi(doi: str) -> str:
    """The one DOI identity of the intake paths: prefix-free and lowercased —
    the form OpenAlex keys its ``Work.doi`` by, so a lookup dict built from a
    works batch can be read with a caller's DOI directly."""
    return bibtex_import_core.normalize_doi(doi).lower()


def fetch_openalex_works(dois: list[str]) -> dict:
    """One OpenAlex batch for the given DOIs → ``{normalized doi: Work}``.
    Errors are logged and yield an empty dict: enrichment and the OA attempt
    are best-effort, never a reason for a request to fail."""
    wanted = [normalize_doi(d) for d in dois if (d or "").strip()]
    if not wanted:
        return {}
    try:
        return {w.doi: w for w in OpenAlexClient(_oa_mailto()).fetch_works_by_doi(wanted)}
    except Exception as e:
        logging.warning("OpenAlex-Batch fuer Import-Anreicherung fehlgeschlagen: %s", e)
        return {}


def enrich_imported_papers(created: list[dict], works: dict | None = None) -> int:
    """Nicht-destruktive Anreicherung frisch importierter Papers: Abstract via
    CrossRef (Fallback OpenAlex) sowie fehlende Jahr/Autoren via OpenAlex.
    Nur leere Felder werden gefuellt. Gibt die Zahl angereicherter Papers.

    ``created`` rows carry ``paper_id`` and ``doi``. ``works`` is an optional
    pre-fetched OpenAlex batch (``fetch_openalex_works``) so a caller that
    also needs the OA location pays for one lookup, not two."""
    if not created:
        return 0
    if works is None:
        works = fetch_openalex_works([r.get("doi") or "" for r in created])

    enriched = 0
    for r in created:
        pid = r.get("paper_id")
        if not pid:
            continue
        doi = (r.get("doi") or "").strip()
        work = works.get(normalize_doi(doi)) if doi else None
        conn = get_conn()
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
            conn = get_conn()
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


def attach_pdf_to_paper(paper_id: int, data: bytes, original_name: str,
                        do_chunks: bool = True) -> dict:
    """Haengt PDF-Bytes an ein Paper ohne Datei: echter SHA256 als file_hash,
    Datei in ALL_DIR, Textextraktion + Seitenzahl, Symlinks. Die kuratierten
    Metadaten bleiben unangetastet (CONTEXT.md "PDF Download"); die
    LLM-Kategorisierung macht der Aufrufer."""
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="Datei ist kein PDF")

    conn = get_conn()
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

    conn = get_conn()
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


def attach_pdf_from_path(paper_id: int, path: str, do_chunks: bool = True) -> dict:
    """``attach_pdf_to_paper`` for a file that is already on disk.

    The migration paths (PRD #173) do not receive an upload — they read a local
    PDF the old library pointed at. Everything else is identical, index hook
    included; the source file is read, never moved, so a failed run leaves the
    reader's own folder exactly as it was.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    return attach_pdf_to_paper(paper_id, data, os.path.basename(path), do_chunks=do_chunks)


def categorize_attached_paper(paper_id: int, title: str, abstract: str, text: str) -> list:
    """LLM-Kategorisierung nach PDF-Anhang (Entscheidung: Anhaengen + Kategorisierung)."""
    if not Config.llm_ready("fast"):
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
            conn = get_conn()
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


def try_fetch_oa_pdf(paper_id: int, url: str) -> bool:
    """Best-effort Open-Access download for a freshly created paper: fetch
    ``url``, verify it is a PDF, attach it, run the category pass. ``True``
    when a PDF now hangs on the paper, ``False`` otherwise — and it **never
    raises**: the intake's contract (ADR-0016) is that a failed OA attempt
    leaves the creation standing and reports ``pdf=none``."""
    url = (url or "").strip()
    if not url:
        return False
    try:
        import httpx
        # httpx does not read REQUESTS_CA_BUNDLE — pass the merged store
        # explicitly (CLAUDE.md ca_trust: ``ca_bundle()`` at every call site).
        resp = httpx.get(
            url, timeout=60, follow_redirects=True,
            headers={"User-Agent": Config.user_agent("LocalBib")},
            verify=ca_trust.ca_bundle() or True,
        )
        resp.raise_for_status()
        data = resp.content
        if not data[:1024].lstrip().startswith(b"%PDF"):
            logging.info("OA-URL fuer Paper %s lieferte kein PDF: %s", paper_id, url)
            return False
        result = attach_pdf_to_paper(
            paper_id, data, os.path.basename(url.split("?")[0]) or "oa.pdf")
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT title, abstract FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
        finally:
            conn.close()
        if row:
            categorize_attached_paper(paper_id, row["title"], row["abstract"], result["text"])
        return True
    except Exception as e:
        logging.warning("OA-PDF-Versuch fuer Paper %s fehlgeschlagen: %s", paper_id, e)
        return False


# ---------------------------------------------------------------------------
# Paper per DOI (ADR-0016): the one intake both callers share
# ---------------------------------------------------------------------------
#
# Two transports reach it: the REST handler ``routers/paper_by_doi.py`` (an
# Add-on frontend, the SPA) and the host adapter
# ``host_services._CoreLibraryApi.create_by_doi`` (an Add-on's Python, gated by
# ``library.write``). Both are the write point for their transport and decide
# *that* a paper is created; the flow itself exists exactly once, here.

DOI_SOURCE = "doi"  # default ``papers.import_source`` of the DOI intake


def _join_authors(authors) -> str:
    if authors is None:
        return ""
    if isinstance(authors, str):
        return authors.strip()
    return "; ".join(a.strip() for a in authors if (a or "").strip())


def _find_by_doi(doi: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, filename, cite_key FROM papers WHERE LOWER(TRIM(doi)) = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _cite_key_of(paper_id: int) -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT cite_key FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return (row["cite_key"] or "") if row else ""
    finally:
        conn.close()


def _pdf_state(has_pdf: bool) -> str:
    return "fetched" if has_pdf else "none"


def create_or_find_by_doi(item: dict) -> dict:
    """Dedup + ``add_paper`` for one item; no network. ``item`` holds ``doi``
    and optionally ``title``, ``authors`` (list or "; "-joined string),
    ``year``, ``journal``, ``abstract``, ``source``. Raises ``ValueError`` on
    an empty DOI so a batch can report it per item."""
    doi = normalize_doi(item.get("doi") or "")
    if not doi:
        raise ValueError("DOI darf nicht leer sein.")
    existing = _find_by_doi(doi)
    if existing:
        return {"doi": doi, "created": False, "paper_id": existing["id"],
                "citekey": existing["cite_key"] or "",
                "pdf": _pdf_state(bool(existing["filename"]))}
    source = (item.get("source") or "").strip() or DOI_SOURCE
    paper_data = {
        # No file yet: a stable, DOI-derived hash keeps the column unique
        # until a PDF replaces it with the real SHA256.
        "file_hash": hashlib.sha256(f"doi:{doi}".encode("utf-8")).hexdigest(),
        "filename": "",
        "original_filename": f"DOI-Import: {doi}",
        "title": (item.get("title") or "").strip(),
        "authors": _join_authors(item.get("authors")),
        "year": item.get("year"),
        "doi": doi,
        "isbn": "",
        "abstract": (item.get("abstract") or "")[:5000],
        "journal": (item.get("journal") or "").strip(),
        "publisher": "",
        "raw_metadata": json.dumps({"source": source, "doi": doi}, ensure_ascii=False),
        "ocr_text": "",
        "cite_key": "",  # generated in add_paper
        "import_source": source,
    }
    paper_id = _get_db().add_paper(paper_data)
    return {"doi": doi, "created": True, "paper_id": paper_id,
            "citekey": _cite_key_of(paper_id), "pdf": "none"}


def intake_by_doi(items: list[dict]) -> list[dict]:
    """The whole DOI intake for a list: create/find each, then one OpenAlex
    batch for enrichment + OA location, then the best-effort PDF per new
    paper. One result per item, in order: ``{doi, created, paper_id, citekey,
    pdf}`` plus ``error`` for an item that could not be created (then
    ``paper_id`` is ``None``). Never raises for a single item."""
    results: list[dict] = []
    for item in items:
        try:
            results.append(create_or_find_by_doi(item))
        except Exception as e:
            logging.warning("Paper per DOI anlegen fehlgeschlagen (%s): %s", item.get("doi"), e)
            results.append({"doi": (item.get("doi") or "").strip(), "created": False,
                            "paper_id": None, "citekey": "", "pdf": "none", "error": str(e)})
    created = [r for r in results if r["created"]]
    if not created:
        return results
    works = fetch_openalex_works([r["doi"] for r in created])
    enrich_imported_papers(created, works=works)
    for r in created:
        work = works.get(r["doi"])
        url = work.oa_pdf_url if work else ""
        if url and try_fetch_oa_pdf(r["paper_id"], url):
            r["pdf"] = "fetched"
    return results
