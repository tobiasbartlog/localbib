"""An installed Add-on's life after the install — the pure rules (ADR-0021, #193).

Pure (ADR-0006): every function works on a ``plugins.json`` document (a dict,
changed in place) and on plain facts about the disk handed in by the caller.
No filesystem, no import, no persistence — ``plugin_loader`` applies the
start-time housekeeping, the routers apply the switches and persist.

**Switching versions.** A running Add-on cannot swap its code in place: its
modules (and, for a numeric stack, its native libraries) stay loaded, and
Windows keeps its files open. So an update or a rollback of a *running* Add-on
is recorded as ``pending_update`` and becomes active on the next start; one
that is not running (switched off, incompatible, failed) switches at once.

**Consent.** An update that declares a Berechtigung the user has not agreed to
stays downloaded but inactive (``pending_update``) until they do; the old
version keeps running meanwhile. Agreeing adds the new Berechtigungen to the
granted ones — never replaces them, or the version still running would lose
consent it had. Consent belongs to a Herkunft (``plugins_config``): a pending
version of another Herkunft (a file claiming the id of an index install) finds
none of the old consent — it asks for everything it declares, and at least
once even when it declares nothing. Agreeing to it is recorded on the pending
update (``agreed``) and replaces the consent only when the switch happens, so
the running version keeps its own until then.

**Keeping versions.** After the start-time switch exactly one predecessor
stays on disk (``previous_version``, the rollback target); older folders are
removed. A folder the document does not know is left alone. Herkunft belongs
to a version folder, not to the id: the predecessor keeps its own
(``previous_source``), a file that replaces the kept folder replaces it
(``record_placed``), and a rollback runs with the predecessor's — unknown means
no rollback.

**Removing.** ``pending_removals`` lists Add-ons whose Bundle is deleted on the
next start (Windows does not release loaded files before). Only the Bundle
goes: the Add-on's own data lives elsewhere and is never touched; its
settings namespace is kept, so a reinstall finds its folders again. Consent is
dropped — a reinstall asks again.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

import plugins_config

ERR_NO_PREVIOUS = "error.plugins.noPreviousVersion"
ERR_NO_PENDING = "error.plugins.noPendingUpdate"


def _entry(doc: dict, addon_id: str) -> dict:
    return plugins_config.ensure_entry(doc, addon_id)


def granted(entry: Mapping) -> set[str]:
    return set(((entry.get("consent") or {}).get("permissions")) or [])


def granted_for(entry: Mapping, prov: Mapping) -> set[str]:
    """What the consent grants code of Herkunft ``prov`` — nothing when it was
    given for another one (``plugins_config.consent_binds``)."""
    return granted(entry) if plugins_config.consent_binds(entry.get("consent") or {}, prov) else set()


def pending_provenance(entry: Mapping) -> dict:
    pending = entry.get("pending_update") or {}
    return plugins_config.provenance(str(pending.get("origin") or ""), str(pending.get("sha256") or ""))


def pending_missing(entry: Mapping) -> list[str]:
    """The Berechtigungen the pending version declares and the user has not
    agreed to yet — the difference the update dialog shows; all of them when
    the pending version has another Herkunft than the consent."""
    pending = entry.get("pending_update") or {}
    if pending.get("agreed") is not None:
        return sorted(set(pending.get("permissions") or []) - set(pending["agreed"]))
    return plugins_config.missing_for(entry.get("consent") or {}, pending_provenance(entry),
                                      pending.get("permissions") or [])


def pending_needs_confirmation(entry: Mapping) -> bool:
    """Whether the pending version waits for the update dialog: a new
    Berechtigung, or a file the user has not confirmed (even one declaring none)."""
    pending = entry.get("pending_update") or {}
    if not pending:
        return False
    if pending.get("agreed") is not None:
        return bool(pending_missing(entry))
    return plugins_config.confirmation_needed(entry.get("consent") or {}, pending_provenance(entry),
                                              pending.get("permissions") or [])


def _folder_source(prov: Mapping) -> dict:
    return {"origin": prov["origin"], "sha256": prov["sha256"]}


def previous_provenance(entry: Mapping) -> Optional[dict]:
    """The Herkunft of the code in the kept predecessor's folder, or ``None``
    when it is unknown (a document from before it was recorded, or a folder
    being replaced right now). Unknown is never guessed: such a predecessor is
    no rollback target, since its code could be anything that claimed the id."""
    source = entry.get("previous_source") or {}
    origin = str(source.get("origin") or "")
    sha256 = str(source.get("sha256") or "")
    if origin not in (plugins_config.ORIGIN_INDEX, plugins_config.ORIGIN_FILE):
        return None
    if origin == plugins_config.ORIGIN_FILE and not sha256:
        return None
    return _folder_source(plugins_config.provenance(origin, sha256))


def record_placed(doc: dict, addon_id: str, version: str, source: Optional[Mapping]) -> bool:
    """The folder of ``version`` was (or is about to be) rewritten with code of
    Herkunft ``source`` (``None``: not known yet). Herkunft is per version
    folder: if it is the kept predecessor's, its recorded Herkunft follows the
    new code. Returns whether the document changed.

    Callers mark the folder unknown (``None``) *before* replacing it and record
    the real Herkunft after, so a crash in between fails closed."""
    entry = (doc.get("plugins") or {}).get(addon_id)
    if not entry or not version or version != entry.get("previous_version") or version == entry.get("version"):
        return False
    if source is None:
        entry["previous_source"] = {"origin": "", "sha256": ""}
    else:
        entry["previous_source"] = _folder_source(
            plugins_config.provenance(str(source.get("origin") or ""), str(source.get("sha256") or "")))
    return True


def switch_version(doc: dict, addon_id: str, version: str, permissions: Iterable[str], *, running: bool,
                   source: Optional[Mapping] = None) -> dict:
    """Record that ``addon_id`` should run ``version`` (an update or a rollback).

    ``running``: whether the current version is loaded right now. Then the
    switch waits for the next start (``pending_update``); otherwise it happens
    at once and the current version becomes the predecessor, keeping its
    Herkunft (``previous_source``). ``source`` is the new version's Herkunft
    ``{origin, sha256}``; ``None`` is a rollback: the kept predecessor's own
    recorded Herkunft — never the running version's, since another Herkunft
    may have replaced that folder — and ``ValueError(ERR_NO_PREVIOUS)`` when it
    is unknown. Returns the entry."""
    entry = _entry(doc, addon_id)
    current = entry.get("version") or ""
    if not version or version == current:
        entry["pending_update"] = None
        return entry
    if source is not None:
        new_source = dict(source)
    elif version == entry.get("previous_version"):
        known = previous_provenance(entry)
        if known is None:
            raise ValueError(ERR_NO_PREVIOUS)
        new_source = known
    else:
        new_source = dict(entry.get("source") or {})
    prov = plugins_config.provenance(str(new_source.get("origin") or ""), str(new_source.get("sha256") or ""))
    if running:
        entry["pending_update"] = {"version": version, "permissions": sorted(set(permissions)),
                                   "origin": prov["origin"], "sha256": prov["sha256"]}
        return entry
    if current:
        entry["previous_version"] = current
        entry["previous_source"] = _folder_source(plugins_config.source_provenance(entry))
    entry["version"] = version
    entry["source"] = {"origin": prov["origin"], "sha256": prov["sha256"]}
    entry["pending_update"] = None
    return entry


def rollback_target(entry: Mapping, versions_on_disk: Iterable[str]) -> str:
    """The version a rollback switches to; raises ``ValueError(code)`` if none
    — also when the Herkunft of its folder is unknown (fail closed)."""
    previous = entry.get("previous_version") or ""
    if not previous or previous not in set(versions_on_disk) or previous == entry.get("version"):
        raise ValueError(ERR_NO_PREVIOUS)
    if previous_provenance(entry) is None:
        raise ValueError(ERR_NO_PREVIOUS)
    return previous


def agree_to_update(doc: dict, addon_id: str, seen: Iterable[str]) -> list[str]:
    """Agree to the pending update's new Berechtigungen. Returns the ones the
    user did not see (a stale dialog) — then nothing is changed."""
    entry = _entry(doc, addon_id)
    pending = entry.get("pending_update")
    if not pending:
        raise ValueError(ERR_NO_PENDING)
    unseen = sorted(set(pending_missing(entry)) - set(seen))
    if unseen:
        return unseen
    prov = pending_provenance(entry)
    if not plugins_config.consent_binds(entry.get("consent") or {}, prov):
        # Another Herkunft: the running version keeps its consent; the new
        # code's is recorded on the pending update and replaces it when the
        # switch happens (``apply_pending``).
        pending["agreed"] = sorted(set(pending.get("permissions") or []))
        return []
    # Same Herkunft: add to what was granted, never replace — the version
    # still running would lose consent it had.
    permissions = granted(entry) | set(pending.get("permissions") or [])
    entry["consent"] = {**entry.get("consent", {}), "version": str(pending.get("version") or ""),
                        "permissions": sorted(permissions)}
    return []


def mark_removal(doc: dict, addon_id: str) -> None:
    """Switch the Add-on off and queue its Bundle for deletion on the next start."""
    entry = _entry(doc, addon_id)
    entry["enabled"] = False
    entry["pending_update"] = None
    removals = doc.setdefault("pending_removals", [])
    if addon_id not in removals:
        removals.append(addon_id)


def cancel_removal(doc: dict, addon_id: str) -> None:
    """A reinstall before the next start keeps the Bundle."""
    doc["pending_removals"] = [r for r in doc.get("pending_removals") or [] if r != addon_id]


def forget_after_removal(doc: dict, addon_id: str) -> None:
    """The Bundle is gone: keep only the settings namespace (user choices such
    as a data folder); everything tied to the Bundle is reset."""
    plugins = doc.setdefault("plugins", {})
    settings = dict((plugins.get(addon_id) or {}).get("settings") or {})
    if settings:
        fresh = plugins_config.empty_entry()
        fresh["settings"] = settings
        plugins[addon_id] = fresh
    else:
        plugins.pop(addon_id, None)
    cancel_removal(doc, addon_id)


def apply_pending(doc: dict) -> list[str]:
    """Start time: activate every pending version the user has agreed to.
    Returns the ids that switched; a pending update without consent stays."""
    switched: list[str] = []
    for addon_id, entry in (doc.get("plugins") or {}).items():
        pending = entry.get("pending_update")
        if not pending:
            continue
        target = str(pending.get("version") or "")
        if not target or target == entry.get("version"):
            entry["pending_update"] = None
            continue
        if pending_needs_confirmation(entry):
            continue
        if pending.get("agreed") is not None:
            # Consent given for the pending code of another Herkunft replaces the old one.
            entry["consent"] = {**plugins_config.empty_consent(), "version": target,
                                "permissions": sorted(pending["agreed"])}
            plugins_config.bind_consent(entry, pending_provenance(entry))
        switch_version(doc, addon_id, target, pending.get("permissions") or [], running=False,
                       source={"origin": pending.get("origin") or "", "sha256": pending.get("sha256") or ""})
        switched.append(addon_id)
    return switched


def keep_versions(entry: Optional[Mapping]) -> Optional[set[str]]:
    """The versions of one Add-on that must stay on disk, or ``None`` when the
    document does not say which one runs (then nothing is pruned)."""
    if not entry or not entry.get("version"):
        return None
    keep = {entry["version"]}
    if entry.get("previous_version"):
        keep.add(entry["previous_version"])
    pending = entry.get("pending_update") or {}
    if pending.get("version"):
        keep.add(str(pending["version"]))
    return keep


def prune_plan(doc: Mapping, on_disk: Mapping[str, Iterable[str]]) -> tuple[list[str], list[tuple[str, str]]]:
    """What the start deletes: ``(whole Add-ons, [(id, version)])``.

    ``on_disk`` maps every Bundle folder id to its version folders. Pending
    removals go as a whole; for every other Add-on the versions beyond the
    running one, its one predecessor and a pending update."""
    removals = [r for r in doc.get("pending_removals") or [] if r in on_disk]
    plugins = doc.get("plugins") or {}
    versions: list[tuple[str, str]] = []
    for addon_id in sorted(on_disk):
        if addon_id in removals:
            continue
        keep = keep_versions(plugins.get(addon_id))
        if keep is None:
            continue
        versions.extend((addon_id, v) for v in sorted(on_disk[addon_id]) if v not in keep)
    return removals, versions


__all__ = [
    "ERR_NO_PENDING",
    "ERR_NO_PREVIOUS",
    "agree_to_update",
    "apply_pending",
    "cancel_removal",
    "forget_after_removal",
    "granted",
    "granted_for",
    "keep_versions",
    "mark_removal",
    "pending_missing",
    "pending_needs_confirmation",
    "pending_provenance",
    "previous_provenance",
    "prune_plan",
    "record_placed",
    "rollback_target",
    "switch_version",
]
