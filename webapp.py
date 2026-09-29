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
# Wo sie liegt, entscheidet config.resolve_env_path() — eine Regel fuer
# webapp, Settings-Router und CLI; hier bleibt nur der Seiteneffekt, den
# Ordner samt leerer Datei beim ersten Start anzulegen.
from config import Config  # importiert noch keine .env — reload_from_env() unten

_FROZEN = getattr(sys, "frozen", False)
_ENV_PATH = _Path(Config.ENV_PATH)

if _FROZEN:
    # Assets (static/, templates/) werden von PyInstaller nach _MEIPASS entpackt
    _BUNDLE_DIR = _Path(getattr(sys, "_MEIPASS", _Path(sys.executable).parent))
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text("", encoding="utf-8")
else:
    _BUNDLE_DIR = _Path(__file__).parent.resolve()

_SCRIPT_DIR = _BUNDLE_DIR
load_dotenv(_ENV_PATH)


import ca_trust

ca_trust.install()

import requests as http_requests

# Re-exported for back-compat with tests that patch ``webapp.<name>`` (LLMClient,
# metadata_validation, http_requests above). The domain logic that used to live
# here moved into routers/ + services/ + host_services.py (Backend-Modularisierung
# #79–#93); webapp is now a thin bootstrap.
from llm_client import LLMClient  # noqa: F401
import metadata_validation  # noqa: F401
from context import registry  # noqa: F401 — tests reach webapp.registry
import plugin_loader
import settings_store

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
    """Trust-Store fuer ausgehende Aufrufe — siehe ``ca_trust.ca_bundle``.

    Bleibt als Name erhalten, weil Tests ``webapp.ca_bundle`` patchen; die
    Logik (certifi + Haus-Bundle zusammenfuehren) liegt in ``ca_trust``."""
    return ca_trust.ca_bundle()


# Env-Settings + LLM-Endpunkte neu laden (die .env von oben ist jetzt aktiv)
Config.reload_from_env()

# =============================================================================
# APP
# =============================================================================

app = FastAPI(title="Literatur-Manager", version="1.0")

# Kein Login, nur Loopback — aber jede offene Webseite kann hierher POSTen.
# Schreibende /api/*-Aufrufe fremder Herkunft werden abgewiesen (origin_guard).
from origin_guard import OriginGuardMiddleware

app.add_middleware(OriginGuardMiddleware)

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
# Add-ons es gibt, entscheidet ``plugin_loader`` aus installierten Bundles und
# Dev-Suchpfaden (ADR-0021) — der Kern kennt keine Add-on-Namen.
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
            # include_router merges the router's lifespan into the app's by
            # wrapping it — once per mount. Plugin routers carry no lifespan of
            # their own, so keep the app's: otherwise every activation nests one
            # more level until startup hits the recursion limit.
            lifespan = app.router.lifespan_context
            app.include_router(router)
            app.router.lifespan_context = lifespan
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
from routers.paper_by_doi import router as _paper_by_doi_router
from routers.papers import router as _papers_router
from routers.validate import router as _validate_router
from routers.references import router as _references_router
from routers.research_chat import router as _research_chat_router
from routers.analysis import router as _analysis_router
from routers.duplicates import router as _duplicates_router
from routers.maintenance import router as _maintenance_router
from routers.search import router as _search_router
from routers.migration import router as _migration_router
from routers.llm import router as _llm_router
from routers.plugins import router as _plugins_router
from routers.marketplace import router as _marketplace_router

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
# Literal /api/papers/by-doi… before the parametric /api/papers/{paper_id} family.
app.include_router(_paper_by_doi_router)
app.include_router(_papers_router)
app.include_router(_validate_router)
app.include_router(_references_router)
app.include_router(_research_chat_router)
app.include_router(_analysis_router)
app.include_router(_import_router)
app.include_router(_duplicates_router)
app.include_router(_maintenance_router)
app.include_router(_search_router)
app.include_router(_migration_router)
app.include_router(_llm_router)
app.include_router(_plugins_router)
app.include_router(_marketplace_router)

