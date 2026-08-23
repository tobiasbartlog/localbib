#!/usr/bin/env python3
"""Datei-Management: Dateinamen-Generierung und Symlink-Erstellung."""

import os
import re
import sys
import shutil
import logging
from typing import Dict

from config import Config
from database import Database


def generate_filename(metadata: Dict) -> str:
    """Generiert standardisierten Dateinamen: YYYY_Nachname_Titel.pdf"""
    year = metadata.get("year", "XXXX") or "XXXX"
    authors = metadata.get("authors", "")

    # Ersten Nachnamen extrahieren
    if authors:
        first_author = authors.split(",")[0].strip()
    else:
        first_author = "Unknown"

    title = metadata.get("title", "Untitled")
    # Titel kürzen und bereinigen
    title = re.sub(r'[^\w\s-]', '', title)
    title_words = title.split()[:5]
    title_short = "_".join(title_words)

    # Sonderzeichen entfernen
    first_author = re.sub(r'[^\w]', '', first_author)
    title_short = re.sub(r'[^\w_]', '', title_short)

    filename = f"{year}_{first_author}_{title_short}.pdf"
    return filename


def create_symlinks(db: Database, paper_id: int, filename: str):
    """Erstellt Symlinks in Kategorie-Ordnern."""
    categories = db.get_paper_categories(paper_id)
    all_categories = db.get_categories()

    # Category ID -> Name Mapping
    cat_map = {c["id"]: c for c in all_categories}

    source_path = os.path.join(Config.ALL_DIR, filename)

    for cat_assignment in categories:
        cat_id = cat_assignment["id"]
        cat = cat_map.get(cat_id)
        if not cat:
            continue

        # Ordnerpfad aufbauen (mit Parent)
        path_parts = [cat["name"]]
        parent_id = cat.get("parent_id")
        while parent_id is not None:
            parent = cat_map.get(parent_id)
            if parent:
                path_parts.insert(0, parent["name"])
                parent_id = parent.get("parent_id")
            else:
                break

        cat_dir = os.path.join(Config.CATEGORIES_DIR, *path_parts)
        os.makedirs(cat_dir, exist_ok=True)

        link_path = os.path.join(cat_dir, filename)

        # Symlink erstellen (Windows: Verknüpfung / Junction)
        try:
            if os.path.exists(link_path) or os.path.islink(link_path):
                os.remove(link_path)

            if sys.platform == "win32":
                # Windows: Hardlink oder Shortcut
                # Symlinks brauchen Admin-Rechte, daher Hardlink
                try:
                    os.link(source_path, link_path)
                except OSError:
                    # Fallback: Shortcut via .lnk
                    _create_windows_shortcut(source_path, link_path)
            else:
                os.symlink(source_path, link_path)

            logging.info(f"  🔗 Verknüpfung: {os.path.join(*path_parts, filename)}")
        except Exception as e:
            logging.error(f"  ❌ Symlink-Fehler: {e}")


def _create_windows_shortcut(target: str, link_path: str):
    """Erstellt Windows .lnk Verknüpfung."""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(link_path.replace(".pdf", ".pdf.lnk"))
        shortcut.Targetpath = target
        shortcut.save()
    except ImportError:
        # Fallback: einfach kopieren (nicht ideal, aber funktioniert)
        logging.warning("  ⚠️  pywin32 nicht installiert. Erstelle Kopie statt Verknüpfung.")
        shutil.copy2(target, link_path)
