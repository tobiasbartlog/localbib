#!/usr/bin/env python3
"""Datei-Management: Dateinamen-Generierung und die Eintraege der Kategorie-Ordner.

Ein Eintrag in einem Kategorie-Ordner zeigt auf die PDF in ``Config.ALL_DIR``.
Wie er dort liegt, bestimmt der Link-Modus (``Config.LINK_MODE``, #213):

* ``hardlink`` - harter Link; scheitert er (anderes Laufwerk, Dateisystem ohne
  harte Links), liegt dort eine Verknuepfung (Windows: ``.lnk``, sonst ein
  symbolischer Link) und der Rueckfall wird gezaehlt.
* ``symlink``  - "Verknuepfung": unter Windows eine ``.lnk``-Datei (echte
  symbolische Links braucht dort Adminrechte), sonst ``os.symlink``.
* ``copy``     - eine Kopie der PDF.

Ohne Eintrag in der ``.env`` gilt unter Windows ``hardlink`` und sonst
``symlink`` - genau das Verhalten von vor der Einstellung.
"""

from __future__ import annotations

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


def stored_pdf_target(filename: str) -> str | None:
    """Wohin die gespeicherte PDF eines Eintrags gehoert — ob sie da ist oder
    nicht; ``None`` fuer einen Eintrag ohne Dateinamen. Dorthin stellt ein
    Import eine fehlende PDF wieder her."""
    if not (filename or "").strip():
        return None
    return os.path.join(Config.ALL_DIR, os.path.basename(filename))


def stored_pdf_path(filename: str) -> str | None:
    """Pfad der gespeicherten PDF eines Eintrags — oder ``None``, wenn keine da ist.

    Eintraege ohne PDF (BibTeX-/DOI-Import) haben ``filename == ""``; ein
    ``os.path.join(ALL_DIR, "")`` waere dann der Ordner selbst, und
    ``os.path.exists`` ist fuer einen Ordner wahr. Daher ``isfile``."""
    path = stored_pdf_target(filename)
    return path if path is not None and os.path.isfile(path) else None


def _category_path_parts(cat: dict, cat_map: dict) -> list:
    """Ordnerpfad einer Kategorie (mit ihren Eltern) als Liste von Namen."""
    path_parts = [cat["name"]]
    parent_id = cat.get("parent_id")
    while parent_id is not None:
        parent = cat_map.get(parent_id)
        if not parent:
            break
        path_parts.insert(0, parent["name"])
        parent_id = parent.get("parent_id")
    return path_parts


def _remove_entry(link_path: str) -> None:
    """Entfernt einen alten Eintrag - in jeder Form, die ein Modus hinterlaesst
    (Link/Kopie unter dem Namen selbst, Verknuepfung mit ``.lnk``), damit ein
    Moduswechsel keine Doppelten stehen laesst."""
    for path in (link_path, link_path + ".lnk"):
        if os.path.islink(path) or os.path.isfile(path):
            os.remove(path)


def _place_shortcut(source: str, link_path: str) -> str:
    """Die Verknuepfung: unter Windows eine ``.lnk``-Datei, sonst ein
    symbolischer Link. Gibt zurueck, was entstanden ist."""
    if sys.platform == "win32":
        return _create_windows_shortcut(source, link_path)
    os.symlink(source, link_path)
    return "symlink"


def place_entry(source: str, link_path: str, mode: str) -> tuple:
    """Legt EINEN Eintrag ``link_path`` fuer die PDF ``source`` an.

    Returns ``(art, rueckfall)`` - ``art`` ist ``hardlink``, ``symlink``,
    ``shortcut`` oder ``copy``; ``rueckfall`` ist wahr, wenn nicht die Art des
    Modus entstanden ist. Wirft ``OSError``, wenn gar nichts angelegt werden
    konnte. Ein unbekannter Modus gilt als ``symlink``.
    """
    if mode == "copy":
        shutil.copy2(source, link_path)
        return "copy", False
    if mode == "hardlink":
        try:
            os.link(source, link_path)
            return "hardlink", False
        except OSError as e:
            # Anderes Laufwerk oder ein Dateisystem ohne harte Links: dann
            # wenigstens eine Verknuepfung, wie es unter Windows immer war.
            logging.info("Harter Link nicht moeglich (%s), lege Verknuepfung an: %s", e, link_path)
            return _place_shortcut(source, link_path), True
    kind = _place_shortcut(source, link_path)
    return kind, kind == "copy"


