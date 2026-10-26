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
from datetime import datetime, timezone
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
    # The Windows .exe has no console (#214): stdout/stderr are None, and the
    # first library that writes to them (uvicorn asks isatty()) would fail.
    # Before anything else may write, they get the rotating log file of #211.
    from logging_setup import log_dir_for as _log_dir_for, redirect_missing_std_streams
    redirect_missing_std_streams(_log_dir_for(str(_ENV_PATH.parent)))
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text("", encoding="utf-8")
else:
    _BUNDLE_DIR = _Path(__file__).parent.resolve()

_SCRIPT_DIR = _BUNDLE_DIR
_PROCESS_ENV_KEYS = frozenset(os.environ)  # real env, before the .env (SET-B5)
load_dotenv(_ENV_PATH, interpolate=False)


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
import detail_layout
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
settings_store.remember_process_env(_PROCESS_ENV_KEYS)
# An empty value means "use the default", not "the current directory". An
# unusable one (control character, not absolute) falls back to the default
# instead of killing the console-less .exe at import; the Startup check tells
# the user that their folder was ignored.
BASE_DIR, _base_dir_notice = Config.resolve_base_dir(os.getenv("LITERATUR_BASE_DIR", ""))
if _base_dir_notice:
    logging.warning("LITERATUR_BASE_DIR %r ignored (%s), using %s",
                    _base_dir_notice["params"]["value"],
                    _base_dir_notice["params"]["reason"], BASE_DIR)
    Config.STARTUP_NOTICES = [_base_dir_notice]
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


class SpaStaticFiles(StaticFiles):
    """``/static`` with a fixed JavaScript type for scripts and modules.

    Starlette takes the type from ``mimetypes``, which on Windows reads the
    registry: a machine that maps ``.js`` to ``text/plain`` - or does not know
    ``.mjs`` at all, as Python 3.11 in the frozen build does not - would serve
    the vendored PDF.js (#229) with a type the browser refuses for a module
    ``import()`` and a module worker. The suffix decides here, not the machine.

    ES modules are served ``no-cache``: their relative imports cannot carry
    the ``?v=`` stamp, so after an update the browser must revalidate them
    (a 304 via ETag) instead of pairing a fresh app.js with a stale module.
    """

    JS_SUFFIXES = (".js", ".mjs")

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(full_path).lower()
        if path.endswith(self.JS_SUFFIXES) and "content-type" in response.headers:
            response.headers["content-type"] = "text/javascript; charset=utf-8"
        if path.endswith(".mjs"):
            response.headers["cache-control"] = "no-cache"
        return response


app.mount("/static", SpaStaticFiles(directory=str(SCRIPT_DIR / "static")), name="static")
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
from routers.version import run_exit_hooks as _run_exit_hooks
from routers.banner import router as _banner_router
from routers.license import router as _license_router
from routers.settings import router as _settings_router, set_reconcile_hook as _set_settings_reconcile_hook
from routers.categories import router as _categories_router
from routers.export import router as _export_router
from routers.custom_fields import router as _custom_fields_router
from routers.bibtex_import import router as _bibtex_import_router
from routers.paper_by_doi import router as _paper_by_doi_router
from routers.papers import router as _papers_router
from routers.metadata_lookup import router as _metadata_lookup_router
from routers.annotations import router as _annotations_router
from routers.collections import router as _collections_router
from routers.validate import router as _validate_router
from routers.references import router as _references_router
from routers.research_chat import router as _research_chat_router
from routers.analysis import router as _analysis_router
from routers.duplicates import router as _duplicates_router
from routers.maintenance import router as _maintenance_router
from routers.refresh_review import router as _refresh_review_router
from routers.diagnosis import router as _diagnosis_router
from routers.search import router as _search_router
from routers.migration import router as _migration_router
from routers.llm import router as _llm_router, start_startup_check as _start_llm_check
from routers.plugins import router as _plugins_router
from routers.marketplace import router as _marketplace_router
from routers import backup as _backup_router_mod

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

