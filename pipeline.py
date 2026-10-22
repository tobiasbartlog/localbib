#!/usr/bin/env python3
"""Paper-Verarbeitungs-Pipeline: process_paper."""

import os
import json
import shutil
import logging
from typing import Optional

from config import Config
from database import Database
from pdf_text import compute_file_hash, extract_text_from_pdf, unlock_pdf
from doi_discovery import discover_doi, extract_isbn_from_text, extract_year_from_text
from crossref import fetch_crossref_metadata
from llm_categorize import categorize_with_llm
from filenames import generate_filename, create_symlinks, stored_pdf_path, stored_pdf_target


def _has_stored_file(paper: dict) -> bool:
    """True when the Item's PDF is in all/ — only then is an input that
    matches it a second copy that may be removed. The one rule
    (``filenames.stored_pdf_path``) the smart import applies too."""
    return stored_pdf_path(paper.get("filename")) is not None


def restore_stored_pdf(db: Database, paper_id: int, filename: str, source: str) -> bool:
    """Puts ``source`` back under the Item's file name in all/, for a row whose
    stored PDF is gone (an earlier run died between the copy and the row, or
    the file was removed by hand). ``True`` once the file is there again: the
    input that matched it may go then.

    ``False`` when it cannot be restored: something that is not the PDF holds
    the name (a folder — never copy into or over it), or the copy fails; a torn
    copy is removed again, or the next run would take it for the stored PDF
    and delete the input. The caller keeps the input then, it is the only copy.

    Shared by ``process_paper`` and the smart import (``routers/import.py``),
    which both judge "stored" by ``filenames.stored_pdf_path``.
    """
    target = stored_pdf_target(filename)
    if target is None or os.path.lexists(target):
        logging.warning(f"⚠️  all/{os.path.basename(filename or '')} ist keine Datei — "
                        f"nicht wiederhergestellt, Input bleibt liegen")
        return False
    try:
        shutil.copy2(source, target)
    except Exception as e:
        logging.warning(f"⚠️  Fehlende Datei nicht wiederhergestellt ({e}) — Input bleibt liegen")
        try:
            os.remove(target)
        except OSError:
            pass
        return False
    try:
        create_symlinks(db, paper_id, os.path.basename(target))
    except Exception as e:
        logging.warning(f"⚠️  Verknüpfungen nach Wiederherstellung fehlgeschlagen: {e}")
    logging.info(f"🩹 Fehlende Datei wiederhergestellt: all/{os.path.basename(target)}")
    return True


def _attach_to_item(paper_id: int, filepath: str) -> bool:
    """Attaches the input PDF to an Item without a file (``paper_ingest``:
    file in all/, real hash, text, symlinks, index hook). ``False`` when that
    fails — the caller then keeps the input, it is the only copy.

    ``paper_ingest`` writes through ``Config.DB_PATH``, the one DB every caller
    of ``process_paper`` passes. Import LAZY, for the same reason as
    ``import_indexing`` below: paper_ingest hangs on literature_manager (the
    facade that imports this module), and a CLI run without the web
    dependencies only loses the attach, never the input.
    """
    try:
        from paper_ingest import attach_pdf_from_path

        result = attach_pdf_from_path(paper_id, filepath)
    except Exception as e:
        logging.warning(
            f"⚠️  PDF nicht an Eintrag {paper_id} angehängt, Input bleibt liegen: {e}"
        )
        return False
    logging.info(f"📎 An bestehenden Eintrag angehängt (ID: {paper_id}): all/{result['filename']}")
    return True


