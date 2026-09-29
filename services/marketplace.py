"""What the Marketplace shows for one Add-on — reine Entscheidung (ADR-0021, #189).

Pure (ADR-0006): given a Marketplace-Index entry, the core's facts
(:class:`services.addon_catalog.CoreFacts`) and what is installed, this module
decides which version the core may offer, whether an installed version is
still listed, and whether an update is available. No network, no filesystem,
no persistence — ``marketplace_client`` fetches and caches the index and its
images, ``routers/marketplace.py`` assembles the response.

Mirrors ``addon_catalog.check_compat`` but against an *index* version entry
(a plain dict from JSON) instead of an installed ``PluginManifest`` — the
Marketplace must judge versions it has never downloaded.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from plugin_api import PERMISSIONS, Permission
from services.addon_catalog import CoreFacts, SOURCE_DEV
from services.addon_install import (
    ERR_INCOMPATIBLE, ERR_NO_ARTIFACT, ERR_NOT_IN_INDEX, ERR_REQUIRES_SOURCE, ERR_VERSION_UNKNOWN, InstallError,
)
from services.update_offer import parse_version

TRUST_LEVELS = ("official", "third-party")

# Card / filter states (wire contract with the SPA).
STATE_NOT_INSTALLED = "not_installed"
STATE_INSTALLED = "installed"
STATE_UPDATE_AVAILABLE = "update_available"
STATE_DEV = "dev"
STATE_INCOMPATIBLE = "incompatible"
STATE_INSTALLED_INCOMPATIBLE = "installed_incompatible"


def _artifact_for(version: Mapping[str, Any], python_tag: str) -> Optional[dict]:
    """The artifact of ``version`` this interpreter can run, or ``None``."""
    for art in version.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        py = str(art.get("python") or "any")
        if py == "any" or py == python_tag:
            return art
    return None


#: ``incompatible_detail``'s ``code`` -> the generic translation key
#: ``version_incompatible`` returns (used where only the sentence matters:
#: ``install_target``'s error detail, the per-version summary).
_DETAIL_ERROR_CODES = {
    "api_version": "error.plugins.contractMismatch",
    "min_core": "error.plugins.coreTooOld",
    "python": "error.plugins.pythonMismatch",
}


def incompatible_detail(version: Mapping[str, Any], facts: CoreFacts) -> Optional[dict]:
    """Why the core may not offer ``version`` — the machine-readable form of
    ``version_incompatible``, with the numbers a sentence needs instead of
    just a code: ``{"code": "api_version", "needed": <int>, "have": <int>}``,
    ``{"code": "min_core", "needed": <semver>, "have": <semver>}`` or
    ``{"code": "python", "needed": [<tag>, ...], "have": <tag>}``. ``None``
    when the core may offer ``version``.

    Checked in the same order as ``version_incompatible``: Contract version,
    ``min_core`` (only against a release build — a source checkout has no
    ``core_version`` to compare, same rule as ``addon_catalog.check_compat``),
    then whether an artifact matches this interpreter's Python tag — skipped
    for a ``requires_source`` version, which by definition has no artifact to
    match. The SPA turns ``code`` into one sentence
    (``marketplace.incompatibleReason.<code>``); there is deliberately no
    ``requires_source`` code here — that path never makes a version
    incompatible (see the skip above), it makes it manual-install-only, a
    fact the card already carries as ``requires_source``.
    """
    api_version = version.get("api_version")
    if isinstance(api_version, int) and not isinstance(api_version, bool) and api_version != facts.api_version:
        return {"code": "api_version", "needed": api_version, "have": facts.api_version}
    min_core = str(version.get("min_core") or "0.0.0")
    if facts.core_version and parse_version(facts.core_version) < parse_version(min_core):
        return {"code": "min_core", "needed": min_core, "have": facts.core_version}
    if not version.get("requires_source") and _artifact_for(version, facts.python_tag) is None:
        needed = sorted({
            str(art.get("python")) for art in version.get("artifacts") or []
            if isinstance(art, dict) and art.get("python") and art.get("python") != "any"
        })
        return {"code": "python", "needed": needed, "have": facts.python_tag}
    return None


def version_incompatible(version: Mapping[str, Any], facts: CoreFacts) -> Optional[str]:
    """``None`` when the core may offer ``version``, else the error code —
    the generic-sentence form of :func:`incompatible_detail`."""
    detail = incompatible_detail(version, facts)
    return _DETAIL_ERROR_CODES[detail["code"]] if detail else None


def sorted_versions(entry: Mapping[str, Any]) -> list[dict]:
    """``entry``'s versions, newest first."""
    versions = [v for v in entry.get("versions") or [] if isinstance(v, dict) and v.get("version")]
    return sorted(versions, key=lambda v: parse_version(str(v["version"])), reverse=True)