def create_symlinks(db: Database, paper_id: int, filename: str, mode: str | None = None) -> dict:
    """Legt die Eintraege eines Papers in seinen Kategorie-Ordnern an.

    ``mode`` ist der Link-Modus, ohne Angabe der eingestellte
    (``Config.LINK_MODE``). Ein einzelner Fehlschlag wird geloggt und gezaehlt,
    nie geworfen. Returns ``{"placed": n, "fallbacks": n, "failed": n}``.
    """
    result = {"placed": 0, "fallbacks": 0, "failed": 0}
    source_path = stored_pdf_path(filename)
    if source_path is None:
        # Ohne Datei gibt es nichts zu verknuepfen. Eine fehlende Datei zeigt
        # die Diagnose (Einstellungen) an, statt hier pro Kategorie zu scheitern.
        if filename:
            logging.debug("Keine Verknuepfung fuer Paper %s: %s fehlt", paper_id, filename)
        return result

    mode = mode or Config.LINK_MODE
    name = os.path.basename(filename)
    categories = db.get_paper_categories(paper_id)
    cat_map = {c["id"]: c for c in db.get_categories()}

    for cat_assignment in categories:
        cat = cat_map.get(cat_assignment["id"])
        if not cat:
            continue
        path_parts = _category_path_parts(cat, cat_map)
        link_path = os.path.join(Config.CATEGORIES_DIR, *path_parts, name)
        try:
            os.makedirs(os.path.dirname(link_path), exist_ok=True)
            _remove_entry(link_path)
            kind, fell_back = place_entry(source_path, link_path, mode)
            result["placed"] += 1
            if fell_back:
                result["fallbacks"] += 1
            logging.info(f"  🔗 Verknüpfung ({kind}): {os.path.join(*path_parts, name)}")
        except Exception as e:
            result["failed"] += 1
            logging.error(f"  ❌ Symlink-Fehler: {e}")
    return result


def clear_category_folders() -> int:
    """Leert ``Config.CATEGORIES_DIR``: entfernt jeden Eintrag und danach die
    leeren Unterordner (veraltete Kategorien). Der Ordner selbst bleibt.

    Beruehrt nie ``Config.ALL_DIR`` - auch dann nicht, wenn der (falsch
    eingerichtet) innerhalb der Kategorie-Ordner laege. Ein harter Link oder
    eine Verknuepfung zu entfernen laesst die PDF selbst unberuehrt.
    Returns die Zahl der entfernten Eintraege.
    """
    root_dir = Config.CATEGORIES_DIR
    if not root_dir or not os.path.isdir(root_dir):
        return 0
    protected = os.path.normcase(os.path.abspath(Config.ALL_DIR)) if Config.ALL_DIR else None

    def _protected(path: str) -> bool:
        if not protected:
            return False
        p = os.path.normcase(os.path.abspath(path))
        return p == protected or p.startswith(protected + os.sep)

    removed = 0
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not _protected(os.path.join(root, d))]
        for fname in files:
            try:
                os.remove(os.path.join(root, fname))
                removed += 1
            except OSError as e:
                logging.warning(f"Konnte Datei nicht entfernen: {fname}: {e}")
    for root, _dirs, _files in os.walk(root_dir, topdown=False):
        if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(root_dir)) or _protected(root):
            continue
        try:
            os.rmdir(root)  # schlaegt fehl, wenn der Ordner nicht leer ist - gewollt
        except OSError:
            pass
    return removed


def _create_windows_shortcut(target: str, link_path: str) -> str:
    """Erstellt eine Windows-``.lnk``-Verknuepfung neben ``link_path``.

    Ohne pywin32 entsteht eine Kopie unter ``link_path`` (nicht ideal, aber
    sie funktioniert). Returns ``"shortcut"`` oder ``"copy"``. Laeuft auch in
    einem Worker-Thread: COM wird dafuer pro Aufruf initialisiert.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        logging.warning("  ⚠️  pywin32 nicht installiert. Erstelle Kopie statt Verknüpfung.")
        shutil.copy2(target, link_path)
        return "copy"
    try:
        pythoncom.CoInitialize()
        initialized = True
    except pythoncom.com_error:
        initialized = False  # der Thread hat COM schon in anderer Art - nutzbar
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(link_path + ".lnk")
        shortcut.Targetpath = target
        shortcut.save()
    finally:
        if initialized:
            pythoncom.CoUninitialize()
    return "shortcut"
