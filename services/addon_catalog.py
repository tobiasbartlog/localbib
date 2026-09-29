"""What Add-ons exist on this machine and whether the core may load them.

Pure (ADR-0006): reads Bundle folders and Manifests, never writes, never
imports an Add-on, never touches ``sys.path``. ``plugin_loader`` does all of
that on top of the answers given here.

Two places hold Add-ons:

* **Bundles** — ``<root>/<id>/<version>/`` with ``plugin.json`` at the top.
  The version the core loads is the one ``plugins.json`` names; without an
  entry, the highest version folder.
* **Dev-Suchpfade** — a source folder that *is* a Bundle (``plugin.json`` at
  its top). A dev folder wins over an installed Bundle of the same id: the
  author is working on it.

Verträglichkeit (``check_compat``) compares the Manifest with the core's facts:
Contract version (only ``plugin_api.API_VERSION``), the Python build tag
(``any`` always fits) and ``min_core``. ``core_version`` is ``None`` for a
source checkout — its VERSION file is not a release statement, so
``min_core`` is not checked there.
"""

from __future__ import annotations

import json
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

import plugins_config
from plugin_api import API_VERSION, MANIFEST_FILENAME, ManifestError, PluginManifest, load_manifest
from services.update_offer import parse_version

SOURCE_BUNDLE = "bundle"
SOURCE_DEV = "dev"

# Wire states of GET /api/plugins.
STATE_ACTIVE = "active"
STATE_INACTIVE = "inactive"
STATE_ERROR = "error"
STATE_INCOMPATIBLE = "incompatible"
STATE_CONSENT_PENDING = "consent_pending"


def python_tag() -> str:
    """This interpreter's build tag in the Manifest's form, e.g. ``cp313-win_amd64``.

    The one place the tag is derived — the version endpoint reports it and the
    compatibility check compares against it."""
    impl = {"cpython": "cp"}.get(sys.implementation.name, sys.implementation.name)
    major, minor = sys.version_info[:2]
    platform = sysconfig.get_platform().replace("-", "_").replace(".", "_").lower()
    return f"{impl}{major}{minor}-{platform}"


@dataclass(frozen=True)
class CoreFacts:
    """What an Add-on is checked against."""

    api_version: int
    python_tag: str
    core_version: Optional[str] = None   # None: source checkout, min_core not enforced
    frozen: bool = False

    def as_dict(self) -> dict:
        return {
            "api_version": self.api_version,
            "python_tag": self.python_tag,
            "core_version": self.core_version,
            "frozen": self.frozen,
        }


@dataclass
class Candidate:
    """One Add-on found on disk. ``manifest`` is ``None`` when it could not be
    read; then exactly one of ``incompatible``/``manifest_error`` says why."""

    id: str
    path: Path
    source: str
    manifest: Optional[PluginManifest] = None
    manifest_error: Optional[str] = None
    incompatible: Optional[str] = None       # error code when not loadable here
    versions: list[str] = field(default_factory=list)


def check_compat(manifest: PluginManifest, facts: CoreFacts) -> Optional[str]:
    """``None`` when the core may load ``manifest``, else the error code."""
    if manifest.api_version != facts.api_version:
        return "error.plugins.contractMismatch"
    if manifest.python != "any" and manifest.python != facts.python_tag:
        return "error.plugins.pythonMismatch"
    if facts.core_version and parse_version(facts.core_version) < parse_version(manifest.min_core):
        return "error.plugins.coreTooOld"
    return None