def best_version(entry: Mapping[str, Any], facts: CoreFacts) -> Optional[dict]:
    """The newest version of ``entry`` the core may offer, or ``None`` if none fits."""
    for version in sorted_versions(entry):
        if version_incompatible(version, facts) is None:
            return version
    return None


def installed_in_index(entry: Mapping[str, Any], installed_version: str) -> bool:
    """Whether ``installed_version`` still appears among ``entry``'s versions."""
    return any(str(v.get("version")) == installed_version for v in entry.get("versions") or [])


def permission_rows(keys: Iterable[Any]) -> list[dict]:
    """Berechtigungen as ``{key, enforced}`` rows (durchgesetzt/erklaert), in
    the order given; unknown keys are dropped."""
    rows = []
    for raw in keys or []:
        try:
            perm = Permission(raw)
        except ValueError:
            continue
        rows.append({"key": perm.value, "enforced": PERMISSIONS[perm].enforced})
    return rows


def _permission_rows(version: Mapping[str, Any]) -> list[dict]:
    """The version's declared Berechtigungen with durchgesetzt/erklaert (#189)."""
    return permission_rows(version.get("permissions") or [])


def _size_of(version: Optional[Mapping[str, Any]], facts: CoreFacts) -> Optional[int]:
    if version is None:
        return None
    artifact = _artifact_for(version, facts.python_tag)
    if artifact is None:
        artifacts = version.get("artifacts") or []
        artifact = artifacts[0] if artifacts and isinstance(artifacts[0], dict) else None
    size = artifact.get("size") if artifact else None
    return size if isinstance(size, int) and not isinstance(size, bool) else None


def _version_summary(version: Mapping[str, Any], facts: CoreFacts) -> dict:
    return {
        "version": str(version.get("version") or ""),
        "released": str(version.get("released") or ""),
        "changelog": str(version.get("changelog") or ""),
        "requires_source": bool(version.get("requires_source")),
        "permissions": _permission_rows(version),
        "size": _size_of(version, facts),
        "compatible": version_incompatible(version, facts) is None,
        "incompatible_reason": version_incompatible(version, facts),
    }


def build_card(entry: Mapping[str, Any], facts: CoreFacts, installed: Optional[Mapping[str, Any]]) -> dict:
    """One Marketplace card for an index entry, merged with its installed state.

    ``installed`` is ``None`` (not installed) or ``{"version", "source",
    "state"}`` — the shape ``routers/marketplace.py`` reads out of
    ``plugin_loader.describe()``.
    """
    addon_id = str(entry.get("id") or "")
    versions = sorted_versions(entry)
    latest = versions[0] if versions else None
    offered = best_version(entry, facts)
    installed_version = str(installed.get("version") or "") if installed else ""
    installed_source = str(installed.get("source") or "") if installed else ""
    is_dev = installed_source == SOURCE_DEV
    pending = (installed.get("pending_update") or {}) if installed else {}
    # A downloaded update waiting for the next start (or for consent) is not
    # offered again; the card says it is pending instead.
    newest_local = max((v for v in (installed_version, str(pending.get("version") or "")) if v),
                       key=parse_version, default="")
    update_available = bool(
        offered and newest_local and not is_dev
        and parse_version(str(offered["version"])) > parse_version(newest_local)
    )
    if is_dev:
        state = STATE_DEV
    elif not installed:
        state = STATE_INCOMPATIBLE if offered is None and versions else STATE_NOT_INSTALLED
    elif update_available:
        state = STATE_UPDATE_AVAILABLE
    elif installed.get("state") == "incompatible":
        state = STATE_INSTALLED_INCOMPATIBLE
    else:
        state = STATE_INSTALLED
    trust = entry.get("trust") if entry.get("trust") in TRUST_LEVELS else "third-party"
    # Why nothing was offered — the newest version's reason, machine-readable
    # (#199): only set when there is a newest version to blame and it did not
    # make the cut, so it never contradicts an ``offered`` version.
    incompatible_reason = incompatible_detail(latest, facts) if (offered is None and latest is not None) else None
    return {
        "id": addon_id,
        "name": str(entry.get("name") or addon_id),
        "tagline": str(entry.get("tagline") or ""),
        "description": str(entry.get("description") or ""),
        "author": str(entry.get("author") or ""),
        "license": str(entry.get("license") or ""),
        "homepage": str(entry.get("homepage") or ""),
        "trust": trust,
        "languages": [str(x) for x in (entry.get("languages") or [])],
        "tags": [str(x) for x in (entry.get("tags") or [])],
        "icon": str(entry.get("icon") or ""),
        "screenshots": [str(x) for x in (entry.get("screenshots") or [])],
        "requires_source": bool(latest and latest.get("requires_source")),
        "size": _size_of(offered or latest, facts),
        "in_index": True,
        "source": installed_source,
        "installed": bool(installed),
        "installed_version": installed_version,
        "installed_not_in_index": bool(installed_version and versions and not installed_in_index(entry, installed_version)),
        "offered_version": str(offered["version"]) if offered else "",
        "update_available": update_available,
        "state": state,
        "incompatible_reason": incompatible_reason,
        "addon_state": str(installed.get("state") or "") if installed else "",
        "installed_permissions": permission_rows(installed.get("permissions") or []) if installed else [],
        "permissions": _permission_rows(offered or latest or {}),
        "versions": [_version_summary(v, facts) for v in versions],
        **lifecycle_fields(installed),
    }


