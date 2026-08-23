"""Router: settings, plugins, and LLM configuration.

Serves:
  GET  /api/settings
  PUT  /api/settings
  GET  /api/plugins/nav
  GET  /api/llm/providers
  GET  /api/llm/models
  GET  /api/llm/embed-models

Pure move from webapp.py (Backend-Modularisierung #80). No behaviour change.
All shared state (registry, Config) comes from context.py / literature_manager.
The plugin-router reconcile hook is injected by webapp.py at mount time via
``set_reconcile_hook()`` so the router never imports webapp (no circular deps).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

import requests as http_requests
from dotenv import load_dotenv
from fastapi import APIRouter
from pydantic import BaseModel

import services.model_recommender as _recommender
from context import registry, PLUGIN_MODULES, plugin_enabled
from literature_manager import Config
from llm_client import llm_for

router = APIRouter()

# ---------------------------------------------------------------------------
# ENV_PATH — same frozen-build logic as webapp.py so both resolve to the same
# file on disk.
# ---------------------------------------------------------------------------
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _CONFIG_DIR = Path(os.path.expanduser("~")) / "Literatur"
    _ENV_PATH = _CONFIG_DIR / ".env"
else:
    _BUNDLE_DIR = Path(__file__).parent.parent.resolve()
    _ENV_PATH = _BUNDLE_DIR / ".env"
    if not _ENV_PATH.exists():
        _main_repo = _BUNDLE_DIR.parent.parent.parent
        if (_main_repo / ".env").exists():
            _ENV_PATH = _main_repo / ".env"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_KEYS = {
    "LITERATUR_BASE_DIR",
    "LINK_MODE",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "KICONNECT_API_KEY",
    "LLM_MODEL",
    "LLM_MODEL_FAST",
    "LLM_EMBED_MODEL",
    "LLM_EMBED_URL",
    "CROSSREF_MAILTO",
    "OPENALEX_API_KEY",
    # "true", sobald das First-Run-Onboarding beantwortet ODER uebersprungen
    # wurde (#140). Fehlt der Key, zeigt die SPA den Dialog.
    "ONBOARDING_COMPLETED",
    "WATCH_INTERVAL",
    "MAX_OCR_PAGES",
    "UNLOCK_PDFS",
}

# ---------------------------------------------------------------------------
# Plugin-router reconcile hook — injected by webapp.py at mount time.
# ---------------------------------------------------------------------------
_reconcile_plugin_routers: Optional[Callable[[], None]] = None


def set_reconcile_hook(fn: Callable[[], None]) -> None:
    """Called by webapp.py to inject the _reconcile_plugin_routers callable."""
    global _reconcile_plugin_routers
    _reconcile_plugin_routers = fn


# ---------------------------------------------------------------------------
# Helpers (copied verbatim from webapp.py; pure functions, no shared state)
# ---------------------------------------------------------------------------

def _normalize_ca_bundle_env() -> None:
    """REQUESTS_CA_BUNDLE portabel machen: requests liest die Variable roh
    (ohne ~/%VARS%-Expansion) und bei verify=None gewinnt sie sogar gegen den
    expliziten Parameter. Daher hier expandieren und entfernen, wenn die Datei
    auf diesem Rechner nicht existiert -> certifi-Fallback ueberall."""
    raw = os.environ.get("REQUESTS_CA_BUNDLE")
    if not raw:
        return
    path = os.path.expanduser(os.path.expandvars(raw))
    if os.path.isfile(path):
        os.environ["REQUESTS_CA_BUNDLE"] = path
    else:
        del os.environ["REQUESTS_CA_BUNDLE"]


def _ca_bundle() -> Optional[str]:
    """CA-Bundle aus REQUESTS_CA_BUNDLE: ~/%VARS% expandiert, aber nur wenn die
    Datei existiert. Sonst None -> requests faellt auf certifi zurueck."""
    raw = os.getenv("REQUESTS_CA_BUNDLE")
    if not raw:
        return None
    path = os.path.expanduser(os.path.expandvars(raw))
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    LITERATUR_BASE_DIR: Optional[str] = None
    LINK_MODE: Optional[str] = None
    LLM_PROVIDER: Optional[str] = None
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: Optional[str] = None
    KICONNECT_API_KEY: Optional[str] = None
    LLM_MODEL: Optional[str] = None
    LLM_MODEL_FAST: Optional[str] = None
    LLM_EMBED_MODEL: Optional[str] = None
    LLM_EMBED_URL: Optional[str] = None
    CROSSREF_MAILTO: Optional[str] = None
    OPENALEX_API_KEY: Optional[str] = None
    ONBOARDING_COMPLETED: Optional[str] = None
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
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _, value = stripped.partition("=")
                key = key.strip()
                if key in ALLOWED_KEYS:
                    settings[key] = value.strip()

    return settings


@router.get("/api/plugins/nav")
async def get_plugin_nav():
    """Nav items contributed by active plugins. Empty when none are enabled.

    The SPA polls this (on load and after toggling a plugin) and registers a
    route + sidebar entry per item. A disabled plugin is never imported, so with
    no plugin enabled this returns ``{"items": []}``.
    """
    from dataclasses import asdict
    return {"items": [asdict(item) for item in registry.nav_items()]}


@router.get("/api/llm/providers")
async def get_llm_providers():
    """Return the available LLM provider presets for the settings dropdown."""
    providers = [
        {"id": pid, "label": meta["label"], "base_url": meta["base_url"]}
        for pid, meta in Config.LLM_PROVIDERS.items()
    ]
    return {"providers": providers, "current": Config.LLM_PROVIDER}


def _ensure_available_models() -> list:
    """Return the active provider's model list, fetching live if the cache is empty."""
    if Config.AVAILABLE_MODELS:
        return Config.AVAILABLE_MODELS
    api_key = Config.LLM_API_KEY
    models_url = Config.LLM_MODELS_URL
    if not api_key or not models_url:
        logging.warning("/api/llm/models: kein API-Key oder Endpunkt fuer Provider '%s'", Config.LLM_PROVIDER)
        return []
    ca = _ca_bundle()
    try:
        resp = http_requests.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
            verify=ca,
        )
        logging.info("/api/llm/models fetch: HTTP %s from %s", resp.status_code, models_url)
        if resp.status_code == 200:
            Config.AVAILABLE_MODELS = [m["id"] for m in resp.json().get("data", [])]
        else:
            logging.warning("/api/llm/models fetch fehlgeschlagen: %s", resp.text[:200])
    except Exception as exc:
        logging.warning("/api/llm/models fetch error: %s", exc)
    return Config.AVAILABLE_MODELS


