"""Router: the Add-ons — state, switches, consent and their settings.

Serves:
  GET  /api/plugins                    every Add-on with its state + the core's facts
  GET  /api/plugins/frontend           what the SPA loads per active Add-on (#187)
  GET  /plugins/{id}/static/{path}     a file of an active Add-on's ``frontend/``
  POST /api/plugins/{id}/enable        switch on (needs consent to every Berechtigung)
  POST /api/plugins/{id}/disable       switch off
  POST /api/plugins/{id}/consent       agree to the Manifest's Berechtigungen
  POST /api/plugins/{id}/update/consent  agree to what a downloaded update adds (#193)
  POST /api/plugins/{id}/rollback      back to the kept predecessor (#193)
  POST /api/plugins/{id}/remove        queue the Bundle for deletion on the next start (#193)
  GET  /api/plugins/{id}/settings      the Add-on's settings (secrets masked)
  PUT  /api/plugins/{id}/settings      change them (``null`` = unchanged)

``GET /api/plugins/nav`` stays in ``routers/settings.py``; it lists what the
loaded Bundles registered. Thin orchestration
(ADR-0006): ``services.addon_catalog`` decides, ``plugin_loader`` loads,
``settings_store`` persists and runs the one post-save chain — which also
re-syncs the plugins, so a switch takes effect without a restart. Errors are
**codes** (ADR-0018).

States: ``active``, ``inactive``, ``error``, ``incompatible``,
``consent_pending`` (switched on, but a Berechtigung lacks consent); ``source``
is ``bundle`` or ``dev`` (a Dev-Suchpfad).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import plugin_loader
import plugins_config
import settings_store
from config import Config
from services import addon_catalog, addon_frontend, addon_lifecycle

router = APIRouter()

_MEDIA_TYPES = {".js": "text/javascript", ".css": "text/css", ".json": "application/json"}


class ConsentRequest(BaseModel):
    permissions: list[str] = []


class AddonSettingsUpdate(BaseModel):
    values: dict[str, Any] = {}


def _not_found(addon_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "error.plugins.notFound", "params": {"id": addon_id}})


def _lookup(addon_id: str) -> tuple[addon_catalog.Candidate, dict]:
    """``(candidate, doc)`` of an Add-on the core found; 404 otherwise."""
    doc = Config.plugins_document()
    found = plugin_loader.catalog(doc)
    if addon_id in found:
        return found[addon_id], doc
    raise _not_found(addon_id)


def _one(addon_id: str) -> dict:
    doc = Config.plugins_document()
    found = plugin_loader.catalog(doc)
    if addon_id not in found:
        raise _not_found(addon_id)
    return plugin_loader.describe_one(addon_id, found[addon_id], doc)


async def _save_and_reload(doc: dict) -> None:
    settings_store.save_plugins_document(doc)
    await settings_store.reload_runtime(settings_store.env_path())


@router.get("/api/plugins")
async def list_plugins():
    return {"plugins": plugin_loader.describe(), "core": plugin_loader.core_facts().as_dict()}


@router.get("/api/plugins/frontend")
async def list_plugin_frontends():
    """Per active Add-on: assets, stylesheet, script, locale files (lang -> URL),
    ``default_language``, ``version`` and its nav route. Only loaded Add-ons are
    listed — an inactive or failed one has nothing the SPA may load."""
    return {"addons": plugin_loader.frontends()}


@router.get("/plugins/{addon_id}/static/{rel_path:path}", include_in_schema=False)
async def plugin_static(addon_id: str, rel_path: str):
    """One file below the active Add-on's ``frontend/`` — nothing else.

    ``no-cache`` makes the browser revalidate (ETag/Last-Modified), so a
    Dev-Suchpfad edit or an update is picked up on the next load."""
    loaded = plugin_loader.loaded_addons().get(addon_id)
    target = addon_frontend.resolve_file(loaded[0], rel_path) if loaded else None
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "error.plugins.fileNotFound", "params": {"id": addon_id}})
    # Windows' registry may map .js to text/plain; the three types the SPA loads are pinned.
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.post("/api/plugins/{addon_id}/enable")
async def enable_plugin(addon_id: str):
    candidate, doc = _lookup(addon_id)
    if candidate.incompatible:
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.incompatible", "params": {"id": addon_id, "reason": candidate.incompatible}})
    if candidate.manifest is None:
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.manifestInvalid", "params": {"id": addon_id}})
    entry = plugins_config.ensure_entry(doc, addon_id)
    missing = addon_catalog.missing_consent(candidate, entry)
    if addon_catalog.needs_confirmation(candidate, entry):
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.consentRequired", "params": {"id": addon_id, "permissions": missing}})
    if candidate.source == addon_catalog.SOURCE_BUNDLE:
        entry["version"] = candidate.path.name
    entry["enabled"] = True
    entry["error"] = None
    addon_lifecycle.cancel_removal(doc, addon_id)
    await _save_and_reload(doc)
    return _one(addon_id)


@router.post("/api/plugins/{addon_id}/disable")
async def disable_plugin(addon_id: str):
    _candidate, doc = _lookup(addon_id)
    entry = plugins_config.ensure_entry(doc, addon_id)
    entry["enabled"] = False
    entry["error"] = None
    await _save_and_reload(doc)
    return _one(addon_id)


@router.post("/api/plugins/{addon_id}/consent")
async def consent_plugin(addon_id: str, data: ConsentRequest):
    """Agree to the Manifest's Berechtigungen. The request names the list the
    user saw; if the Manifest now declares more, the dialog was stale (409).
    The consent is recorded for this code's Herkunft (index, this file's
    checksum, this folder) — other code claiming the id finds none."""
    candidate, doc = _lookup(addon_id)
    if candidate.manifest is None:
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.consentNotApplicable", "params": {"id": addon_id}})
    declared = sorted(p.value for p in candidate.manifest.permissions)
    entry = plugins_config.ensure_entry(doc, addon_id)
    prov = addon_catalog.provenance(candidate, entry)
    # An update's dialog shows only the difference; what was agreed before
    # for the same Herkunft counts as seen.
    unseen = sorted(set(declared) - set(data.permissions) - addon_lifecycle.granted_for(entry, prov))
    if unseen:
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.consentStale", "params": {"id": addon_id, "permissions": unseen}})
    entry["consent"] = {**plugins_config.empty_consent(), "version": candidate.manifest.version,
                        "permissions": declared}
    plugins_config.bind_consent(entry, prov)
    await _save_and_reload(doc)
    return _one(addon_id)


# ---------------------------------------------------------------------------
# Lifecycle (#193): update consent, rollback, removal
# ---------------------------------------------------------------------------

def _require_bundle(candidate: addon_catalog.Candidate, addon_id: str) -> None:
    if candidate.source != addon_catalog.SOURCE_BUNDLE:
        raise HTTPException(status_code=409, detail={"code": "error.plugins.notABundle", "params": {"id": addon_id}})


@router.post("/api/plugins/{addon_id}/update/consent")
async def consent_pending_update(addon_id: str, data: ConsentRequest):
    """Agree to the Berechtigungen a downloaded update adds. The old version
    keeps running; the new one becomes active on the next start."""
    _candidate, doc = _lookup(addon_id)
    try:
        unseen = addon_lifecycle.agree_to_update(doc, addon_id, data.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "params": {"id": addon_id}})
    if unseen:
        raise HTTPException(status_code=409, detail={
            "code": "error.plugins.consentStale", "params": {"id": addon_id, "permissions": unseen}})
    await _save_and_reload(doc)
    return {**_one(addon_id), "restart_required": True}


@router.post("/api/plugins/{addon_id}/rollback")
async def rollback_plugin(addon_id: str):
    """Back to the one kept predecessor. A running Add-on switches on the next
    start (``restart_required``); one that is not running switches now."""
    candidate, doc = _lookup(addon_id)
    _require_bundle(candidate, addon_id)
    entry = plugins_config.ensure_entry(doc, addon_id)
    try:
        target = addon_lifecycle.rollback_target(entry, candidate.versions)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "params": {"id": addon_id}})
    running = plugin_loader.is_loaded(addon_id)
    # The predecessor ran before, so its Berechtigungen were agreed to — for
    # its own Herkunft, which switch_version takes from ``previous_source``: a
    # file that replaced the kept folder since then still needs confirmation.
    addon_lifecycle.switch_version(doc, addon_id, target, sorted(addon_lifecycle.granted(entry)), running=running)
    await _save_and_reload(doc)
    return {**_one(addon_id), "restart_required": running}


@router.post("/api/plugins/{addon_id}/remove")
async def remove_plugin(addon_id: str):
    """Entfernen: switch off at once; the Bundle is deleted on the next start
    (Windows keeps loaded files). A Dev-Suchpfad is only forgotten — its
    source folder is never deleted. Add-on data is never touched."""
    candidate, doc = _lookup(addon_id)
    if candidate.source == addon_catalog.SOURCE_DEV:
        if plugin_loader.dev_origin(candidate) == "env":
            # LOCALBIB_PLUGIN_DEV_PATHS is not the app's to forget; it can
            # only be switched off here.
            raise HTTPException(status_code=409, detail={
                "code": "error.plugins.devPathFromEnv", "params": {"id": addon_id, "path": str(candidate.path)}})
        folder = str(candidate.path)
        doc["dev_paths"] = [p for p in doc.get("dev_paths") or [] if not plugin_loader.same_path(p, folder)]
        # The id's entry is shared with an installed Bundle the folder may have
        # shadowed: that one keeps its switch. Only an id nothing provides any
        # more is switched off.
        if addon_id not in plugin_loader.catalog(doc):
            plugins_config.ensure_entry(doc, addon_id)["enabled"] = False
        await _save_and_reload(doc)
        return {"id": addon_id, "removed": True, "restart_required": False}
    addon_lifecycle.mark_removal(doc, addon_id)
    await _save_and_reload(doc)
    return {**_one(addon_id), "restart_required": True}


# ---------------------------------------------------------------------------
# Settings in the Add-on's own namespace
# ---------------------------------------------------------------------------

def _fields(candidate: addon_catalog.Candidate, addon_id: str) -> list[dict]:
    if candidate.manifest is None:
        return []
    return [{"key": s.key, "type": s.type, "label": s.label, "default": s.default}
            for s in candidate.manifest.settings]


def _settings_payload(addon_id: str, candidate: addon_catalog.Candidate, doc: dict) -> dict:
    fields = _fields(candidate, addon_id)
    stored = ((doc.get("plugins") or {}).get(addon_id) or {}).get("settings") or {}
    secrets = {f["key"] for f in fields if f["type"] == "secret"}
    values: dict[str, Any] = {}
    for f in fields:
        value = stored.get(f["key"], f["default"])
        values[f["key"]] = plugins_config.mask_setting(value) if f["key"] in secrets else value
    return {"id": addon_id, "fields": fields, "values": values}


@router.get("/api/plugins/{addon_id}/settings")
async def get_plugin_settings(addon_id: str):
    candidate, doc = _lookup(addon_id)
    return _settings_payload(addon_id, candidate, doc)


@router.put("/api/plugins/{addon_id}/settings")
async def put_plugin_settings(addon_id: str, data: AddonSettingsUpdate):
    candidate, doc = _lookup(addon_id)
    fields = {f["key"]: f for f in _fields(candidate, addon_id)}
    unknown = sorted(k for k in data.values if k not in fields)
    if unknown:
        raise HTTPException(status_code=422, detail={
            "code": "error.plugins.settingUnknown", "params": {"id": addon_id, "keys": unknown}})
    entry = plugins_config.ensure_entry(doc, addon_id)
    for key, value in data.values.items():
        if value is None:
            continue  # unchanged — the masking contract with the UI
        if fields[key]["type"] == "bool":
            entry["settings"][key] = bool(value)
        elif isinstance(value, str) and not value.strip():
            entry["settings"].pop(key, None)
        else:
            entry["settings"][key] = str(value).strip()
    await _save_and_reload(doc)
    doc = Config.plugins_document()
    return _settings_payload(addon_id, candidate, doc)