def lifecycle_fields(installed: Optional[Mapping[str, Any]]) -> dict:
    """The installed Add-on's switch and lifecycle facts on a card (#193):
    ``enabled``, ``missing_consent`` (switching on asks first), ``error``
    ({code, message} or None), ``previous_version`` (rollback target),
    ``rollback_available`` (whether the rollback endpoint would actually
    succeed — the predecessor's Herkunft must be known, never guessed from a
    non-empty ``previous_version`` alone), ``pending_update`` ({version,
    missing} or None), ``pending_removal`` and ``dev_shadow_disabled`` (an
    installed Bundle a same-id Dev-Suchpfad shadowed and switched off)."""
    installed = installed or {}
    pending = installed.get("pending_update")
    error = installed.get("error")
    return {
        "enabled": bool(installed.get("enabled")),
        "missing_consent": [str(m) for m in installed.get("missing_consent") or []],
        # Herkunft and whether switching on needs the dialog first (also with
        # no missing Berechtigung, for an unconfirmed file or folder).
        "needs_confirmation": bool(installed.get("needs_confirmation")),
        # An installed Bundle a Dev-Suchpfad of the same id shadows and
        # switched off — the card explains this instead of a bare "inactive".
        "dev_shadow_disabled": bool(installed.get("dev_shadow_disabled")),
        "origin": str(installed.get("origin") or ""),
        "sha256": str(installed.get("sha256") or ""),
        "error": dict(error) if isinstance(error, Mapping) else None,
        "previous_version": str(installed.get("previous_version") or ""),
        "rollback_available": bool(installed.get("rollback_available")),
        "pending_update": ({"version": str(pending.get("version") or ""),
                            "missing": [str(m) for m in pending.get("missing") or []],
                            "confirm": bool(pending.get("confirm")) or bool(pending.get("missing")),
                            "origin": str(pending.get("origin") or "index"),
                            "sha256": str(pending.get("sha256") or "")}
                           if isinstance(pending, Mapping) and pending.get("version") else None),
        "pending_removal": bool(installed.get("pending_removal")),
        # A Dev-Suchpfad from LOCALBIB_PLUGIN_DEV_PATHS ("env") cannot be
        # removed in the app, only switched off; "document" or None otherwise.
        "dev_origin": installed.get("dev_origin") or None,
    }


def _local_only_card(addon_id: str, installed: Mapping[str, Any]) -> dict:
    """An installed Add-on the index does not list — a Dev-Suchpfad (shown
    under "Entwicklung", user story #36) or a Bundle installed from a file."""
    source = str(installed.get("source") or SOURCE_DEV)
    return {
        "id": addon_id,
        "name": str(installed.get("name") or addon_id),
        "tagline": str(installed.get("tagline") or ""),
        "description": "",
        "author": "",
        "license": "",
        "homepage": "",
        "trust": "",
        "languages": [],
        "tags": [],
        "icon": "",
        "screenshots": [],
        "requires_source": False,
        "size": None,
        "in_index": False,
        "source": source,
        "installed": True,
        "installed_version": str(installed.get("version") or ""),
        "installed_not_in_index": False,
        "offered_version": "",
        "update_available": False,
        "state": STATE_DEV if source == SOURCE_DEV else STATE_INSTALLED,
        "incompatible_reason": None,
        "addon_state": str(installed.get("state") or ""),
        "installed_permissions": permission_rows(installed.get("permissions") or []),
        "permissions": permission_rows(installed.get("permissions") or []),
        "versions": [],
        **lifecycle_fields(installed),
    }


