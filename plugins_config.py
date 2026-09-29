"""The Add-on state document — ``plugins.json`` next to ``.env`` and ``llm.json``.

Same pattern as ``llm_config`` (ADR-0019): this module owns the document's
shape, its defaults and its coercion; the core is the only writer; nothing
else hand-rolls JSON for it. Per Add-on (keyed by its id):

* ``version``          the installed Bundle version the core loads,
* ``enabled``          the user's switch,
* ``source``           ``{origin, sha256}`` where the installed Bundle came
                       from: ``index`` or ``file`` (with the file's SHA-256);
                       empty for a Bundle installed before this was recorded,
                       which counts as ``index``,
* ``consent``          ``{version, permissions, origin, sha256, path}`` the user
                       agreed to — and *what* they agreed to it for (the
                       **Herkunft**, see below),
* ``settings``         the Add-on's own namespace (``SettingsApi.get/set``),
* ``previous_version`` the one version kept for a rollback,
* ``previous_source``  ``{origin, sha256}`` of the code in that kept folder —
                       Herkunft is per version folder, not per id (a file
                       may replace the kept folder). Empty means unknown, and
                       an unknown predecessor is no rollback target,
* ``pending_update``   ``{version, permissions, origin, sha256, agreed}`` of a
                       downloaded version that becomes active on the next
                       start — or waits for consent (``services.addon_lifecycle``),
* ``error``            ``{code, message}`` of the last failed load, or ``None``.

**Consent belongs to a Herkunft, not to an id.** Any Bundle can claim any id,
so a consent records where the agreed-to code came from: ``index`` (any version
the index offers — an index update asks only for new Berechtigungen), ``file``
(exactly the file with that SHA-256) or ``dev`` (exactly that folder). Code of
another Herkunft finds no consent (:func:`consent_binds`). A consent without a
Herkunft predates this rule (an older ``plugins.json``, or ``LEGACY_CONSENT``
from an old ``.env``): it is *unbound* and keeps counting for what is
installed, so nothing that ran before stops. The only way new code reaches an
id through the app is a file or folder install, and that binds such a consent
to the installed Bundle's Herkunft first (:func:`bind_unbound_consent`); any
new consent is recorded bound (``POST /api/plugins/{id}/consent``).

Top level: ``dev_paths`` (Dev-Suchpfade the user added), ``pending_removals``
(Bundles to delete on the next start) and ``boot_marker`` (the id of the
Add-on being loaded right now — a crash leaves it behind).

Legacy ``.env`` keys of the plugins that predate Bundles are migrated **read-
only** at load (``from_legacy_env``); the first save of the document removes
them from the ``.env``. Nothing is mirrored back into the process environment:
an Add-on reads its values through ``SettingsApi`` only.

Leaf module: stdlib only (plus the sibling leaf ``llm_config`` for the key
hint), so ``config.py`` can call it while loading.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from typing import Any, Mapping, Optional

from llm_config import key_hint

DOCUMENT_VERSION = 1

# Legacy .env key -> (add-on id, field). Field "enabled" is the switch, any
# other field a key in the Add-on's settings namespace.
LEGACY_ENV_KEYS: dict[str, tuple[str, str]] = {
}

# Add-on id -> the Berechtigungen its Bundle Manifest declares, for the plugins
# that ran before Bundles existed. A legacy switch set to ``true`` already let
# the plugin do all of that, so migrating it counts as consent to exactly this
# list — the Add-on comes up active as soon as its Bundle is found. A Manifest
# that later declares more still asks (``addon_catalog.missing_consent``).
LEGACY_CONSENT: dict[str, tuple[str, ...]] = {
}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def empty_document() -> dict:
    return {
        "version": DOCUMENT_VERSION,
        "plugins": {},
        "dev_paths": [],
        "pending_removals": [],
        "boot_marker": "",
    }


ORIGIN_INDEX = "index"
ORIGIN_FILE = "file"
ORIGIN_DEV = "dev"
ORIGINS = (ORIGIN_INDEX, ORIGIN_FILE, ORIGIN_DEV)


def empty_consent() -> dict:
    return {"version": "", "permissions": [], "origin": "", "sha256": "", "path": ""}


def empty_entry() -> dict:
    return {
        "version": "",
        "enabled": False,
        "source": {"origin": "", "sha256": ""},
        "consent": empty_consent(),
        "settings": {},
        "previous_version": "",
        "previous_source": {"origin": "", "sha256": ""},
        "pending_update": None,
        "error": None,
    }


def ensure_entry(doc: dict, addon_id: str) -> dict:
    """The entry of ``addon_id`` in ``doc``, created with defaults if missing."""
    plugins = doc.setdefault("plugins", {})
    if addon_id not in plugins:
        plugins[addon_id] = empty_entry()
    return plugins[addon_id]


# ---------------------------------------------------------------------------
# Herkunft (provenance) of consent
# ---------------------------------------------------------------------------

def provenance(origin: str, sha256: str = "", path: str = "") -> dict:
    """``{origin, sha256, path}`` — only the part the origin is judged by."""
    origin = origin if origin in ORIGINS else ORIGIN_INDEX
    return {
        "origin": origin,
        "sha256": (sha256 or "").strip().lower() if origin == ORIGIN_FILE else "",
        "path": _norm_path(path) if origin == ORIGIN_DEV else "",
    }


def source_provenance(entry: Mapping) -> dict:
    """The Herkunft of the installed Bundle of ``entry`` (``index`` when unknown)."""
    source = entry.get("source") or {}
    return provenance(str(source.get("origin") or ""), str(source.get("sha256") or ""))


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def is_unbound(consent: Mapping) -> bool:
    """A consent from before Herkunft was recorded (see the module docstring)."""
    return not (consent or {}).get("origin")


def consent_binds(consent: Mapping, prov: Mapping) -> bool:
    """Whether ``consent`` was given for code of Herkunft ``prov``.

    Same origin, and for ``file`` the same SHA-256, for ``dev`` the same folder.
    An unbound consent binds to anything (it is the legacy state; the core
    binds it on the next look)."""
    consent = consent or {}
    if is_unbound(consent):
        return True
    mine = provenance(str(consent.get("origin") or ""), str(consent.get("sha256") or ""),
                      str(consent.get("path") or ""))
    theirs = provenance(str(prov.get("origin") or ""), str(prov.get("sha256") or ""), str(prov.get("path") or ""))
    if mine["origin"] != theirs["origin"]:
        return False
    if mine["origin"] == ORIGIN_FILE:
        return bool(mine["sha256"]) and mine["sha256"] == theirs["sha256"]
    if mine["origin"] == ORIGIN_DEV:
        return bool(mine["path"]) and mine["path"] == theirs["path"]
    return True


def bind_consent(entry: dict, prov: Mapping) -> None:
    """Record on ``entry``'s consent the Herkunft it counts for."""
    consent = entry.setdefault("consent", empty_consent())
    bound = provenance(str(prov.get("origin") or ""), str(prov.get("sha256") or ""), str(prov.get("path") or ""))
    consent.update(bound)


