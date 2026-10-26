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

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import detail_layout
import llm_config
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
    # Look and sidebar of this installation (#208). The server is the truth;
    # the browser's localStorage only mirrors it for a flicker-free first paint.
    "DARK_MODE",
    "THESIS_MODE",
    # Which blocks the Item detail view shows and where (#230): compact JSON,
    # normalized by detail_layout on write and on read.
    "DETAIL_LAYOUT",
    # Dialect of every BibTeX export: "biblatex" (default) or "bibtex".
    "BIB_DIALECT",
}

# Keys that never reach the SPA in plain text (SET-B9, "keys never reach the
# SPA"): GET sends "" plus a ``<KEY>_HINT``; a PUT of that hint is no change.
SECRET_KEYS = {"OPENALEX_API_KEY"}

# Keys holding "true" / "false" and nothing else.
BOOLEAN_KEYS = {"DARK_MODE", "THESIS_MODE"}

# Keys config.py parses as an int (see ``Config._env_int``), with the
# minimum each must satisfy. Validated here, at the write site, so a bad
# value never reaches the .env in the first place — config.py's parse stays
# tolerant besides, as a second line of defence for a value that got in some
# other way.
NUMERIC_KEYS = {"WATCH_INTERVAL": 1, "MAX_OCR_PAGES": 1}

# Keys holding one of a fixed set of values (Config owns the sets).
CHOICE_KEYS = {"LINK_MODE": Config.LINK_MODES, "BIB_DIALECT": Config.BIB_DIALECTS}

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
    DARK_MODE: Optional[str] = None
    THESIS_MODE: Optional[str] = None
    DETAIL_LAYOUT: Optional[str] = None
    BIB_DIALECT: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/settings")
async def get_settings():
    # Defaults für Keys die nicht in .env stehen. Der Link-Modus kommt als der
    # wirksame Wert (#213) — sein Default steht in Config.reload_from_env.
    defaults = {
        "UNLOCK_PDFS": "true",
        "LINK_MODE": Config.LINK_MODE,
        "BIB_DIALECT": Config.BIB_DIALECT,
    }
    settings = dict(defaults)
    settings.update(settings_store.read_env(_ENV_PATH, ALLOWED_KEYS))
    # Always the layout in force, also without an entry (or with an unusable one).
    settings["DETAIL_LAYOUT"] = detail_layout.dumps(
        detail_layout.effective(settings.get("DETAIL_LAYOUT")))
    for key in SECRET_KEYS:
        current = _effective_secret(key)
        settings[key] = ""
        settings[key + "_HINT"] = llm_config.key_hint(current) if current else ""
    return settings


def _effective_secret(key: str) -> str:
    """The secret in use: the running value (``Config``, from the process
    environment — where a real process-env value wins over the .env, SET-B5),
    else the .env entry. The hint is built from this, and a PUT of that hint
    is compared against it, so a key supplied by the environment alone is
    shown as in use and kept."""
    return getattr(Config, key, "") or settings_store.read_env(_ENV_PATH, {key}).get(key) or ""


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


def _clean_path_value(value: str) -> str:
    """Whitespace and one pair of surrounding quotes off a pasted path
    (Explorer's "Copy as path" yields ``"C:\\...\\Literatur"``)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


@router.put("/api/settings")
async def update_settings(data: SettingsUpdate):
    updates: dict[str, Optional[str]] = {}
    for key in ALLOWED_KEYS:
        value = getattr(data, key, None)
        if value is None:
            continue
        if key in SECRET_KEYS:
            current = _effective_secret(key)
            cleaned = value.strip()
            if current and cleaned == llm_config.key_hint(current):
                continue  # the masked hint sent back: unchanged, keep the key
            # "" removes the key — delete the line rather than write it empty.
            updates[key] = cleaned or None
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
        elif key in BOOLEAN_KEYS:
            if value not in ("true", "false"):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.invalidBoolean", "params": {"key": key}},
                )
            updates[key] = value
        elif key == "DETAIL_LAYOUT":
            try:
                updates[key] = detail_layout.dumps(detail_layout.parse(value))
            except detail_layout.InvalidLayout:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.invalidLayout", "params": {"key": key}},
                )
        elif key in CHOICE_KEYS:
            if value not in CHOICE_KEYS[key]:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.invalidChoice", "params": {"key": key}},
                )
            updates[key] = value
        elif key == "LITERATUR_BASE_DIR":
            cleaned = _clean_path_value(value)
            # ~ and %VARS% are expanded at startup, so judge the expanded form.
            expanded = os.path.expanduser(os.path.expandvars(cleaned))
            if not cleaned or not os.path.isabs(expanded):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.baseDirNotAbsolute", "params": {"key": key}},
                )
            updates[key] = cleaned
        else:
            updates[key] = value
    for key, value in updates.items():
        # A lone surrogate cannot be written as UTF-8: refuse before any write.
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "error.settings.invalidValue", "params": {"key": key}},
                )
    settings_store.write_env(_ENV_PATH, updates)
    # Config zur Laufzeit aktualisieren — EIN Pfad fuer beide Settings-Writer
    # (.env hier, llm.json in routers/llm.py), damit nichts auseinanderdriftet.
    await settings_store.reload_runtime(_ENV_PATH)
    answer: dict = {"status": "ok"}
    if "DETAIL_LAYOUT" in updates:
        # The layout in force, so the editor needs no second GET.
        answer["detail_layout"] = detail_layout.effective(updates["DETAIL_LAYOUT"])
    return answer