@app.exception_handler(settings_store.EnvLocked)
async def _env_locked_handler(request: Request, exc: settings_store.EnvLocked):
    """The .env is held by another process: nothing was changed (423)."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=423, content={"detail": {"code": "error.settings.locked"}})


app.include_router(_stats_router)
app.include_router(_version_router)
app.include_router(_banner_router)
app.include_router(_license_router)
app.include_router(_settings_router)
app.include_router(_categories_router)
app.include_router(_export_router)
app.include_router(_custom_fields_router)
app.include_router(_bibtex_import_router)
# Literal /api/papers/by-doi… before the parametric /api/papers/{paper_id} family.
app.include_router(_paper_by_doi_router)
app.include_router(_papers_router)
app.include_router(_metadata_lookup_router)
app.include_router(_annotations_router)
app.include_router(_collections_router)
app.include_router(_validate_router)
app.include_router(_references_router)
app.include_router(_research_chat_router)
app.include_router(_analysis_router)
app.include_router(_import_router)
app.include_router(_duplicates_router)
app.include_router(_maintenance_router)
app.include_router(_refresh_review_router)
app.include_router(_diagnosis_router)
app.include_router(_search_router)
app.include_router(_migration_router)
app.include_router(_llm_router)
app.include_router(_plugins_router)
app.include_router(_marketplace_router)
app.include_router(_backup_router_mod.router)

# Add-on loader (ADR-0021): persists through the one plugins.json writer;
# the release version feeds the min_core check of a frozen build.
plugin_loader.configure(persist=settings_store.save_plugins_document, core_version=APP_VERSION)
# Backups: the version goes into each manifest, the restore steps of its
# README.txt come from the bundled catalogs.
_backup_router_mod.configure(app_version=APP_VERSION, locales_dir=str(SCRIPT_DIR / "static" / "locales"))


# =============================================================================
# SPA ENTRY POINT
# =============================================================================

def _ui_prefs() -> dict:
    """The server-held look of this installation, read from the .env file the
    settings router writes (not from ``os.environ``, which keeps stale values)."""
    held = settings_store.read_env(_Path(Config.ENV_PATH), {"DARK_MODE", "THESIS_MODE", "DETAIL_LAYOUT"})
    return {"darkMode": held.get("DARK_MODE", ""), "thesisMode": held.get("THESIS_MODE", ""),
            # The detail layout (#230): always the effective, normalized one.
            "detailLayout": detail_layout.effective(held.get("DETAIL_LAYOUT")),
            "detailLayoutRules": detail_layout.rules()}


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
         "addons": plugin_loader.frontends(),
         # Dark mode / thesis area (#208): the .env is the truth, rendered in
         # so the first frame is right in any browser. "" = not set yet.
         "prefs": _ui_prefs()},
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
        _run_exit_hooks()  # the .exe's tray icon leaves with the process (#214)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_shutdown, daemon=True).start()
    return {"status": "ok", "message": "app.shuttingDown"}


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
        db.set_app_setting("license_first_run_at", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())


@app.on_event("startup")
async def _start_auto_backup() -> None:
    """The automatic backup schedule: a background thread that looks every
    few minutes whether a backup is due (``routers/backup.py``)."""
    _backup_router_mod.start_schedule()


@app.on_event("shutdown")
async def _stop_auto_backup() -> None:
    _backup_router_mod.stop_schedule()


@app.on_event("startup")
async def _start_llm_startup_check() -> None:
    """Startup check of the AI models (#209): ask every bound role's
    connection for its model list — in the background, the start does not
    wait. A role whose model is gone, whose key is rejected or whose host does
    not answer surfaces now, in the SPA's popup, instead of mid-import."""
    _start_llm_check()


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

    # Rotating log file in <settings folder>/logs (#211). In the source tree the
    # console keeps its output too; attaching a file handler to a handler-less
    # root would otherwise swallow the stderr fallback.
    from logging_setup import console_handler, log_dir_for, quiet_url_loggers, setup_file_logging
    if not _FROZEN and not logging.getLogger().handlers:
        logging.getLogger().addHandler(console_handler())
    setup_file_logging(log_dir_for(os.path.dirname(Config.ENV_PATH)))
    quiet_url_loggers()

    _PORT = int(os.getenv("LOCALBIB_PORT", "8000"))
    _URL = f"http://localhost:{_PORT}"

    # The Windows .exe runs without a console (#214, ADR-0023): the starter
    # takes over - running-instance check, server thread, tray icon, browser.
    # The source tree (and a frozen build elsewhere) keeps the path below.
    if _FROZEN and sys.platform == "win32":
        import starter
        sys.exit(starter.run_frozen(
            app, port=_PORT, argv=sys.argv[1:], bundle_dir=str(SCRIPT_DIR),
            wait_for_free_port=lambda port: wait_for_free_port("127.0.0.1", port),
        ))

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