def _read(folder: Path, facts: CoreFacts, source: str, expected_id: str = "") -> Candidate:
    fallback_id = expected_id or folder.name
    candidate = Candidate(id=fallback_id, path=folder, source=source)
    # A Contract-1 Manifest fails the schema (api_version >= 2), but it is not
    # broken: it is incompatible. Look at the raw field before validating.
    try:
        raw = json.loads((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        candidate.manifest_error = f"{MANIFEST_FILENAME}: {exc}"
        return candidate
    if isinstance(raw, dict):
        if isinstance(raw.get("id"), str) and raw["id"] and not expected_id:
            candidate.id = raw["id"]
        api = raw.get("api_version")
        if isinstance(api, int) and not isinstance(api, bool) and api != facts.api_version:
            candidate.incompatible = "error.plugins.contractMismatch"
            return candidate
    try:
        manifest = load_manifest(folder)
    except ManifestError as exc:
        candidate.manifest_error = str(exc)
        return candidate
    if expected_id and manifest.id != expected_id:
        candidate.manifest_error = f"id: {manifest.id!r} does not match the Bundle folder {expected_id!r}"
        return candidate
    candidate.id = manifest.id
    candidate.manifest = manifest
    candidate.incompatible = check_compat(manifest, facts)
    return candidate


def _bundle_version(folder: Path, wanted: str) -> tuple[Optional[Path], list[str]]:
    versions = sorted(
        (d.name for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=parse_version,
    )
    if not versions:
        return None, []
    chosen = wanted if wanted in versions else versions[-1]
    return folder / chosen, versions


def discover(
    bundle_root: str | Path,
    dev_paths: Iterable[str],
    doc: Mapping,
    facts: CoreFacts,
) -> dict[str, Candidate]:
    """Every Add-on on disk, keyed by id. Unreadable folders are skipped
    silently only when they are not Add-ons at all (no ``plugin.json``)."""
    plugins = doc.get("plugins") or {}
    found: dict[str, Candidate] = {}
    root = Path(bundle_root) if bundle_root else None
    if root is not None and root.is_dir():
        for addon_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            wanted = (plugins.get(addon_dir.name) or {}).get("version") or ""
            chosen, versions = _bundle_version(addon_dir, wanted)
            if chosen is None or not (chosen / MANIFEST_FILENAME).is_file():
                continue
            candidate = _read(chosen, facts, SOURCE_BUNDLE, expected_id=addon_dir.name)
            candidate.versions = versions
            found[candidate.id] = candidate
    for raw_path in dev_paths:
        folder = Path(raw_path)
        if not (folder / MANIFEST_FILENAME).is_file():
            continue
        candidate = _read(folder, facts, SOURCE_DEV)
        found[candidate.id] = candidate
    return found


def provenance(candidate: Candidate, entry: Mapping) -> dict:
    """The Herkunft of ``candidate``'s code (``plugins_config.provenance``): a
    Dev-Suchpfad is its folder; a Bundle is where ``entry`` says it came from."""
    if candidate.source == SOURCE_DEV:
        return plugins_config.provenance(plugins_config.ORIGIN_DEV, path=str(candidate.path))
    return plugins_config.source_provenance(entry)


def _declared(manifest: PluginManifest) -> list[str]:
    return [p.value for p in manifest.permissions]


def missing_consent(candidate: Candidate, entry: Mapping) -> list[str]:
    """The declared Berechtigungen the user has not agreed to for *this*
    code — all of them when the consent was given for another Herkunft."""
    if candidate.manifest is None:
        return []
    return plugins_config.missing_for(entry.get("consent") or {}, provenance(candidate, entry),
                                      _declared(candidate.manifest))


def needs_confirmation(candidate: Candidate, entry: Mapping) -> bool:
    """Whether the Zustimmungsdialog must come first: a Berechtigung lacks
    consent, or the code is a file or folder the user has not confirmed yet."""
    if candidate.manifest is None:
        return False
    return plugins_config.confirmation_needed(entry.get("consent") or {}, provenance(candidate, entry),
                                              _declared(candidate.manifest))


def dev_shadow_disabled(candidate: Candidate, entry: Mapping) -> bool:
    """Whether *this* id is an installed Bundle (index or file) that a
    Dev-Suchpfad of the same id now shadows and switched off (#8da7248: a
    dev folder never inherits the installed Bundle's consent, so it needs a
    fresh confirmation and is recorded ``enabled=False`` until then). The
    Marketplace uses this to explain, rather than silently show, why a
    previously active Add-on went dark."""
    if candidate.source != SOURCE_DEV or entry.get("enabled"):
        return False
    source_origin = str((entry.get("source") or {}).get("origin") or "")
    return source_origin in (plugins_config.ORIGIN_INDEX, plugins_config.ORIGIN_FILE)


def should_load(candidate: Candidate, entry: Mapping) -> bool:
    """Enabled, readable, compatible and consented — the only loadable case."""
    return bool(
        entry.get("enabled")
        and candidate.manifest is not None
        and not candidate.incompatible
        and not needs_confirmation(candidate, entry)
    )


def state_of(candidate: Candidate, entry: Mapping, loaded: bool) -> tuple[str, Optional[dict]]:
    """``(state, error)`` of an Add-on for the listing."""
    if loaded:
        return STATE_ACTIVE, None
    if candidate.manifest_error:
        return STATE_ERROR, {"code": "error.plugins.manifestInvalid", "message": candidate.manifest_error}
    if candidate.incompatible:
        return STATE_INCOMPATIBLE, {"code": candidate.incompatible, "message": ""}
    if entry.get("error"):
        return STATE_ERROR, dict(entry["error"])
    if entry.get("enabled") and needs_confirmation(candidate, entry):
        return STATE_CONSENT_PENDING, None
    return STATE_INACTIVE, None


def secret_keys(manifest: Optional[PluginManifest], values: Mapping) -> set[str]:
    """Which settings are secrets: those the Manifest types ``secret``; without
    a Manifest (a legacy Add-on), keys named like a key (``*_key``)."""
    if manifest is not None:
        return {s.key for s in manifest.settings if s.type == "secret"}
    return {k for k in values if k.endswith("_key")}


__all__ = [
    "API_VERSION",
    "Candidate",
    "CoreFacts",
    "check_compat",
    "dev_shadow_disabled",
    "discover",
    "missing_consent",
    "needs_confirmation",
    "provenance",
    "python_tag",
    "secret_keys",
    "should_load",
    "state_of",
]