def process_paper(filepath: str, db: Database, *, upload: bool = False) -> Optional[int]:
    """
    Verarbeitet ein einzelnes PDF:
    1. Hash prüfen (Duplikat?)
    2. Text extrahieren (OCR)
    3. DOI suchen → CrossRef Metadaten
    4. LLM-Kategorisierung
    5. Datei umbenennen + in "all" kopieren
    6. In DB speichern (erst nach der Kopie, s. u.)
    7. Kategorien zuweisen, Symlinks erstellen
    8. Abgeleitete Indizes: Chunks + Embeddings (Research-Chat/semantische Suche)

    Gibt die paper_id zurück — auch die des Eintrags ohne Datei, an den ein
    Hash-Treffer das PDF angehängt hat (es liegt dann in der Bibliothek) —,
    sonst None (Duplikat, behaltener Input).

    ``upload``: ``filepath`` ist die Kopie eines Browser-Uploads, das Original
    liegt weiter beim Nutzer. Sie wird bei einem DOI-/Titel-Treffer auf einen
    Eintrag ohne gespeichertes PDF entfernt wie jedes Duplikat, statt als
    "einzige Kopie" im Input-Ordner zu warten (wie der Smart-Upload).
    """
    original_filename = os.path.basename(filepath)
    logging.info(f"\n{'='*60}")
    logging.info(f"📄 Verarbeite: {original_filename}")
    logging.info(f"{'='*60}")

    # 1. Duplikat-Check
    file_hash = compute_file_hash(filepath)
    existing = db.find_duplicate_paper(file_hash=file_hash)
    if existing:
        logging.info(f"⏭️  Bereits in Datenbank (Hash: {file_hash[:12]}...)")
        attached_to = None
        if stored_pdf_target(existing.get("filename")) is None:
            # The row kept the hash but has no file (Diagnosis "clear_pdf"):
            # this input is the only copy. Attach it to that row, as the smart
            # import does; if that fails the input stays where it is.
            if not _attach_to_item(existing["id"], filepath):
                return None
            attached_to = existing["id"]
        elif stored_pdf_path(existing["filename"]) is None:
            # The row names a file all/ does not hold (an earlier run died
            # between the two): this input is the only copy left — restore it
            # before the input is removed, never delete it unrestored.
            if not restore_stored_pdf(db, existing["id"], existing["filename"], filepath):
                return None
        # Datei aus Input-Ordner entfernen, damit sie nicht immer wieder auftaucht
        try:
            os.remove(filepath)
            logging.info(f"🗑️  Duplikat-Datei aus Input entfernt: {original_filename}")
        except Exception as e:
            logging.warning(f"⚠️  Konnte Duplikat-Datei nicht entfernen: {e}")
        # An attached PDF is in the library now: the Item's id, so the callers
        # report it as imported. A mere duplicate stays None.
        return attached_to

    # 1b. PDF-Schutz entfernen (wenn aktiviert)
    if Config.UNLOCK_PDFS:
        unlock_pdf(filepath)

    # 2. Text extrahieren
    logging.info("📖 Extrahiere Text aus PDF...")
    text = extract_text_from_pdf(filepath, Config.MAX_OCR_PAGES)
    if not text.strip():
        logging.warning("⚠️  Kein Text extrahiert. PDF könnte gescannt sein.")

    logging.info(f"   {len(text)} Zeichen extrahiert")

    # 3. DOI suchen und Metadaten holen
    metadata = {
        "title": "",
        "authors": "",
        "year": None,
        "doi": "",
        "isbn": "",
        "abstract": "",
        "journal": "",
        "publisher": "",
    }

    doi = discover_doi(text, metadata["title"], metadata["authors"])
    if doi:
        logging.info(f"🔍 DOI gefunden: {doi}")
        metadata["doi"] = doi
        crossref_data = fetch_crossref_metadata(doi)
        if crossref_data:
            metadata.update(crossref_data)
            logging.info(f"   ✅ CrossRef: {metadata['title'][:80]}...")
        else:
            logging.warning("   ⚠️  CrossRef lieferte keine Daten für diese DOI")
    else:
        logging.info("🔍 Keine DOI gefunden. Extrahiere Metadaten aus Text...")

    # Fallback: ISBN
    if not metadata["isbn"]:
        isbn = extract_isbn_from_text(text)
        if isbn:
            metadata["isbn"] = isbn
            logging.info(f"   ISBN gefunden: {isbn}")

    # Fallback: Jahr aus Text
    if not metadata["year"]:
        year = extract_year_from_text(text)
        if year:
            metadata["year"] = year
            logging.info(f"   Jahr aus Text: {year}")

    # Fallback: Titel aus Dateiname
    if not metadata["title"]:
        # Versuche Titel aus erstem Text zu nehmen
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            metadata["title"] = lines[0][:200]
        else:
            metadata["title"] = original_filename.replace(".pdf", "")

    # 3b. Duplikat-Check (DOI + Titel)
    dup = db.find_duplicate_paper(doi=metadata.get("doi", ""), title=metadata.get("title", ""))
    if dup:
        logging.info(f"⏭️  Duplikat erkannt (DOI/Titel): '{dup.get('title', '')}' (ID: {dup['id']})")
        # Only a stored file makes the input a second copy. An Item without PDF
        # (DOI intake, BibTeX import, Diagnosis "clear_pdf") or whose file in
        # all/ is gone leaves the input as the only copy: it stays in the input
        # folder. It is not attached — a DOI/title match names the same work,
        # not the same file, and the smart import attaches only on a hash match.
        # A browser upload is a copy (its original stays on the user's disk),
        # so it goes like any duplicate.
        if not _has_stored_file(dup):
            if not upload:
                logging.warning(
                    f"⚠️  Eintrag {dup['id']} hat kein PDF in all/ — Input bleibt liegen: "
                    f"{original_filename} (im Eintrag per „PDF anhängen“ zuordnen)"
                )
                return None
            logging.info(f"   Eintrag {dup['id']} hat kein PDF in all/ — Upload-Kopie wird "
                         f"entfernt, das Original liegt beim Nutzer")
        try:
            os.remove(filepath)
            logging.info(f"🗑️  Duplikat-Datei entfernt: {original_filename}")
        except Exception as e:
            logging.warning(f"⚠️  Konnte Duplikat-Datei nicht entfernen: {e}")
        return None

    # 4. LLM-Kategorisierung
    logging.info("🤖 LLM-Kategorisierung...")
    categories_json = db.get_categories()
    category_tree = db.get_category_tree()

    assignments = categorize_with_llm(
        title=metadata["title"],
        abstract=metadata["abstract"],
        text_snippet=text[:2000],
        category_tree=category_tree,
        categories_json=categories_json
    )

    # 5. Dateiname generieren
    new_filename = generate_filename(metadata)
    # Duplikat-Dateiname vermeiden
    target_path = os.path.join(Config.ALL_DIR, new_filename)
    counter = 1
    while os.path.exists(target_path):
        name, ext = os.path.splitext(new_filename)
        new_filename = f"{name}_{counter}{ext}"
        target_path = os.path.join(Config.ALL_DIR, new_filename)
        counter += 1

    # 6. In DB speichern
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
    }

    # Datei zuerst, dann die DB-Zeile: eine Zeile ohne Datei liesse den
    # naechsten Lauf das Input-PDF als "Duplikat" loeschen. Scheitert die
    # Zeile, wird die Kopie wieder entfernt und das Input bleibt liegen.
    shutil.copy2(filepath, target_path)
    logging.info(f"📁 Kopiert nach: all/{new_filename}")
    try:
        paper_id = db.add_paper(paper_data)
    except Exception:
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise
    logging.info(f"💾 Paper gespeichert (ID: {paper_id})")

    # 7. Kategorien zuweisen — ein Fehler hier kostet nie den Import selbst
    for assignment in assignments:
        cat_id = assignment.get("category_id")
        confidence = assignment.get("confidence", 0.0)
        if not cat_id:
            continue
        try:
            db.assign_category(paper_id, cat_id, confidence)
        except Exception as e:
            logging.warning(f"⚠️  Kategorie {cat_id} nicht zugewiesen: {e}")
            continue
        cat_name = next(
            (c["name"] for c in categories_json if c["id"] == cat_id), "?"
        )
        logging.info(f"  🏷️  → {cat_name} (Konfidenz: {confidence:.0%})")

    # 8. Symlinks erstellen
    create_symlinks(db, paper_id, new_filename)

    # 9. Abgeleitete Indizes (#153): Research-Chat-Chunks + semantische Vektoren.
    # Import LAZY: literature_manager (die Re-Export-Fassade) importiert dieses
    # Modul, und import_indexing haengt ueber embedding_index/context wieder an
    # literature_manager — ein Top-Level-Import waere damit zirkulaer. Zur
    # Aufrufzeit sind alle Module fertig geladen.
    # Der Hook wirft nie; dieser try/except deckt nur den Importfehler ab (z. B.
    # ein CLI-Lauf ohne installierte Web-Abhaengigkeiten).
    try:
        from import_indexing import index_paper_after_import

        idx = index_paper_after_import(paper_id, target_path)
        if idx["chunks"]:
            logging.info(f"🧩 {idx['chunks']} Chunks, {idx['chunk_vectors']} neue Chunk-Vektoren")
    except Exception as e:
        logging.warning(f"⚠️  Indexierung nach Import fehlgeschlagen: {e}")

    # 10. Original aus Input löschen
    try:
        os.remove(filepath)
        logging.info(f"🗑️  Input-Datei entfernt: {original_filename}")
    except Exception as e:
        logging.warning(f"⚠️  Konnte Input-Datei nicht löschen: {e}")

    logging.info(f"✅ Fertig: {new_filename}")
    return paper_id
