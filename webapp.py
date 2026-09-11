#!/usr/bin/env python3
"""
Literatur-Manager Web UI
========================
FastAPI-basierte Web-Oberflaeche fuer den Literatur-Manager.

Start: python webapp.py
Dann: http://localhost:8000
"""

import os
import sys

# Sicherstellen, dass das Skript-Verzeichnis im Importpfad an erster Stelle steht
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time  # noqa: F401 — re-exported; tests patch webapp.time
import signal
import logging
from datetime import datetime
from typing import Optional

# .env laden VOR allen Projekt-Imports, da metadata_validation -> literature_manager -> os.getenv
from dotenv import load_dotenv
from pathlib import Path as _Path

# Frozen build (PyInstaller): gebündelte Assets liegen in sys._MEIPASS, die
# .env muss aber in einem persistenten, beschreibbaren Verzeichnis liegen
# (der Exe-/_internal-Ordner kann read-only sein, z.B. unter Program Files).
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    # Assets (static/, templates/) werden von PyInstaller nach _MEIPASS entpackt
    _BUNDLE_DIR = _Path(getattr(sys, "_MEIPASS", _Path(sys.executable).parent))
    # Persistente Konfiguration neben den Nutzerdaten (~/Literatur/.env)
    _CONFIG_DIR = _Path(os.path.expanduser("~")) / "Literatur"
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _ENV_PATH = _CONFIG_DIR / ".env"
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text("", encoding="utf-8")
else:
    _BUNDLE_DIR = _Path(__file__).parent.resolve()
    _ENV_PATH = _BUNDLE_DIR / ".env"
    if not _ENV_PATH.exists():
        _main_repo = _BUNDLE_DIR.parent.parent.parent
        if (_main_repo / ".env").exists():
            _ENV_PATH = _main_repo / ".env"

_SCRIPT_DIR = _BUNDLE_DIR
load_dotenv(_ENV_PATH)


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


_normalize_ca_bundle_env()

import requests as http_requests

# Re-exported for back-compat with tests that patch ``webapp.<name>`` (LLMClient,
# metadata_validation, http_requests above). The domain logic that used to live
# here moved into routers/ + services/ + host_services.py (Backend-Modularisierung
# #79–#93); webapp is now a thin bootstrap.
from llm_client import LLMClient  # noqa: F401
import metadata_validation  # noqa: F401
from context import registry, PLUGIN_MODULES, plugin_enabled

SCRIPT_DIR = _SCRIPT_DIR
ENV_PATH = _ENV_PATH

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from literature_manager import Config, Database
from literature_manager import extract_text_from_pdf  # noqa: F401 — re-export for tests

# =============================================================================
# KONFIGURATION
# =============================================================================

# Pfad portabel halten: ~ und %VARS% expandieren, damit dieselbe .env auf
# Rechnern mit unterschiedlichem Benutzernamen funktioniert.
BASE_DIR = os.path.expanduser(os.path.expandvars(os.getenv(
    "LITERATUR_BASE_DIR",
    os.path.join("~", "Literatur"),
)))
Config.init_paths(BASE_DIR)


def ca_bundle() -> Optional[str]:
    """CA-Bundle aus REQUESTS_CA_BUNDLE: ~/%VARS% expandiert, aber nur wenn die
    Datei existiert. Sonst None -> requests faellt auf certifi zurueck. So bleibt
    dieselbe .env portabel ueber Rechner (Bundle nur dort aktiv, wo es liegt)."""
    raw = os.getenv("REQUESTS_CA_BUNDLE")
    if not raw:
        return None
    path = os.path.expanduser(os.path.expandvars(raw))
    return path if os.path.isfile(path) else None


# Env-Settings + LLM-Endpunkte neu laden (die .env von oben ist jetzt aktiv)
Config.reload_from_env()

# =============================================================================
# APP
# =============================================================================

app = FastAPI(title="Literatur-Manager", version="1.0")