# Add-on loader (ADR-0021): persists through the one plugins.json writer;
# the release version feeds the min_core check of a frozen build.
plugin_loader.configure(persist=settings_store.save_plugins_document, core_version=APP_VERSION)


# =============================================================================
# SPA ENTRY POINT
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Die Sprache wird serverseitig gerendert, nicht nachgeladen (ADR-0018):
    # so tragen `<html lang>` und der erste Frame dieselbe Sprache und es
    # blitzt kein roher Key-Pfad auf, bevor die SPA den Katalog waehlt.
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "ui_language": Config.UI_LANGUAGE,
         # Add-on frontends (#187): rendered in, so the boot loads their
         # scripts before the router resolves the first route — no round trip.
         "addons": plugin_loader.frontends()},
    )


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
    """Load the Add-ons from Bundles and Dev-Suchpfaden. A disabled one is
    never imported.
    ``plugin_loader.sync_all`` guards every plugin on its own, so the router
    reconcile below always runs (ADR-0021)."""
    try:
        await plugin_loader.sync_all(startup=True)
    except Exception as exc:
        logging.warning("Plugin-Sync beim Start fehlgeschlagen: %s", exc)
    _reconcile_plugin_routers()


@app.on_event("shutdown")
async def _teardown_plugins() -> None:
    """Deactivate all plugins on shutdown (stops watchers, unmounts routes)."""
    try:
        await plugin_loader.teardown_all()
    except Exception as exc:
        logging.warning("Plugin-Teardown fehlgeschlagen: %s", exc)
    _reconcile_plugin_routers()


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
    """Warn at startup when a bound role names a model its connection does
    not offer — the mistake that otherwise surfaces as a 4xx mid-import."""
    from routers.llm import fetch_models

    seen: dict[str, tuple[list, object]] = {}
    for tier in ("reasoning", "fast", "embedding"):
        ep = Config.llm_endpoint(tier)
        if ep is None:
            continue
        if ep["base_url"] not in seen:
            seen[ep["base_url"]] = fetch_models(ep["base_url"], ep["api_key"])
        models, error = seen[ep["base_url"]]
        if error:
            logging.warning("%s: Modell-Liste von %s nicht abrufbar (%s)", tier, ep["base_url"], error.get("code"))
        elif models and ep["model"] not in models:
            logging.warning(
                "⚠️  Rolle %s: Modell '%s' ist bei %s nicht verfügbar. Verfügbare Modelle: %s",
                tier, ep["model"], ep["base_url"], models,
            )


# Core Projects feature removed (issue #52, ADR-0004): the /api/projects CRUD and
# paper-project assignment endpoints are gone. The projects/paper_projects tables
# stay as dead data until a later release; read access for the plugin-side
# migration lives in host_services._CoreLibraryApi.get_core_projects.


# =============================================================================
# MAIN
# =============================================================================

#: How long a restarted process waits for its predecessor to free the port (#193).
RESTART_PORT_WAIT_SECONDS = 10.0


def wait_for_free_port(host: str, port: int, timeout: float = RESTART_PORT_WAIT_SECONDS) -> bool:
    """Block until ``host:port`` can be bound, at most ``timeout`` seconds.

    Only a restarted process calls it (``POST /api/app/restart``): the old one
    still answers the restart request and holds the port for a moment."""
    import socket

    deadline = time.monotonic() + timeout
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
                return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


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

    # Neustart aus der App (#193): der alte Prozess haelt den Port noch kurz;
    # der offene Browser-Tab laedt sich selbst neu, also kein neuer Tab.
    _RESTARTED = os.getenv("LOCALBIB_RESTARTED") == "1"
    os.environ.pop("LOCALBIB_RESTARTED", None)
    if _RESTARTED:
        wait_for_free_port("127.0.0.1", _PORT)

    # Browser automatisch öffnen (verzögert, bis der Server hochgefahren ist).
    # Im Dev-Modus (Hot-Reload via Editor) nicht erwünscht → nur im Frozen-Build
    # oder wenn explizit angefordert.
    if not _RESTARTED and (_FROZEN or os.getenv("LOCALBIB_OPEN_BROWSER") == "1"):
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(_URL)).start()

    uvicorn.run(app, host="127.0.0.1", port=_PORT)
