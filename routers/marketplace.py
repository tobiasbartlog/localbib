"""Router: the Marketplace — merged index + installed states (#189, ADR-0021).

Serves:
  GET  /api/marketplace                  cards: index entries + installed state + Dev-Suchpfad
  GET  /api/marketplace/assets/{path}    an icon/screenshot, cached so the browser
                                          never loads from GitHub directly
  POST /api/marketplace/install/{id}     download + install from the index (SSE, #191)
  POST /api/marketplace/install-file     install an uploaded Bundle (#191)
  POST /api/marketplace/dev-path         load a folder as Dev-Suchpfad (#191)

Thin orchestration (ADR-0006): ``marketplace_client`` fetches and caches the
index (network + disk, never raises); ``services.marketplace`` decides
compatibility and builds the cards (pure); ``plugin_loader``/``addon_catalog``
supply what is installed; ``addon_installer`` downloads, checks and renames a
Bundle into place (``services.addon_install`` decides what is safe). The
index cache is not a settings document — it is disposable and owned entirely
by ``marketplace_client``. The install endpoints persist ``plugins.json``
through ``settings_store`` and run its post-save chain, so an Add-on whose
Berechtigungen are already agreed to comes up without a restart.

**Install -> Zustimmung.** An install records the Add-on as switched on with
its version. Without consent to every Berechtigung it stays in
``consent_pending`` — installed, inactive — and the answer carries the
``consent`` request the SPA renders as the Zustimmungsdialog. Agreeing is
``POST /api/plugins/{id}/consent`` + ``/enable``; declining changes nothing.

**Update** (#193) is the same install of another version: a running Add-on
gets it as ``pending_update`` (active on the next start; with a new
Berechtigung only after ``/update/consent``), one that is not running switches
at once. The answer's ``update`` block tells the SPA which case it was.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import addon_installer
import marketplace_client
import plugin_loader
import plugins_config
import settings_store
from config import Config
from services import addon_catalog, addon_install, addon_lifecycle, marketplace
from services.addon_install import InstallError

logger = logging.getLogger(__name__)

router = APIRouter()

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
}


def _installed_by_id() -> dict[str, dict]:
    """id -> what ``services.marketplace`` needs of the installed state, for
    every Add-on the core finds on disk — Bundles and Dev-Suchpfade alike,
    whether currently loaded or not (an inactive or errored Add-on is still
    "installed" from the Marketplace's point of view)."""
    doc = Config.plugins_document()
    found = plugin_loader.catalog(doc)
    out: dict[str, dict] = {}
    for addon_id, candidate in found.items():
        described = plugin_loader.describe_one(addon_id, candidate, doc)
        out[addon_id] = {
            "version": described["version"],
            "source": described["source"],
            "dev_origin": described["dev_origin"],
            "state": described["state"],
            "name": described["name"],
            "tagline": described["tagline"],
            "permissions": described["permissions"],
            "enabled": described["enabled"],
            "missing_consent": described["missing_consent"],
            "needs_confirmation": described["needs_confirmation"],
            "origin": described["origin"],
            "sha256": described["sha256"],
            "error": described["error"],
            "previous_version": described["previous_version"],
            "rollback_available": described["rollback_available"],
            "pending_update": described["pending_update"],
            "pending_removal": described["pending_removal"],
        }
    return out


@router.get("/api/marketplace")
async def get_marketplace():
    """The merged Marketplace view: one card per Add-on, index entries first,
    Dev-Suchpfad-only Add-ons appended. Opening the Marketplace always tries
    the network first (``force=True``, #199) — a freshly published Add-on
    must show up now, not after the once-a-day window ``refresh_if_stale``
    uses for the version-check piggyback. Never fails on a dead network — it
    degrades to the disk cache (``offline: true``) or, without one yet, to an
    empty list (``has_index: false``) so the page always has something to say."""
    entry, offline = marketplace_client.fetch(force=True)
    index = entry["index"] if entry else None
    addons = (index or {}).get("addons") or []
    facts = plugin_loader.core_facts()
    cards = marketplace.merge_view(addons, _installed_by_id(), facts)
    index_date = None
    if entry is not None:
        index_date = datetime.fromtimestamp(entry["fetched_at"], tz=timezone.utc).isoformat()
    return {
        "addons": cards,
        "offline": offline,
        "has_index": entry is not None,
        "index_date": index_date,
        "core": facts.as_dict(),
    }


@router.get("/api/marketplace/assets/{rel_path:path}", include_in_schema=False)
async def marketplace_asset(rel_path: str):
    """One cached icon/screenshot — the browser never loads it from GitHub."""
    target = marketplace_client.cached_image(rel_path)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "error.marketplace.assetNotFound"})
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Install (#191)
# ---------------------------------------------------------------------------

class InstallRequest(BaseModel):
    version: str = ""


class DevPathRequest(BaseModel):
    path: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _index_addons() -> list:
    entry, _offline = marketplace_client.fetch()
    return ((entry or {}).get("index") or {}).get("addons") or []


async def _record_install(addon_id: str, version: str, *, trust: str, origin: str, sha256: str = "",
                          permissions: Optional[list[str]] = None) -> dict:
    """Persist the installed version, run the post-save chain (which loads it
    if its Berechtigungen are already agreed to) and answer with its state and
    the consent request.

    A first install is recorded switched on. A different version of an
    installed Bundle is an **update** (#193): the switch keeps as it was; a
    running Add-on gets the new version as ``pending_update`` (active on the
    next start, or after consent to what it adds), one that is not running
    switches at once. The answer's ``update`` says which case happened."""
    doc = Config.plugins_document()
    entry = plugins_config.ensure_entry(doc, addon_id)
    addon_lifecycle.cancel_removal(doc, addon_id)
    current = entry.get("version") or ""
    is_update = bool(version and current and current != version)
    running = plugin_loader.is_loaded(addon_id)
    explicit = origin in (plugins_config.ORIGIN_FILE, plugins_config.ORIGIN_DEV)
    if explicit:
        # A file or folder may claim any id: a consent given before Herkunft
        # was recorded stays with the Bundle it was given for, never passes
        # to this code.
        plugins_config.bind_unbound_consent(entry)
    # A Dev-Suchpfad shadows the Bundle; the Bundle's own Herkunft stays.
    source = None if origin == plugins_config.ORIGIN_DEV else {"origin": origin, "sha256": sha256}
    if source is not None and version:
        # This code may have replaced the kept predecessor's folder.
        addon_lifecycle.record_placed(doc, addon_id, version, source)
    if is_update:
        addon_lifecycle.switch_version(doc, addon_id, version, permissions or [], running=running, source=source)
    else:
        if version:
            entry["version"] = version
            entry["pending_update"] = None
        if source is not None:
            prov = plugins_config.provenance(origin, sha256)
            entry["source"] = {"origin": prov["origin"], "sha256": prov["sha256"]}
        entry["enabled"] = True
    if explicit and not entry.get("pending_update"):
        # Installs from a file or folder always need one explicit
        # confirmation (the dialog shows the warning and the checksum), even
        # with no Berechtigung: off until the consent call unless this very
        # code was confirmed before.
        found = plugin_loader.catalog(doc)
        if addon_id in found and addon_catalog.needs_confirmation(found[addon_id], entry):
            entry["enabled"] = False
    entry["error"] = None
    settings_store.save_plugins_document(doc)
    await settings_store.reload_runtime(settings_store.env_path())
    doc = Config.plugins_document()
    found = plugin_loader.catalog(doc)
    if addon_id not in found:  # a racing removal; nothing to describe
        raise InstallError(addon_install.ERR_FOLDER)
    addon = plugin_loader.describe_one(addon_id, found[addon_id], doc)
    consent = marketplace.consent_request(addon, trust, origin, sha256)
    update = None
    if is_update:
        staged = running and addon.get("pending_update") is not None
        missing = addon["pending_update"]["missing"] if staged else list(addon.get("missing_consent") or [])
        confirm = addon["pending_update"]["confirm"] if staged else bool(addon.get("needs_confirmation"))
        update = {"from": current, "to": version, "staged": staged, "missing": missing,
                  "confirm": confirm, "restart_required": staged}
        # The dialog of an update shows only the difference.
        consent = {**consent, "version": version, "update": True, "staged": staged, "confirm": confirm,
                   "permissions": marketplace.permission_rows(missing), "missing": missing}
    return {"addon": addon, "consent": consent, "update": update}


def _place(staged, manifest) -> None:
    """Move the Bundle into place. If it replaces the kept predecessor's
    folder, that folder's Herkunft is forgotten first and persisted, so
    whatever interrupts the install before ``_record_install`` leaves an
    unknown predecessor (no rollback target), never stale consent."""
    doc = Config.plugins_document()
    if addon_lifecycle.record_placed(doc, manifest.id, manifest.version, None):
        settings_store.save_plugins_document(doc)
    addon_installer.place(staged, manifest)


@router.post("/api/marketplace/install/{addon_id}")
async def install_from_index(addon_id: str, data: Optional[InstallRequest] = None):
    """Download the index artifact with byte progress (SSE, the Smart-Import
    dialect) and install it. Events: ``progress`` (``step`` download | verify |
    extract | activate, ``received``/``total`` bytes while downloading),
    then ``complete`` (``addon`` + ``consent``) or ``error`` (``code``,
    ``params``). Whatever fails — including a client that goes away — the
    work folder is discarded and the target stays untouched."""
    facts = plugin_loader.core_facts()
    index_entry = marketplace.find_entry(_index_addons(), addon_id)
    try:
        version, artifact = marketplace.install_target(index_entry, facts, (data.version if data else "") or "")
    except InstallError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail())
    declared = artifact.get("size")
    limit = addon_install.download_limit(declared)
    trust = marketplace.trust_of(index_entry)
    return StreamingResponse(
        _install_events(addon_id, str(version["version"]), artifact, limit, trust),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _install_events(addon_id: str, version: str, artifact: dict, limit: int, trust: str):
    """The SSE body of an index install (see ``install_from_index``)."""
    declared = artifact.get("size")
    total = declared if isinstance(declared, int) and not isinstance(declared, bool) else None
    work = addon_installer.new_workdir()
    try:
        yield _sse({"type": "progress", "step": "download", "received": 0, "total": total})
        result: dict = {}
        zip_path = work / "bundle.zip"
        # aclosing: a disconnect closes the download (and its open file) before
        # the ``finally`` below removes the work folder — Windows would refuse.
        async with contextlib.aclosing(addon_installer.download(
                str(artifact["url"]), zip_path, limit=limit, result=result)) as progress:
            async for received, announced in progress:
                yield _sse({"type": "progress", "step": "download", "received": received,
                            "total": announced or total})
        yield _sse({"type": "progress", "step": "verify"})
        if not addon_install.checksum_matches(str(artifact.get("sha256") or ""), result.get("sha256", "")):
            raise InstallError(addon_install.ERR_CHECKSUM)
        yield _sse({"type": "progress", "step": "extract"})
        manifest = addon_installer.extract(zip_path, work / "bundle")
        problem = addon_install.check_identity(manifest.id, manifest.version, addon_id, version)
        if problem is not None:
            raise problem
        _place(work / "bundle", manifest)
        yield _sse({"type": "progress", "step": "activate"})
        payload = await _record_install(addon_id, manifest.version, trust=trust, origin="index",
                                        permissions=[p.value for p in manifest.permissions])
        yield _sse({"type": "complete", **payload})
    except InstallError as exc:
        yield _sse({"type": "error", "code": exc.code, "params": exc.params})
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is still one clear event
        logger.error("Add-on-Installation %s fehlgeschlagen: %s", addon_id, exc, exc_info=True)
        yield _sse({"type": "error", "code": "error.marketplace.installFailed",
                    "params": {"message": str(exc)[:300]}})
    finally:
        addon_installer.discard(work)


@router.post("/api/marketplace/install-file")
async def install_from_file(file: UploadFile = File(...)):
    """Install an uploaded Bundle: the same steps as an index install minus
    the index checksum — the computed SHA-256 is returned for the user to
    compare. Always Drittanbieter: nothing vouches for a file."""
    work = addon_installer.new_workdir()
    try:
        zip_path = work / "bundle.zip"
        sha256, _size = addon_installer.save_stream(file.file, zip_path, limit=addon_install.MAX_BUNDLE_BYTES)
        manifest = addon_installer.extract(zip_path, work / "bundle")
        _place(work / "bundle", manifest)
        return await _record_install(manifest.id, manifest.version, trust="third-party",
                                     origin="file", sha256=sha256,
                                     permissions=[p.value for p in manifest.permissions])
    except InstallError as exc:
        raise HTTPException(status_code=422, detail=exc.as_detail())
    finally:
        addon_installer.discard(work)


@router.post("/api/marketplace/dev-path")
async def add_dev_path(data: DevPathRequest):
    """Aus Ordner laden: add a Bundle source folder to the Dev-Suchpfade in
    ``plugins.json``. It then shows under "Entwicklung" and, like any
    install, waits for the Zustimmung before it runs."""
    raw = (data.path or "").strip().strip('"')
    folder = Path(raw).expanduser() if raw else None
    if folder is None or not folder.is_absolute() or not (folder / addon_install.MANIFEST_FILENAME).is_file():
        raise HTTPException(status_code=422, detail={"code": addon_install.ERR_FOLDER, "params": {"path": raw}})
    folder = folder.resolve()
    found = addon_catalog.discover("", [str(folder)], {}, plugin_loader.core_facts())
    candidate = next(iter(found.values()), None)
    if candidate is None or candidate.manifest is None:
        message = (candidate.manifest_error or candidate.incompatible or "") if candidate else ""
        raise HTTPException(status_code=422, detail={"code": addon_install.ERR_MANIFEST,
                                                     "params": {"message": message}})
    if candidate.incompatible:
        raise HTTPException(status_code=409, detail={"code": addon_install.ERR_INCOMPATIBLE,
                                                     "params": {"reason": candidate.incompatible}})
    doc = Config.plugins_document()
    # Before the folder is on the path: a legacy consent of the same id stays
    # with the Bundle it was given for (see ``_record_install``).
    plugins_config.bind_unbound_consent(plugins_config.ensure_entry(doc, candidate.id))
    paths = doc.setdefault("dev_paths", [])
    if str(folder) not in paths:
        paths.append(str(folder))
    settings_store.save_plugins_document(doc)
    try:
        return await _record_install(candidate.id, "", trust="third-party", origin="dev")
    except InstallError as exc:
        raise HTTPException(status_code=422, detail=exc.as_detail())
