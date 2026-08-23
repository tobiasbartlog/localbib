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
from filenames import generate_filename, create_symlinks


def process_paper(filepath: str, db: Database) -> Optional[int]:
    """
    Verarbeitet ein einzelnes PDF:
    1. Hash prüfen (Duplikat?)
    2. Text extrahieren (OCR)
    3. DOI suchen → CrossRef Metadaten
    4. LLM-Kategorisierung
    5. In DB speichern
    6. Datei umbenennen + in "all" kopieren
    7. Symlinks erstellen
    8. Abgeleitete Indizes: Chunks + Embeddings (Research-Chat/semantische Suche)

    Gibt paper_id zurück oder None bei Fehler.
    """
    original_filename = os.path.basename(filepath)
    logging.info(f"\n{'='*60}")
    logging.info(f"📄 Verarbeite: {original_filename}")
    logging.info(f"{'='*60}")

    # 1. Duplikat-Check
    file_hash = compute_file_hash(filepath)
    if db.paper_exists(file_hash):
        logging.info(f"⏭️  Bereits in Datenbank (Hash: {file_hash[:12]}...)")
        # Datei aus Input-Ordner entfernen, damit sie nicht immer wieder auftaucht
        try:
            os.remove(filepath)
            logging.info(f"🗑️  Duplikat-Datei aus Input entfernt: {original_filename}")
        except Exception as e:
            logging.warning(f"⚠️  Konnte Duplikat-Datei nicht entfernen: {e}")
        return None

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

    paper_id = db.add_paper(paper_data)
    logging.info(f"💾 Paper gespeichert (ID: {paper_id})")

    # Kategorien zuweisen
    for assignment in assignments:
        cat_id = assignment.get("category_id")
        confidence = assignment.get("confidence", 0.0)
        if cat_id:
            db.assign_category(paper_id, cat_id, confidence)
            cat_name = next(
                (c["name"] for c in categories_json if c["id"] == cat_id), "?"
            )
            logging.info(f"  🏷️  → {cat_name} (Konfidenz: {confidence:.0%})")

    # 7. Datei kopieren
    shutil.copy2(filepath, target_path)
    logging.info(f"📁 Kopiert nach: all/{new_filename}")

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
