"""Router: settings, plugins, and LLM configuration.

Add-on switches and settings are not settings of this router: they live in
``plugins.json`` and are served by ``routers/plugins.py``.

Serves:
  GET  /api/settings
  PUT  /api/settings
  GET  /api/plugins/nav
  GET  /api/llm/providers

The LLM configuration itself (connections, roles, model lists) is the
``llm.json`` document served by ``routers/llm.py``; the flat LLM_* keys are no
longer settings — ``Config.reload_from_env`` reads them once for the in-memory
migration of an old ``.env`` and the first save of the document removes them.

Pure move from webapp.py (Backend-Modularisierung #80). No behaviour change.
All shared state (registry, Config) comes from context.py / literature_manager.
Persistence and the post-save reload chain live in ``settings_store`` (shared
with ``routers/llm.py``); the plugin-router reconcile hook is injected there
by webapp.py at mount time so no router ever imports webapp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import settings_store
from context import registry
from literature_manager import Config

router = APIRouter()

# The .env this router reads and writes. One rule for every entry point
# (``config.resolve_env_path``); kept as a module attribute because tests
# redirect it per test (``monkeypatch.setattr(settings_mod, "_ENV_PATH", …)``).
_ENV_PATH = Path(Config.ENV_PATH)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_KEYS = {
    "LITERATUR_BASE_DIR",
    "LINK_MODE",
    "CROSSREF_MAILTO",
    "OPENALEX_API_KEY",
    # "true", sobald das First-Run-Onboarding beantwortet ODER uebersprungen
    # wurde (#140). Fehlt der Key, zeigt die SPA den Dialog.
    "ONBOARDING_COMPLETED",
    "UI_LANGUAGE",
    "WATCH_INTERVAL",
    "MAX_OCR_PAGES",
    "UNLOCK_PDFS",
}

# Keys config.py parses as an int (see ``Config._env_int``), with the
# minimum each must satisfy. Validated here, at the write site, so a bad
# value never reaches the .env in the first place — config.py's parse stays
# tolerant besides, as a second line of defence for a value that got in some
# other way.
NUMERIC_KEYS = {"WATCH_INTERVAL": 1, "MAX_OCR_PAGES": 1}

# Plugin-router reconcile hook — lives in settings_store now; re-exported so
# webapp.py's wiring reads the same as before.
set_reconcile_hook = settings_store.set_reconcile_hook


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    LITERATUR_BASE_DIR: Optional[str] = None
    LINK_MODE: Optional[str] = None
    CROSSREF_MAILTO: Optional[str] = None
    OPENALEX_API_KEY: Optional[str] = None
    ONBOARDING_COMPLETED: Optional[str] = None
    UI_LANGUAGE: Optional[str] = None
    WATCH_INTERVAL: Optional[str] = None
    MAX_OCR_PAGES: Optional[str] = None
    UNLOCK_PDFS: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/settings")
async def get_settings():
    # Defaults für Keys die nicht in .env stehen
    defaults = {
        "UNLOCK_PDFS": "true",
    }
    settings = dict(defaults)
    settings.update(settings_store.read_env(_ENV_PATH, ALLOWED_KEYS))
    return settings


@router.get("/api/plugins/nav")
async def get_plugin_nav():
    """Nav items the loaded Add-on Bundles registered. Empty when none is active.

    The SPA reads this (on load and after switching an Add-on) for its sidebar;
    the routes behind the items come from the Bundles' frontend registrations.
    A disabled Add-on is never imported, so with none active this returns
    ``{"items": []}``.
    """
    from dataclasses import asdict
    return {"items": [asdict(item) for item in registry.nav_items()]}


@router.get("/api/llm/providers")
async def get_llm_providers():
    """The LLM provider presets (id, label, preset base URL) for the
    connection cards and the onboarding dialog. Connections, roles and model
    lists live in ``routers/llm.py``."""
    providers = [
        {"id": pid, "label": meta["label"], "base_url": meta["base_url"]}
        for pid, meta in Config.LLM_PROVIDERS.items()
    ]
    return {"providers": providers}


@router.put("/api/settings")
async def update_settings(data: SettingsUpdate):
    updates: dict[str, Optional[str]] = {}
    for key in ALLOWED_KEYS:
        value = getattr(data, key, None)
        if value is None:
            continue
        if key in NUMERIC_KEYS:
            stripped = value.strip()
            if not stripped:
                # Clearing the field means "use the default" — remove the
                # key rather than write an empty value config.py would have
                # to tolerate.
                updates[key] = None
                continue
            try:
                parsed = int(stripped)
            except ValueError:
                parsed = None
            if parsed is None or parsed < NUMERIC_KEYS[key]:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.invalidNumber", "params": {"key": key}},
                )
            updates[key] = stripped
        else:
            updates[key] = value
    settings_store.write_env(_ENV_PATH, updates)
    # Config zur Laufzeit aktualisieren — EIN Pfad fuer beide Settings-Writer
    # (.env hier, llm.json in routers/llm.py), damit nichts auseinanderdriftet.
    await settings_store.reload_runtime(_ENV_PATH)
    return {"status": "ok"}