@router.get("/api/llm/models")
async def get_llm_models():
    """Return available models from the active LLM provider. Fetches live if cache empty."""
    models = _ensure_available_models()
    return {"models": models, "current": Config.LLM_MODEL, "current_fast": Config.LLM_MODEL_FAST}


def _embed_models_url(embed_url: str) -> str:
    """Leitet aus einer Embeddings-URL den /models-Endpunkt desselben
    Anbieters ab (``.../v1/embeddings`` -> ``.../v1/models``)."""
    base = (embed_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/embeddings"):
        base = base[: -len("/embeddings")]
    return f"{base}/models"


@router.get("/api/llm/embed-models")
async def get_embed_models(url: Optional[str] = None):
    """Modell-Liste fuer das Embedding-Dropdown der Settings-UI.

    Ohne Embed-URL-Override gilt die Liste des aktiven LLM-Providers (wie
    /api/llm/models). Mit Override — gespeichert oder als ``url``-Parameter
    fuer noch ungespeicherte SPA-Eingaben (leerer Param = explizit kein
    Override) — wird dessen abgeleiteter /models-Endpunkt abgefragt.
    Fehler degradieren zu einer leeren Liste; die SPA zeigt dann das
    Freitextfeld statt des Dropdowns.
    """
    override = (Config.LLM_EMBED_URL if url is None else url).strip()
    if not override:
        models = list(_ensure_available_models())
    else:
        models = []
        models_url = _embed_models_url(override)
        headers = {}
        if Config.LLM_API_KEY:
            headers["Authorization"] = f"Bearer {Config.LLM_API_KEY}"
        try:
            resp = http_requests.get(models_url, headers=headers, timeout=10, verify=_ca_bundle())
            if resp.status_code == 200:
                models = [m["id"] for m in resp.json().get("data", [])]
            else:
                logging.warning("/api/llm/embed-models fetch fehlgeschlagen: %s", resp.text[:200])
        except Exception as exc:
            logging.warning("/api/llm/embed-models fetch error: %s", exc)
    # Embedding-artige Modelle zuerst — die Provider-Liste enthaelt ueberwiegend
    # Chat-Modelle, die fuer dieses Feld selten gemeint sind.
    models.sort(key=lambda m: (0 if "embed" in m.lower() else 1, m.lower()))
    return {"models": models, "current": Config.LLM_EMBED_MODEL}


@router.post("/api/llm/suggest-models")
async def suggest_models():
    """Recommend a reasoning + a fast model from the active provider's list.

    Asks the reasoning model to pick the two best (source="llm"); on any
    failure — no key, unparseable answer, invalid IDs — falls back to the
    offline name heuristic (source="heuristic"). Persists nothing; the SPA
    pre-fills the dropdowns and the user saves explicitly.
    """
    models = _ensure_available_models()
    if not models:
        return {"suggestion": None, "error": "Keine Modelle verfuegbar. Provider/API-Key pruefen."}

    # Primary: let the reasoning model choose.
    if Config.KICONNECT_API_KEY:
        try:
            llm = llm_for("model_suggest")
            content = llm.complete(
                [{"role": "user", "content": _recommender.build_suggest_prompt(models)}],
                timeout=60,
            )
            parsed = _recommender.parse_llm_suggestion(content, models)
            if parsed:
                return {"suggestion": parsed}
            logging.info("suggest-models: LLM-Antwort unbrauchbar, nutze Heuristik")
        except Exception as exc:
            logging.warning("suggest-models: LLM-Call fehlgeschlagen (%s), nutze Heuristik", exc)

    # Fallback: offline heuristic.
    return {"suggestion": _recommender.heuristic_suggest(models)}


@router.put("/api/settings")
async def update_settings(data: SettingsUpdate):
    lines = []
    existing_keys = set()

    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in ALLOWED_KEYS:
                    new_val = getattr(data, key, None)
                    if new_val is not None:
                        lines.append(f"{key}={new_val}")
                        existing_keys.add(key)
                        continue
            lines.append(line)

    # Neue Keys hinzufuegen, die noch nicht in der Datei stehen
    for key in ALLOWED_KEYS:
        if key not in existing_keys:
            new_val = getattr(data, key, None)
            if new_val is not None:
                lines.append(f"{key}={new_val}")

    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Config zur Laufzeit aktualisieren — ein Pfad fuer Import & Reload,
    # damit die Env-Defaults nicht wieder auseinanderdriften.
    load_dotenv(_ENV_PATH, override=True)
    _normalize_ca_bundle_env()
    Config.reload_from_env()

    # Plugins live aktivieren/deaktivieren, wenn ein Toggle sich geaendert hat.
    # Ueber ALLE Eintraege aus context.PLUGIN_MODULES, damit auch ein reiner
    # Pfadwechsel eines Plugins per Re-Sync sofort greift.
    # deactivate() loest alle Plugin-Registrierungen rueckstandsfrei (Phase-0b-Akzeptanz).
    try:
        for module_name, env_var in PLUGIN_MODULES.items():
            await registry.sync(module_name, plugin_enabled(env_var))
        if _reconcile_plugin_routers is not None:
            _reconcile_plugin_routers()
    except Exception as exc:
        logging.warning("Plugin-Sync nach Settings-Aenderung fehlgeschlagen: %s", exc)

    return {"status": "ok"}