def missing_for(consent: Mapping, prov: Mapping, declared) -> list[str]:
    """The ``declared`` Berechtigungen ``consent`` does not cover for code of
    Herkunft ``prov`` — all of them when it was given for another Herkunft."""
    granted = set((consent or {}).get("permissions") or []) if consent_binds(consent, prov) else set()
    return sorted(set(declared) - granted)


def confirmation_needed(consent: Mapping, prov: Mapping, declared) -> bool:
    """Whether code of Herkunft ``prov`` declaring ``declared`` needs the
    Zustimmungsdialog before it may run: a Berechtigung lacks consent, or it is
    a file or a folder the user has not confirmed — even one declaring none,
    since the dialog is where they see that an Add-on runs with all the app's
    rights (and, for a file, its checksum)."""
    if missing_for(consent, prov, declared):
        return True
    return prov.get("origin") in (ORIGIN_FILE, ORIGIN_DEV) and not consent_binds(consent, prov)


def bind_unbound_consent(entry: dict) -> bool:
    """Bind a legacy consent to the installed Bundle's Herkunft (``index``
    when unknown). Called before a file or folder install could claim the id,
    so a legacy consent never passes to code the user has not seen."""
    if not is_unbound(entry.get("consent") or {}):
        return False
    bind_consent(entry, source_provenance(entry))
    return True


# ---------------------------------------------------------------------------
# Coercion — the document is written by the core only, so a bad value is a
# damaged file, not a user error: coerce to the default instead of raising.
# ---------------------------------------------------------------------------

def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _setting_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _error(value: Any) -> Optional[dict]:
    if not isinstance(value, dict) or not _str(value.get("code")):
        return None
    return {"code": _str(value.get("code")), "message": str(value.get("message") or "")[:1000]}


def _source(value: Any) -> dict:
    """A Bundle folder's Herkunft ``{origin, sha256}``; empty when unknown or damaged."""
    source = value if isinstance(value, dict) else {}
    origin = _str(source.get("origin"))
    if origin not in (ORIGIN_INDEX, ORIGIN_FILE):
        return {"origin": "", "sha256": ""}
    return {"origin": origin, "sha256": _str(source.get("sha256")).lower() if origin == ORIGIN_FILE else ""}