def merge_view(
    index_addons: list[Mapping[str, Any]],
    installed_by_id: Mapping[str, Mapping[str, Any]],
    facts: CoreFacts,
) -> list[dict]:
    """The Marketplace's merged view (issue #189): every index entry as a card,
    plus installed Dev-Suchpfad Add-ons the index does not (yet) list."""
    cards: list[dict] = []
    seen: set[str] = set()
    for entry in index_addons:
        addon_id = str(entry.get("id") or "")
        # An announced entry (no version yet) has nothing to offer; an
        # installed copy of it still shows, as a local card below.
        if not addon_id or addon_id in seen or not sorted_versions(entry):
            continue
        seen.add(addon_id)
        cards.append(build_card(entry, facts, installed_by_id.get(addon_id)))
    for addon_id, installed in installed_by_id.items():
        if addon_id in seen:
            continue
        cards.append(_local_only_card(addon_id, installed))
    cards.sort(key=lambda c: c["name"].lower())
    return cards


def find_entry(index_addons: list[Mapping[str, Any]], addon_id: str) -> Optional[Mapping[str, Any]]:
    """The index entry of ``addon_id``, or ``None``."""
    for entry in index_addons or []:
        if isinstance(entry, Mapping) and str(entry.get("id") or "") == addon_id:
            return entry
    return None


def install_target(entry: Optional[Mapping[str, Any]], facts: CoreFacts, version: str = "") -> tuple[dict, dict]:
    """``(version entry, artifact)`` the core downloads for an install of
    ``entry`` — the requested ``version`` or else the best compatible one.
    Raises :class:`InstallError` when nothing installable fits."""
    if entry is None:
        raise InstallError(ERR_NOT_IN_INDEX)
    if version:
        chosen = next((v for v in sorted_versions(entry) if str(v["version"]) == version), None)
        if chosen is None:
            raise InstallError(ERR_VERSION_UNKNOWN, {"version": version})
    else:
        chosen = best_version(entry, facts)
        if chosen is None:
            raise InstallError(ERR_INCOMPATIBLE)
    if chosen.get("requires_source"):
        raise InstallError(ERR_REQUIRES_SOURCE)
    reason = version_incompatible(chosen, facts)
    if reason:
        raise InstallError(ERR_INCOMPATIBLE, {"reason": reason})
    artifact = _artifact_for(chosen, facts.python_tag)
    if artifact is None or not str(artifact.get("url") or "").lower().startswith("https://") \
            or not str(artifact.get("sha256") or "").strip():
        raise InstallError(ERR_NO_ARTIFACT)
    return dict(chosen), dict(artifact)


def trust_of(entry: Optional[Mapping[str, Any]]) -> str:
    """The Vertrauensstufe the consent dialog shows. Anything not listed as
    ``official`` in the index — a file, a Dev-Suchpfad — counts as third-party."""
    if entry is not None and entry.get("trust") in TRUST_LEVELS:
        return str(entry["trust"])
    return "third-party"


def consent_request(addon: Mapping[str, Any], trust: str, origin: str, sha256: str = "") -> dict:
    """What the Zustimmungsdialog renders for an installed Add-on.

    ``addon`` is one row of ``plugin_loader.describe_one``; ``origin`` is
    ``index``, ``file`` or ``dev``. ``missing`` lists the Berechtigungen not yet
    agreed to; ``confirm`` says whether the dialog is needed at all — also
    with ``missing`` empty, for a file or folder the user has not confirmed
    (the dialog is where they see the rights warning and the checksum)."""
    missing = list(addon.get("missing_consent") or [])
    return {
        "id": str(addon.get("id") or ""),
        "name": str(addon.get("name") or ""),
        "version": str(addon.get("version") or ""),
        "trust": trust if trust in TRUST_LEVELS else "third-party",
        "origin": origin,
        "sha256": sha256,
        "permissions": permission_rows(addon.get("permissions") or []),
        "missing": missing,
        "confirm": bool(addon.get("needs_confirmation")) or bool(missing),
    }


__all__ = [
    "STATE_DEV",
    "STATE_INCOMPATIBLE",
    "STATE_INSTALLED",
    "STATE_INSTALLED_INCOMPATIBLE",
    "STATE_NOT_INSTALLED",
    "STATE_UPDATE_AVAILABLE",
    "TRUST_LEVELS",
    "best_version",
    "build_card",
    "consent_request",
    "find_entry",
    "incompatible_detail",
    "install_target",
    "installed_in_index",
    "lifecycle_fields",
    "permission_rows",
    "trust_of",
    "merge_view",
    "sorted_versions",
    "version_incompatible",
]
