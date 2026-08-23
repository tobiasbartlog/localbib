#!/usr/bin/env python3
"""
Literatur-Manager für Dissertationen
=====================================
Automatisches Tagging, Kategorisierung und Verwaltung von wissenschaftlichen PDFs.

Features:
- Watchdog: Überwacht Input-Ordner auf neue PDFs
- DOI-Extraktion + CrossRef API für Metadaten
- OCR-Fallback mit pymupdf
- LLM-Kategorisierung über RWTH-GPT (KI Connect NRW)
- SQLite-Datenbank
- Automatische Symlinks in Kategorie-Ordnern

Autor: Tobi Bartlog, ICOM RWTH Aachen

------------------------------------------------------------------------------
Re-Export-Fassade (#94)
------------------------------------------------------------------------------
Die Innereien dieses Moduls wurden in fokussierte Sub-Module zerlegt
(``config``, ``database``, ``pdf_text``, ``doi_discovery``, ``crossref``,
``llm_categorize``, ``filenames``, ``pipeline``, ``watcher``, ``cli``).

``literature_manager`` bleibt eine **statische** Re-Export-Fassade, sodass alle
bestehenden Importeure (``from literature_manager import Config, Database, …``)
und der CLI-Entry-Point (``python literature_manager.py …``) unverändert
funktionieren. Die Importe sind bewusst statisch/explizit gehalten, damit die
PyInstaller-Analyse (``localbib.spec``) ihnen folgen kann.
"""

from config import Config, setup_logging
from database import Database
from pdf_text import (
    compute_file_hash,
    extract_text_from_pdf,
    unlock_pdf,
    ocr_pdf,
    ocr_pdf_searchable,
)
from doi_discovery import (
    extract_doi_from_text,
    extract_year_from_text,
    extract_isbn_from_text,
    extract_arxiv_id_from_text,
    resolve_doi_from_arxiv,
    search_crossref_for_doi,
    search_openalex_for_doi,
    discover_doi,
)
from crossref import fetch_crossref_metadata
from llm_categorize import categorize_with_llm
from filenames import generate_filename, create_symlinks, _create_windows_shortcut
from pipeline import process_paper
from watcher import PDFHandler, start_watcher, WATCHDOG_AVAILABLE
from cli import (
    cmd_init,
    cmd_add_category,
    cmd_list_categories,
    cmd_import,
    cmd_watch,
    cmd_list_papers,
    cmd_search,
    cmd_rebuild,
    cmd_export_bibtex,
    cmd_stats,
    _rebuild_category_folders,
    _rebuild_all_symlinks,
    main,
)

__all__ = [
    # config
    "Config",
    "setup_logging",
    # database
    "Database",
    # pdf_text
    "compute_file_hash",
    "extract_text_from_pdf",
    "unlock_pdf",
    "ocr_pdf",
    "ocr_pdf_searchable",
    # doi_discovery
    "extract_doi_from_text",
    "extract_year_from_text",
    "extract_isbn_from_text",
    "extract_arxiv_id_from_text",
    "resolve_doi_from_arxiv",
    "search_crossref_for_doi",
    "search_openalex_for_doi",
    "discover_doi",
    # crossref
    "fetch_crossref_metadata",
    # llm_categorize
    "categorize_with_llm",
    # filenames
    "generate_filename",
    "create_symlinks",
    "_create_windows_shortcut",
    # pipeline
    "process_paper",
    # watcher
    "PDFHandler",
    "start_watcher",
    "WATCHDOG_AVAILABLE",
    # cli
    "cmd_init",
    "cmd_add_category",
    "cmd_list_categories",
    "cmd_import",
    "cmd_watch",
    "cmd_list_papers",
    "cmd_search",
    "cmd_rebuild",
    "cmd_export_bibtex",
    "cmd_stats",
    "_rebuild_category_folders",
    "_rebuild_all_symlinks",
    "main",
]


if __name__ == "__main__":
    main()