def _entry(raw: Any) -> dict:
    entry = empty_entry()
    if not isinstance(raw, dict):
        return entry
    entry["version"] = _str(raw.get("version"))
    entry["enabled"] = raw.get("enabled") is True
    entry["source"] = _source(raw.get("source"))
    consent = raw.get("consent") if isinstance(raw.get("consent"), dict) else {}
    entry["consent"] = {
        "version": _str(consent.get("version")),
        "permissions": sorted(_str_list(consent.get("permissions"))),
        "origin": "", "sha256": "", "path": "",
    }
    if _str(consent.get("origin")) in ORIGINS:
        # An unknown origin stays unbound — a damaged file, not a new grant.
        entry["consent"].update(provenance(_str(consent.get("origin")), _str(consent.get("sha256")),
                                           _str(consent.get("path"))))
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    entry["settings"] = {
        str(k): _setting_value(v) for k, v in settings.items() if isinstance(k, str) and k
    }
    entry["previous_version"] = _str(raw.get("previous_version"))
    entry["previous_source"] = _source(raw.get("previous_source"))
    pending = raw.get("pending_update")
    if isinstance(pending, dict) and _str(pending.get("version")):
        pending_origin = _str(pending.get("origin"))
        pending_origin = pending_origin if pending_origin in (ORIGIN_INDEX, ORIGIN_FILE) else ORIGIN_INDEX
        entry["pending_update"] = {
            "version": _str(pending.get("version")),
            "permissions": sorted(_str_list(pending.get("permissions"))),
            "origin": pending_origin,
            "sha256": _str(pending.get("sha256")).lower() if pending_origin == ORIGIN_FILE else "",
            # Consent to a pending version of another Herkunft, applied at the switch.
            "agreed": sorted(_str_list(pending["agreed"])) if isinstance(pending.get("agreed"), list) else None,
        }
    entry["error"] = _error(raw.get("error"))
    return entry


def normalize(doc: Any) -> dict:
    """The canonical form of ``doc``; unknown keys dropped, bad values defaulted."""
    out = empty_document()
    if not isinstance(doc, dict):
        return out
    plugins = doc.get("plugins") if isinstance(doc.get("plugins"), dict) else {}
    out["plugins"] = {
        str(pid): _entry(raw) for pid, raw in plugins.items() if isinstance(pid, str) and pid
    }
    out["dev_paths"] = _str_list(doc.get("dev_paths"))
    out["pending_removals"] = _str_list(doc.get("pending_removals"))
    out["boot_marker"] = _str(doc.get("boot_marker"))
    return out


# ---------------------------------------------------------------------------
# Legacy .env keys
# ---------------------------------------------------------------------------

def _truthy(value: str) -> bool:
    return (value or "").strip().lower() == "true"


def apply_legacy_env(doc: dict, values: Mapping[str, Optional[str]]) -> dict:
    """A copy of ``doc`` with the legacy ``values`` applied (``None`` or ``""``
    for a setting clears it). Keys outside ``LEGACY_ENV_KEYS`` are ignored."""
    doc = normalize(copy.deepcopy(doc))
    for key, value in values.items():
        target = LEGACY_ENV_KEYS.get(key)
        if target is None:
            continue
        addon_id, field = target
        entry = ensure_entry(doc, addon_id)
        if field == "enabled":
            entry["enabled"] = _truthy(value or "")
            granted = LEGACY_CONSENT.get(addon_id)
            if entry["enabled"] and granted and not entry["consent"]["permissions"]:
                # Unbound: it counts for the Bundle the core finds for this id
                # and is bound to that one on the next look.
                entry["consent"] = {**empty_consent(), "permissions": sorted(granted)}
        elif value is None or not str(value).strip():
            entry["settings"].pop(field, None)
        else:
            entry["settings"][field] = str(value).strip()
    return doc


def from_legacy_env(env: Mapping[str, str]) -> Optional[dict]:
    """The document an old ``.env`` describes, or ``None`` when it names none of
    the legacy keys (a fresh install). Read-only: nothing is written."""
    present = {k: env[k] for k in LEGACY_ENV_KEYS if k in env and str(env[k]).strip()}
    if not present:
        return None
    return apply_legacy_env(empty_document(), present)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def mask_setting(value: Any) -> dict:
    """Wire form of a secret: never the value, only ``has_key`` + ``key_hint``."""
    text = value if isinstance(value, str) else ""
    return {"has_key": bool(text), "key_hint": key_hint(text)}


# ---------------------------------------------------------------------------
# File I/O (save is called by the core's persistence helper only)
# ---------------------------------------------------------------------------

def load(path: str) -> Optional[dict]:
    """The stored document, or ``None`` when there is none or it is unreadable
    (a corrupt file must not take the app down)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
        return None
    return data


#: Bounded retry of the final rename. On Windows a virus scanner or EDR agent
#: may hold the freshly written file for a moment, and ``os.replace`` then
#: fails with ``PermissionError`` (WinError 5) although nothing is wrong.
#: Five attempts, 50 ms doubling: at most ~0.75 s before the error surfaces.
REPLACE_ATTEMPTS = 5
REPLACE_FIRST_DELAY = 0.05


def _replace_with_retry(src: str, dst: str) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_FIRST_DELAY * (2 ** attempt))


def save(path: str, doc: dict) -> None:
    """Atomic write (temp file + replace): the file holds Add-on secrets.
    The replace is retried briefly on ``PermissionError`` (see above)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plugins-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(normalize(doc), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
