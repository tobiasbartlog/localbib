#!/usr/bin/env python3
"""Watchdog: Überwacht den Input-Ordner auf neue PDFs."""

import time
import logging

from config import Config
from database import Database
from pipeline import process_paper

# Optional imports
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    print("⚠️  watchdog nicht installiert. Nur manueller Import möglich.")
    print("   pip install watchdog")


class PDFHandler(FileSystemEventHandler):
    """Überwacht Input-Ordner auf neue PDFs."""

    def __init__(self, db: Database):
        self.db = db

    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.lower().endswith(".pdf"):
            # Kurz warten, damit Datei fertig geschrieben ist
            time.sleep(2)
            try:
                process_paper(event.src_path, self.db)
            except Exception as e:
                logging.error(f"❌ Fehler bei Verarbeitung: {e}")

    def on_moved(self, event):
        if event.dest_path.lower().endswith(".pdf"):
            time.sleep(2)
            try:
                process_paper(event.dest_path, self.db)
            except Exception as e:
                logging.error(f"❌ Fehler bei Verarbeitung: {e}")


def start_watcher(db: Database):
    """Startet Watchdog-Überwachung des Input-Ordners."""
    if not WATCHDOG_AVAILABLE:
        logging.error("❌ watchdog nicht installiert!")
        return

    event_handler = PDFHandler(db)
    observer = Observer()
    observer.schedule(event_handler, Config.INPUT_DIR, recursive=False)
    observer.start()

    logging.info(f"👁️  Überwache: {Config.INPUT_DIR}")
    logging.info(f"   PDFs einfach in diesen Ordner ziehen!")
    logging.info(f"   Beenden mit Strg+C\n")

    try:
        while True:
            time.sleep(Config.WATCH_INTERVAL)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("\n👋 Watchdog beendet.")
    observer.join()
