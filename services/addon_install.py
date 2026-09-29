"""Installing a Bundle — the pure decisions (ADR-0021, #191).

Pure (ADR-0006): no network, no filesystem, no persistence. ``addon_installer``
downloads, hashes, extracts and renames on top of the answers given here;
``routers/marketplace.py`` orchestrates and persists.

What is decided here:

* **Size limits** — the download is cut off as soon as it exceeds the declared
  artifact size (the index says how big it is) or the hard cap, whichever is
  smaller; extraction is cut off at ``MAX_UNPACKED_BYTES``.
* **Archive members** — every Zip entry must be a plain relative path inside
  the Bundle: no absolute path, no drive letter, no ``..``, no NUL, no symlink.
  One bad member rejects the whole archive before anything is extracted.
* **Checksums** — the index's SHA-256 is compared case-insensitively.

Errors are codes (ADR-0018): ``InstallError.code`` is resolved by the SPA.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Optional, Sequence

#: A Bundle may be big (a numeric stack is ~250 MB), but not unbounded.
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
#: Unpacked, a Bundle may grow; a Zip bomb may not.
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024
MAX_MEMBERS = 50_000

MANIFEST_FILENAME = "plugin.json"

# Error codes (resolved by the SPA's catalog).
ERR_NOT_IN_INDEX = "error.marketplace.notInIndex"
ERR_VERSION_UNKNOWN = "error.marketplace.versionUnknown"
ERR_REQUIRES_SOURCE = "error.marketplace.requiresSource"
ERR_INCOMPATIBLE = "error.marketplace.incompatible"
ERR_NO_ARTIFACT = "error.marketplace.noArtifact"
ERR_DOWNLOAD = "error.marketplace.downloadFailed"
ERR_TOO_LARGE = "error.marketplace.tooLarge"
ERR_CHECKSUM = "error.marketplace.checksumMismatch"
ERR_NOT_A_ZIP = "error.marketplace.notABundle"
ERR_UNSAFE = "error.marketplace.unsafeArchive"
ERR_NO_MANIFEST = "error.marketplace.manifestMissing"
ERR_MANIFEST = "error.marketplace.manifestInvalid"
ERR_ID_MISMATCH = "error.marketplace.idMismatch"
ERR_VERSION_MISMATCH = "error.marketplace.versionMismatch"
ERR_ALREADY_LOADED = "error.marketplace.alreadyInstalled"
ERR_FOLDER = "error.marketplace.folderNotABundle"


class InstallError(Exception):
    """A refused install. ``code`` is an i18n key, ``params`` its variables."""

    def __init__(self, code: str, params: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.params = dict(params or {})

    def as_detail(self) -> dict:
        return {"code": self.code, "params": dict(self.params)}


def download_limit(declared_size: Optional[int]) -> int:
    """How many bytes a download may have before it is cut off.

    The declared size is exact for a Bundle built by ``localbib-addon build``,
    so one byte more is already a different file."""
    if isinstance(declared_size, int) and not isinstance(declared_size, bool) and declared_size > 0:
        return min(declared_size, MAX_BUNDLE_BYTES)
    return MAX_BUNDLE_BYTES


def checksum_matches(expected: str, actual: str) -> bool:
    exp = (expected or "").strip().lower()
    return bool(exp) and exp == (actual or "").strip().lower()


def _is_symlink(external_attr: int) -> bool:
    return stat.S_ISLNK((external_attr >> 16) & 0xFFFF)


def member_parts(name: str, external_attr: int = 0) -> Optional[tuple[str, ...]]:
    """The path parts of one Zip member, or ``None`` when it may not be
    extracted: absolute, drive-lettered, ``..``, NUL, empty or a symlink.

    Backslashes count as separators — a Windows-built Zip may use them, and a
    ``..\\`` must not slip past a POSIX-only check."""
    if not name or "\x00" in name or _is_symlink(external_attr):
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    parts = tuple(p for p in PurePosixPath(normalized).parts if p not in ("", "."))
    if not parts:
        return None
    for part in parts:
        if part == ".." or ":" in part:
            return None
    return parts


@dataclass(frozen=True)
class Member:
    """What the check needs of one ``zipfile.ZipInfo``."""

    name: str
    external_attr: int
    file_size: int
    is_dir: bool


def check_members(members: Sequence[Member]) -> Optional[InstallError]:
    """``None`` when every member is safe and the archive is a Bundle
    (``plugin.json`` at its top), else the reason. Nothing is extracted yet."""
    if len(members) > MAX_MEMBERS:
        return InstallError(ERR_TOO_LARGE)
    total = 0
    has_manifest = False
    for m in members:
        parts = member_parts(m.name, m.external_attr)
        if parts is None:
            return InstallError(ERR_UNSAFE, {"member": m.name[:200]})
        total += max(0, int(m.file_size))
        if total > MAX_UNPACKED_BYTES:
            return InstallError(ERR_TOO_LARGE)
        if parts == (MANIFEST_FILENAME,) and not m.is_dir:
            has_manifest = True
    if not has_manifest:
        return InstallError(ERR_NO_MANIFEST)
    return None


def check_identity(manifest_id: str, manifest_version: str,
                   expected_id: str = "", expected_version: str = "") -> Optional[InstallError]:
    """A Marketplace download must be the Add-on and version the index named."""
    if expected_id and manifest_id != expected_id:
        return InstallError(ERR_ID_MISMATCH, {"expected": expected_id, "found": manifest_id})
    if expected_version and manifest_version != expected_version:
        return InstallError(ERR_VERSION_MISMATCH, {"expected": expected_version, "found": manifest_version})
    return None


def safe_version_folder(version: str) -> bool:
    """The Manifest's version becomes a folder name; it must be one."""
    parts = member_parts(version)
    return parts is not None and len(parts) == 1 and not version.startswith(".")


def members_of(infos: Iterable) -> list[Member]:
    """``zipfile.ZipInfo`` objects -> :class:`Member` (plain data, no I/O)."""
    return [Member(name=i.filename, external_attr=i.external_attr, file_size=i.file_size, is_dir=i.is_dir())
            for i in infos]


__all__ = [
    "InstallError",
    "MAX_BUNDLE_BYTES",
    "MAX_UNPACKED_BYTES",
    "Member",
    "check_identity",
    "check_members",
    "checksum_matches",
    "download_limit",
    "member_parts",
    "members_of",
    "safe_version_folder",
]
