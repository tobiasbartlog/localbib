"""Shared request/runtime context for the LocalBib core.

Neutral home for the few cross-cutting helpers that both ``webapp.py`` and the
upcoming ``services/`` + ``routers/`` packages need, without importing
``webapp`` (which would be circular). Holds:

* ``get_conn()``     — a FRESH sqlite3 connection per call (unchanged behaviour).
* ``safe_pdf_path()``— path-traversal-safe resolver for stored PDFs.
* ``registry``       — the single ``PluginRegistry`` instance for the app.

Side-effect-free at import time (like ``registry.py`` / ``network_orchestrator``):
no DB writes, no logging config, no filesystem ops. ``Config`` is read lazily at
call time, so ``Config.init_paths()`` (done by ``webapp.py`` at startup) is
already in effect by the time these helpers run.
"""

from __future__ import annotations

import os
import sqlite3

from fastapi import HTTPException

from literature_manager import Config
from registry import PluginRegistry


def get_conn() -> sqlite3.Connection:
    """Erstellt eine neue DB-Verbindung mit Timeout."""
    conn = sqlite3.connect(Config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def safe_pdf_path(filename: str) -> str:
    """Gibt sicheren Pfad zur PDF-Datei zurueck. Wirft 404 bei Problemen."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(Config.ALL_DIR, safe_name)
    real_path = os.path.realpath(filepath)
    real_all = os.path.realpath(Config.ALL_DIR)
    if not real_path.startswith(real_all):
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="PDF-Datei nicht auf der Festplatte gefunden")
    return filepath


# Plugin-Registry (Phase 0b). Single source of truth for the whole app; the
# concrete host-service adapters ("llm"/"library") live in webapp.py and are
# injected via ``registry.register_services(...)`` after they are constructed.
registry = PluginRegistry()

# Plugin table: module name -> the env var that toggles it (Decision #17).
# The host (webapp startup/shutdown + PUT /api/settings) iterates this instead
# of hard-wiring each plugin. Lives here — the neutral resource module — so
# ``routers/settings.py`` can read it without importing ``webapp`` (circular).
PLUGIN_MODULES: dict[str, str] = {
}


def plugin_enabled(env_var: str) -> bool:
    """True iff the given toggle env var is set to 'true' (case-insensitive)."""
    return os.getenv(env_var, "false").strip().lower() == "true"