app.mount("/static", StaticFiles(directory=str(SCRIPT_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(SCRIPT_DIR / "templates"))

db = Database(Config.DB_PATH)

# --- Host-Services fuer Plugins (Phase 4: api.llm / api.library) -------------
# Konkrete Adapter ueber Kern-Bestand leben im neutralen Modul host_services.py
# (#93). Plugins erreichen LLM & Bibliothek NUR hierueber (P3) — nie per
# Direktimport von llm_client/Database. Re-exported so tests patching
# ``webapp._CoreLlmApi`` / ``webapp._CoreLibraryApi`` keep working.
from host_services import _CoreLlmApi, _CoreLibraryApi, _cite_key  # noqa: F401

# Plugin-Registry (Phase 0b). Die Instanz gehoert context.py; hier werden nur
# die konkreten Host-Service-Adapter (die Kern-Interna nutzen) injiziert. Welche
# Plugins es gibt und welche Env-Var sie schaltet, steht in
# ``context.PLUGIN_MODULES`` (Entscheidung #17) — der Startup-/Shutdown-Sync
# unten iteriert darueber, statt jedes Plugin einzeln zu verdrahten.
registry.register_services({"llm": _CoreLlmApi(), "library": _CoreLibraryApi()})


# Routes contributed by active plugins, tracked so deactivate can unmount them.
_plugin_router_routes: dict = {}  # id(router) -> [route objects added to the app]


def _reconcile_plugin_routers() -> None:
    """Mount routers of active plugins, unmount those of deactivated ones.

    FastAPI has no public 'remove router' API, so we track the exact route
    objects each router appended and drop them on deactivate. Single-user local
    app: mutating app.router.routes at runtime is safe (Starlette matches by
    iterating the list per request)."""
    desired = {id(r): r for r in registry.routers()}
    for rid, router in desired.items():
        if rid not in _plugin_router_routes:
            before = len(app.router.routes)
            app.include_router(router)
            _plugin_router_routes[rid] = app.router.routes[before:]
            app.openapi_schema = None
    for rid in list(_plugin_router_routes):
        if rid not in desired:
            for route in _plugin_router_routes.pop(rid):
                try:
                    app.router.routes.remove(route)
                except ValueError:
                    pass
            app.openapi_schema = None


# =============================================================================
# ROUTERS (Backend-Modularisierung #79–#93)
# One APIRouter per domain. Every domain handler, Pydantic model and helper that
# used to live in webapp.py now sits in routers/ + services/ + the neutral
# modules (pdf_chunking, validation_policy, host_services, context). webapp.py
# only imports the routers and mounts them; shared resources (Config, Database)
# come from literature_manager / context.py.
# =============================================================================

from routers.stats import router as _stats_router
from routers.version import router as _version_router, APP_VERSION  # noqa: F401  (re-export)
from routers.banner import router as _banner_router
from routers.license import router as _license_router
from routers.appearance import router as _appearance_router
from routers.settings import router as _settings_router, set_reconcile_hook as _set_settings_reconcile_hook
from routers.categories import router as _categories_router
from routers.export import router as _export_router
from routers.custom_fields import router as _custom_fields_router
from routers.bibtex_import import router as _bibtex_import_router
from routers.papers import router as _papers_router
from routers.validate import router as _validate_router
from routers.references import router as _references_router
from routers.research_chat import router as _research_chat_router
from routers.analysis import router as _analysis_router
from routers.duplicates import router as _duplicates_router
from routers.maintenance import router as _maintenance_router
from routers.search import router as _search_router

# Re-exports kept ONLY for tests that reach them via ``webapp.<name>``
# (``from webapp import _normalize_title``; ``webapp.detect_book_structure``).
from services.duplicate_detection import normalize_title as _normalize_title  # noqa: F401
from routers.bibtex_import import detect_book_structure  # noqa: F401

# Import router (#87): the dotted path "routers.import" cannot be a static import
# because "import" is a Python keyword, so load it via importlib.
import importlib as _importlib
_import_router = _importlib.import_module("routers.import").router

# Inject the plugin-router reconcile callable so settings PUT can mount/unmount
# plugin routes without importing webapp (which would be circular).
_set_settings_reconcile_hook(_reconcile_plugin_routers)

app.include_router(_stats_router)
app.include_router(_version_router)
app.include_router(_banner_router)
app.include_router(_license_router)
app.include_router(_appearance_router)
app.include_router(_settings_router)
app.include_router(_categories_router)
app.include_router(_export_router)
app.include_router(_custom_fields_router)
app.include_router(_bibtex_import_router)
app.include_router(_papers_router)
app.include_router(_validate_router)
app.include_router(_references_router)
app.include_router(_research_chat_router)
app.include_router(_analysis_router)
app.include_router(_import_router)
app.include_router(_duplicates_router)
app.include_router(_maintenance_router)
app.include_router(_search_router)


# =============================================================================
# SPA ENTRY POINT
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the app icon at the well-known path.

    index.html links the PNG variants explicitly, but browsers still request
    /favicon.ico on their own — pinning the app to the Windows taskbar is the
    case that actually needs it."""
    return FileResponse(SCRIPT_DIR / "static" / "icons" / "localbib.ico")


# =============================================================================
# SERVER MANAGEMENT
# =============================================================================

@app.post("/api/server/shutdown")
async def shutdown_server():
    """Faehrt den Server sauber herunter."""
    import threading

    def _shutdown():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_shutdown, daemon=True).start()
    return {"status": "ok", "message": "Server wird heruntergefahren..."}


# =============================================================================
# LIFESPAN EVENTS (plugin sync/teardown, start-count, model probe)
# =============================================================================

@app.on_event("startup")
async def _sync_plugins() -> None:
    """Load each plugin at startup iff its toggle env var is set. A disabled
    plugin is never imported. Iterates ``context.PLUGIN_MODULES`` — no plugin
    is hard-wired here."""
    try:
        for module_name, env_var in PLUGIN_MODULES.items():
            await registry.sync(module_name, plugin_enabled(env_var))
        _reconcile_plugin_routers()
    except Exception as exc:
        logging.warning("Plugin-Sync beim Start fehlgeschlagen: %s", exc)


@app.on_event("shutdown")
async def _teardown_plugins() -> None:
    """Deactivate all plugins on shutdown (stops watchers, unmounts routes)."""
    try:
        for module_name in PLUGIN_MODULES:
            await registry.sync(module_name, False)
        _reconcile_plugin_routers()
    except Exception as exc:
        logging.warning("Plugin-Teardown fehlgeschlagen: %s", exc)


@app.on_event("startup")
async def _increment_start_count() -> None:
    """Increment app_start_count and stamp the first run once (#144).

    Der Erststart-Stempel ist der Anker der 14-Tage-Testphase (ADR-0015). Er
    wird genau einmal gesetzt und danach nie mehr angefasst — auch nicht bei
    einer Quellinstallation, die den Stempel gar nicht braucht: ihn dort
    auszulassen hiesse, dass ein spaeter gebautes .exe im selben Datenordner
    ohne Anker startet. Ein Bestandsnutzer, der auf diese Version aktualisiert,
    bekommt den Stempel jetzt und damit volle 14 Tage; das ist gewollt.
    """
    count = int(db.get_app_setting("app_start_count") or "0")
    db.set_app_setting("app_start_count", str(count + 1))
    if not db.get_app_setting("license_first_run_at"):
        db.set_app_setting("license_first_run_at", datetime.utcnow().isoformat())


@app.on_event("startup")
async def _check_available_models() -> None:
    """Fetch available models from the active LLM provider and warn if model is missing."""
    if not Config.LLM_API_KEY or not Config.LLM_MODELS_URL:
        return
    provider = Config.LLM_PROVIDER
    ca = ca_bundle()
    try:
        resp = http_requests.get(
            Config.LLM_MODELS_URL,
            headers={"Authorization": f"Bearer {Config.LLM_API_KEY}"},
            timeout=10,
            verify=ca,
        )
        if resp.status_code == 200:
            Config.AVAILABLE_MODELS = [m["id"] for m in resp.json().get("data", [])]
            logging.info("%s: %d Modelle verfügbar: %s", provider, len(Config.AVAILABLE_MODELS), Config.AVAILABLE_MODELS)
            if Config.LLM_MODEL and Config.LLM_MODEL not in Config.AVAILABLE_MODELS:
                logging.warning(
                    "⚠️  LLM_MODEL '%s' ist bei Provider '%s' nicht verfügbar. "
                    "Verfügbare Modelle: %s",
                    Config.LLM_MODEL,
                    provider,
                    Config.AVAILABLE_MODELS,
                )
        else:
            logging.warning("%s models-Abfrage fehlgeschlagen: HTTP %s", provider, resp.status_code)
    except Exception as exc:
        logging.warning("%s models-Abfrage nicht möglich: %s", provider, exc)


# Core Projects feature removed (issue #52, ADR-0004): the /api/projects CRUD and
# paper-project assignment endpoints are gone. The projects/paper_projects tables
# stay as dead data until a later release; read access for the plugin-side
# migration lives in host_services._CoreLibraryApi.get_core_projects.


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # PyInstaller: verhindert Prozess-Forkbomb

    import uvicorn

    _PORT = int(os.getenv("LOCALBIB_PORT", "8000"))
    _URL = f"http://localhost:{_PORT}"

    print()
    print("  ========================================")
    print("  Literatur-Manager Web UI")
    print("  ========================================")
    print()
    print(f"  URL:       {_URL}")
    print(f"  Datenbank: {Config.DB_PATH}")
    print(f"  PDFs:      {Config.ALL_DIR}")
    print(f"  Input:     {Config.INPUT_DIR}")
    print()

    # Browser automatisch öffnen (verzögert, bis der Server hochgefahren ist).
    # Im Dev-Modus (Hot-Reload via Editor) nicht erwünscht → nur im Frozen-Build
    # oder wenn explizit angefordert.
    if _FROZEN or os.getenv("LOCALBIB_OPEN_BROWSER") == "1":
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(_URL)).start()

    uvicorn.run(app, host="127.0.0.1", port=_PORT)
