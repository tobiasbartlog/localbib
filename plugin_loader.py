"""The Add-on loader: brings the running core in line with ``plugins.json``.

Neutral runtime module (like ``settings_store``), imported by ``webapp.py``,
``settings_store`` and ``routers/plugins.py``. It owns what nothing else may
do: the two ``sys.path`` entries per active Add-on (the Bundle and its
``vendor/``), importing and un-importing the Add-on package, handing it the
host services its Berechtigungen unlock, and checking that its routes live
under ``/api/plugins/<id>/``. What exists on disk and whether it may load is
decided by the pure ``services.addon_catalog``; persisting the document is the
injected ``persist`` callable (``settings_store.save_plugins_document``), so
this module never imports the persistence layer.

**Error isolation** (ADR-0021, closes the follow-up bug of ADR-0009): every
step for one Add-on — Manifest, compatibility, import, activate, route prefix —
is guarded on its own. A failure becomes that Add-on's ``error`` in the
document and the loop goes on with the next one; neither the start nor the
router reconcile of the others is cut short.

**Boot marker**: before an Add-on is imported its id is written to
``boot_marker``; after it is ready the marker is cleared. A start that finds a
marker left behind knows that Add-on took the last run down with it, switches
it off and records why.

**Start-time housekeeping** (#193): before the first import, an agreed
``pending_update`` becomes the running version, queued ``pending_removals``
lose their Bundle folder (never the Add-on's data) and every Add-on keeps
exactly one predecessor. The rules are ``services.addon_lifecycle``.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import plugins_config
from config import Config
from context import registry
from plugin_api import API_VERSION, GATED_SERVICES, CoreSettings, Permission, PluginManifest
from services import addon_catalog, addon_frontend, addon_lifecycle

logger = logging.getLogger(__name__)

Persist = Callable[[dict], None]

_persist: Optional[Persist] = None
_core_version: Optional[str] = None


@dataclass
class _Loaded:
    path: Path
    source: str
    sys_path: list[str]
    manifest: Optional[PluginManifest] = None


# id -> what the loader added for it
_loaded: dict[str, _Loaded] = {}


def configure(*, persist: Persist, core_version: Optional[str]) -> None:
    """Wire the persistence callable and the release version (from ``webapp``)."""
    global _persist, _core_version
    _persist = persist
    _core_version = core_version


def is_frozen() -> bool:
    """True in the PyInstaller build. One switch, so tests can flip exactly one.

    Loading itself is identical in both modes — a Dev-Suchpfad works in the
    exe as in the source tree. The one difference is ``min_core``: only a
    release build has a version to compare against."""
    return bool(getattr(sys, "frozen", False))


def core_facts() -> addon_catalog.CoreFacts:
    frozen = is_frozen()
    return addon_catalog.CoreFacts(
        api_version=API_VERSION,
        python_tag=addon_catalog.python_tag(),
        core_version=_core_version if frozen else None,
        frozen=frozen,
    )


def dev_paths(doc: dict) -> list[str]:
    out: list[str] = []
    for path in [*doc.get("dev_paths", []), *Config.PLUGIN_DEV_PATHS]:
        if path not in out:
            out.append(path)
    return out


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def dev_origin(candidate: addon_catalog.Candidate) -> Optional[str]:
    """Where a Dev-Suchpfad comes from: ``env`` (``LOCALBIB_PLUGIN_DEV_PATHS``
    — the app cannot forget it, only switch it off) or ``document`` (added in
    the app, ``plugins.json``). ``None`` for a Bundle."""
    if candidate.source != addon_catalog.SOURCE_DEV:
        return None
    if any(same_path(str(candidate.path), p) for p in Config.PLUGIN_DEV_PATHS):
        return "env"
    return "document"


def catalog(doc: Optional[dict] = None) -> dict[str, addon_catalog.Candidate]:
    doc = doc if doc is not None else Config.plugins_document()
    return addon_catalog.discover(Config.PLUGIN_DIR, dev_paths(doc), doc, core_facts())


def is_loaded(addon_id: str) -> bool:
    return addon_id in _loaded


def loaded_addons() -> dict[str, tuple[Path, PluginManifest]]:
    """``id -> (Bundle folder, Manifest)`` of every loaded Bundle/dev Add-on."""
    return {i: (l.path, l.manifest) for i, l in _loaded.items() if l.manifest is not None}


def frontends() -> list[dict]:
    """What the SPA loads per loaded Add-on (``services.addon_frontend.describe``).

    One list for both readers: ``GET /api/plugins/frontend`` (runtime
    switches) and the index page, which renders it as ``window.LB_ADDONS`` so
    the boot needs no round trip before it can load the scripts."""
    out = []
    for addon_id, (_path, manifest) in sorted(loaded_addons().items()):
        entry = addon_frontend.describe(addon_id, manifest)
        if entry is not None:
            out.append(entry)
    return out


def _save(doc: dict) -> None:
    if _persist is None:
        raise RuntimeError("plugin_loader.configure() was not called")
    _persist(doc)


# ---------------------------------------------------------------------------
# Settings service handed to an Add-on
# ---------------------------------------------------------------------------

class AddonSettings:
    """``plugin_api.SettingsApi`` for one Add-on: ``get``/``set`` in its own
    namespace of ``plugins.json``, ``core()`` only with ``settings.core``."""

    def __init__(self, addon_id: str, manifest: PluginManifest) -> None:
        self._id = addon_id
        self._defaults = {s.key: s.default for s in manifest.settings}
        self._core = manifest.declares(Permission.SETTINGS_CORE)

    def get(self, key: str) -> Optional[Any]:
        entry = (Config.PLUGINS_DOCUMENT.get("plugins") or {}).get(self._id) or {}
        values = entry.get("settings") or {}
        return values[key] if key in values else self._defaults.get(key)

    def set(self, key: str, value: Any) -> None:
        doc = Config.plugins_document()
        plugins_config.ensure_entry(doc, self._id)["settings"][str(key)] = value
        _save(doc)

    def core(self) -> Optional[CoreSettings]:
        if not self._core:
            return None
        return CoreSettings(
            mailto=Config.polite_mailto(),
            openalex_api_key=Config.OPENALEX_API_KEY or "",
            ui_language=Config.UI_LANGUAGE,
            base_dir=Config.BASE_DIR,
        )


class AddonLibrary:
    """``plugin_api.LibraryApi`` for one Add-on: the host library as is for
    every read, and its one writing method, ``create_by_doi``, gated by
    ``library.write`` (``PermissionError`` without it) with the Item's origin
    pinned to the Add-on id. The handle itself exists only with
    ``library.read`` (``GATED_SERVICES``); write rides on it."""

    def __init__(self, host: Any, addon_id: str, manifest: PluginManifest) -> None:
        self._host = host
        self._id = addon_id
        self._write = manifest.declares(Permission.LIBRARY_WRITE)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def create_by_doi(self, doi: str, *, title: str = "", authors: Any = None,
                      year: Optional[int] = None, journal: str = "", abstract: str = "") -> dict:
        if not self._write:
            raise PermissionError(f"Add-on {self._id!r} did not declare library.write")
        return self._host.create_by_doi(doi, title=title, authors=authors, year=year,
                                        journal=journal, abstract=abstract, source=self._id)


def _services_for(addon_id: str, manifest: PluginManifest) -> dict[str, Any]:
    """Host services gated by the Manifest: undeclared means ``None``."""
    host = registry.host_services()
    declared = {p for p in manifest.permissions}
    services: dict[str, Any] = {
        name: (host.get(name) if perm in declared else None) for name, perm in GATED_SERVICES.items()
    }
    if services.get("library") is not None:
        services["library"] = AddonLibrary(services["library"], addon_id, manifest)
    services["settings"] = AddonSettings(addon_id, manifest)
    return services


# ---------------------------------------------------------------------------
# Import bookkeeping
# ---------------------------------------------------------------------------

class _LoadError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


def _purge_modules(addon_id: str) -> None:
    for name in [m for m in sys.modules if m == addon_id or m.startswith(addon_id + ".")]:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _drop_paths(entries: list[str]) -> None:
    for entry in entries:
        while entry in sys.path:
            sys.path.remove(entry)


def _module_inside(module: Any, folder: Path) -> bool:
    origin = getattr(module, "__file__", None)
    if not origin:
        return False
    try:
        Path(origin).resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _check_prefix(addon_id: str) -> None:
    prefix = f"/api/plugins/{addon_id}"
    for router in registry.routers_of(addon_id):
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", "") or ""
            if path != prefix and not path.startswith(prefix + "/"):
                raise _LoadError("error.plugins.routePrefix", f"{path} is outside {prefix}/")


async def _load(candidate: addon_catalog.Candidate, doc: dict) -> None:
    """Import and activate one Add-on; record the outcome in ``doc`` (persisted)."""
    addon_id = candidate.id
    manifest = candidate.manifest
    assert manifest is not None
    entry = plugins_config.ensure_entry(doc, addon_id)
    entries = [str(candidate.path), str(candidate.path / "vendor")]

    existing = sys.modules.get(addon_id)
    if existing is not None and not _module_inside(existing, candidate.path):
        entry["error"] = {"code": "error.plugins.moduleConflict",
                          "message": f"module name {addon_id!r} is already taken"}
        _save(doc)
        return

    doc["boot_marker"] = addon_id
    _save(doc)
    added = [e for e in entries if e not in sys.path]
    sys.path.extend(added)
    importlib.invalidate_caches()
    error: Optional[dict] = None
    try:
        try:
            module = importlib.import_module(addon_id)
        except Exception as exc:
            raise _LoadError("error.plugins.importFailed", f"{type(exc).__name__}: {exc}") from exc
        if not _module_inside(module, candidate.path):
            raise _LoadError("error.plugins.moduleConflict", f"{addon_id} resolved outside its Bundle")
        try:
            await registry.activate(addon_id, services=_services_for(addon_id, manifest))
        except Exception as exc:
            raise _LoadError("error.plugins.activateFailed", f"{type(exc).__name__}: {exc}") from exc
        _check_prefix(addon_id)
    except _LoadError as exc:
        logger.warning("Add-on %s konnte nicht geladen werden: %s", addon_id, exc.message or exc.code)
        error = {"code": exc.code, "message": exc.message[:1000]}
        try:
            await registry.deactivate(addon_id)
        except Exception as teardown_exc:  # the Add-on's own teardown; state is reset anyway
            logger.warning("Add-on %s: deactivate nach Fehler: %s", addon_id, teardown_exc)
        _drop_paths(added)
        _purge_modules(addon_id)
    else:
        _loaded[addon_id] = _Loaded(path=candidate.path, source=candidate.source, sys_path=added, manifest=manifest)
    entry["error"] = error
    doc["boot_marker"] = ""
    _save(doc)


async def _unload(addon_id: str) -> None:
    loaded = _loaded.pop(addon_id, None)
    try:
        await registry.deactivate(addon_id)
    except Exception as exc:
        logger.warning("Add-on %s: deactivate fehlgeschlagen: %s", addon_id, exc)
    if loaded is not None:
        _drop_paths(loaded.sys_path)
    _purge_modules(addon_id)


# ---------------------------------------------------------------------------
# The two entry points
# ---------------------------------------------------------------------------

def _recover_boot_marker(doc: dict) -> bool:
    marker = doc.get("boot_marker") or ""
    if not marker:
        return False
    entry = plugins_config.ensure_entry(doc, marker)
    entry["enabled"] = False
    entry["error"] = {"code": "error.plugins.crashedDuringLoad",
                      "message": "LocalBib stopped while this Add-on was loading; it was switched off."}
    doc["boot_marker"] = ""
    logger.warning("Add-on %s war beim letzten Start am Laden, als LocalBib abbrach — deaktiviert.", marker)
    return True


def _bundle_folders(root: Path) -> dict[str, list[str]]:
    """``id -> [version folders]`` of every Bundle under ``root`` (dot-folders
    such as ``.staging`` are not Add-ons)."""
    out: dict[str, list[str]] = {}
    if not root.is_dir():
        return out
    for addon_dir in root.iterdir():
        if addon_dir.is_dir() and not addon_dir.name.startswith("."):
            out[addon_dir.name] = [v.name for v in addon_dir.iterdir() if v.is_dir() and not v.name.startswith(".")]
    return out


def _delete(folder: Path, root: Path) -> bool:
    """Delete one Bundle folder strictly below ``root``; ``False`` if it stays
    (still locked, say) — the next start tries again."""
    try:
        folder.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    if folder.resolve() == root.resolve():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def _housekeeping(doc: dict) -> bool:
    """Start-time lifecycle (ADR-0021, #193), before anything is imported:
    activate agreed pending versions, delete queued removals (the Bundle
    only), keep exactly one predecessor. The rules are
    ``services.addon_lifecycle``; this applies them to the disk. Returns
    whether the document changed."""
    changed = bool(addon_lifecycle.apply_pending(doc))
    root = Path(Config.PLUGIN_DIR) if Config.PLUGIN_DIR else None
    on_disk = _bundle_folders(root) if root is not None else {}
    removals, versions = addon_lifecycle.prune_plan(doc, on_disk)
    for addon_id in removals:
        if _delete(root / addon_id, root):
            logger.info("Add-on %s entfernt.", addon_id)
    for addon_id in list(doc.get("pending_removals") or []):
        if root is None or not (root / addon_id).exists():
            addon_lifecycle.forget_after_removal(doc, addon_id)
            changed = True
    for addon_id, version in versions:
        _delete(root / addon_id / version, root)
    return changed


async def sync_all(*, startup: bool = False) -> None:
    """Bring the Add-ons in line with their switches. Never raises for a
    single Add-on; the caller reconciles the routers afterwards."""
    doc = Config.plugins_document()
    if startup:
        changed = _recover_boot_marker(doc)
        try:
            changed = _housekeeping(doc) or changed
        except Exception as exc:  # a locked folder must not stop the start
            logger.warning("Add-on-Aufraeumen beim Start fehlgeschlagen: %s", exc)
        if changed:
            _save(doc)
    found = catalog(doc)
    plugins = doc.get("plugins") or {}

    desired: dict[str, addon_catalog.Candidate] = {}
    for addon_id, candidate in found.items():
        entry = plugins.get(addon_id) or plugins_config.empty_entry()
        if addon_catalog.should_load(candidate, entry):
            desired[addon_id] = candidate

    for addon_id in list(_loaded):
        target = desired.get(addon_id)
        if target is None or target.path != _loaded[addon_id].path:
            await _unload(addon_id)
    for addon_id, candidate in desired.items():
        if addon_id in _loaded:
            continue
        try:
            await _load(candidate, doc)
        except Exception as exc:  # persistence failed — keep the others going
            logger.warning("Add-on %s: Laden abgebrochen: %s", addon_id, exc)


async def teardown_all() -> None:
    """Deactivate everything (shutdown). Per Add-on guarded, like the load."""
    for addon_id in list(_loaded):
        await _unload(addon_id)


# ---------------------------------------------------------------------------
# Listing (GET /api/plugins)
# ---------------------------------------------------------------------------

def describe_one(addon_id: str, candidate: addon_catalog.Candidate, doc: dict) -> dict:
    entry = (doc.get("plugins") or {}).get(addon_id) or plugins_config.empty_entry()
    manifest = candidate.manifest
    state, error = addon_catalog.state_of(candidate, entry, addon_id in _loaded)
    is_bundle = candidate.source == addon_catalog.SOURCE_BUNDLE
    # An incompatible Bundle has no readable Manifest; its folder still names
    # the version, which the Marketplace needs to offer the fitting update.
    version = manifest.version if manifest else (candidate.path.name if is_bundle else "")
    previous = entry.get("previous_version") or ""
    previous_valid = is_bundle and previous in candidate.versions
    pending = entry.get("pending_update") or None
    prov = addon_catalog.provenance(candidate, entry)
    return {
        "id": addon_id,
        "name": manifest.name if manifest else addon_id,
        "version": version,
        "tagline": manifest.tagline if manifest else "",
        "source": candidate.source,
        "dev_origin": dev_origin(candidate),
        "path": str(candidate.path),
        "enabled": bool(entry.get("enabled")),
        "state": state,
        "error": error,
        "permissions": sorted(p.value for p in manifest.permissions) if manifest else [],
        "missing_consent": addon_catalog.missing_consent(candidate, entry),
        # Herkunft (origin index/file/dev + the file's checksum) and whether the
        # Zustimmungsdialog must come before it may run — also with no
        # Berechtigung, for a file or folder the user has not confirmed.
        "origin": prov["origin"],
        "sha256": prov["sha256"],
        "needs_confirmation": addon_catalog.needs_confirmation(candidate, entry),
        # An installed Bundle a Dev-Suchpfad of the same id now shadows and
        # switched off (see docstring) — the Marketplace explains this rather
        # than showing a bare "inactive".
        "dev_shadow_disabled": addon_catalog.dev_shadow_disabled(candidate, entry),
        "consent": entry.get("consent"),
        "versions": list(candidate.versions),
        # Lifecycle (#193): rollback target, the version waiting for the next
        # start (and the Berechtigungen it still needs), a queued removal.
        "previous_version": previous if previous_valid else "",
        # Whether the rollback endpoint would actually succeed: the kept
        # predecessor's folder must exist AND its Herkunft must be known
        # (#4e25609) — a document written before that fix has a
        # previous_version with no recorded previous_source.
        "rollback_available": bool(previous_valid and addon_lifecycle.previous_provenance(entry) is not None),
        "pending_update": ({"version": pending["version"], "missing": addon_lifecycle.pending_missing(entry),
                            "confirm": addon_lifecycle.pending_needs_confirmation(entry),
                            "origin": pending.get("origin") or plugins_config.ORIGIN_INDEX,
                            "sha256": pending.get("sha256") or ""}
                           if is_bundle and pending and pending.get("version") in candidate.versions else None),
        "pending_removal": addon_id in (doc.get("pending_removals") or []),
    }


def describe() -> list[dict]:
    doc = Config.plugins_document()
    found = catalog(doc)
    return [describe_one(addon_id, found[addon_id], doc) for addon_id in sorted(found)]
