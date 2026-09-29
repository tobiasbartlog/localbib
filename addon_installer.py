"""Installing a Bundle — download, hash, extract, rename (ADR-0021, #191).

Neutral I/O module (like ``marketplace_client`` / ``plugin_loader``): the one
place that writes Bundle files into ``Config.PLUGIN_DIR``. The decisions —
limits, which Zip members are safe, whether a checksum matches — are the pure
``services.addon_install``; ``routers/marketplace.py`` orchestrates the steps
and persists ``plugins.json``.

**Nothing half-written ever reaches the target.** Every install works in its
own folder under ``<PLUGIN_DIR>/.staging/`` — the same volume as the target,
so the final step is one atomic ``os.replace`` of a complete folder. The
download, the extraction and every check happen before that rename; the
caller discards the work folder in a ``finally``, which also runs when the
client disconnects mid-download (the stream is cancelled). ``addon_catalog``
skips dot-folders, so a leftover staging folder is never mistaken for an
Add-on.

Order is fixed: download (size cut off while streaming, hashed on the way) →
checksum → member check (before a single byte is extracted) → extraction
(again size-capped, each path re-checked against the target) → Manifest →
identity/compatibility → rename.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import tempfile
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Optional

import httpx

import ca_trust
import plugin_loader
from config import Config
from plugin_api import ManifestError, PluginManifest, load_manifest
from services import addon_catalog
from services.addon_install import (
    ERR_ALREADY_LOADED, ERR_DOWNLOAD, ERR_INCOMPATIBLE, ERR_MANIFEST, ERR_NOT_A_ZIP, ERR_TOO_LARGE, ERR_UNSAFE,
    MAX_UNPACKED_BYTES, InstallError, check_members, member_parts, members_of, safe_version_folder,
)

CHUNK = 64 * 1024
#: Progress is reported at most every this many bytes (plus the last chunk).
PROGRESS_STEP = 256 * 1024
USER_AGENT = "LocalBib-Marketplace"


def staging_root() -> Path:
    return Path(Config.PLUGIN_DIR) / ".staging"


def new_workdir() -> Path:
    """A fresh, private work folder for one install, next to the target."""
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="install-", dir=str(root)))


def discard(path: Optional[Path]) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Download (streamed, size-capped, hashed on the way)
# ---------------------------------------------------------------------------

def _client() -> httpx.AsyncClient:
    bundle = ca_trust.ca_bundle()
    return httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=bundle) if bundle else True,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=60.0),
        headers={"User-Agent": USER_AGENT},
    )


async def download(url: str, dest: Path, *, limit: int, result: dict) -> AsyncIterator[tuple[int, Optional[int]]]:
    """Stream ``url`` into ``dest``; yields ``(received, total)`` as it goes.

    Cut off the moment more than ``limit`` bytes arrive (or the server
    announces more) — never after the fact. On completion ``result`` holds
    ``sha256`` and ``size``. Raises :class:`InstallError` on any failure."""
    hasher = hashlib.sha256()
    received = 0
    reported = 0
    try:
        async with _client() as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise InstallError(ERR_DOWNLOAD, {"message": f"HTTP {resp.status_code}"})
                try:
                    total: Optional[int] = int(resp.headers.get("content-length") or 0) or None
                except ValueError:
                    total = None
                if total is not None and total > limit:
                    raise InstallError(ERR_TOO_LARGE)
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > limit:
                            raise InstallError(ERR_TOO_LARGE)
                        hasher.update(chunk)
                        fh.write(chunk)
                        if received - reported >= PROGRESS_STEP:
                            reported = received
                            yield received, total
    except httpx.HTTPError as exc:
        raise InstallError(ERR_DOWNLOAD, {"message": f"{type(exc).__name__}: {exc}"[:300]}) from exc
    if received != reported:
        yield received, total
    result["sha256"] = hasher.hexdigest()
    result["size"] = received


def save_stream(src: BinaryIO, dest: Path, *, limit: int) -> tuple[str, int]:
    """Copy an uploaded file into ``dest`` with the same cap and hash."""
    hasher = hashlib.sha256()
    received = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise InstallError(ERR_TOO_LARGE)
            hasher.update(chunk)
            fh.write(chunk)
    return hasher.hexdigest(), received


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(zip_path: Path, dest: Path) -> PluginManifest:
    """Unpack a verified download into ``dest`` (an empty folder) and return
    its Manifest. Every member is checked before the first is written."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InstallError(ERR_NOT_A_ZIP) from exc
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    written = 0
    try:
        with zf:
            infos = zf.infolist()
            problem = check_members(members_of(infos))
            if problem is not None:
                raise problem
            for info in infos:
                parts = member_parts(info.filename, info.external_attr)
                if parts is None:  # check_members already refused this; belt and braces
                    raise InstallError(ERR_UNSAFE, {"member": info.filename[:200]})
                target = root.joinpath(*parts)
                try:
                    target.resolve().relative_to(root)
                except ValueError as exc:
                    raise InstallError(ERR_UNSAFE, {"member": info.filename[:200]}) from exc
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UNPACKED_BYTES:
                            raise InstallError(ERR_TOO_LARGE)
                        out.write(chunk)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise InstallError(ERR_NOT_A_ZIP) from exc
    try:
        manifest = load_manifest(dest)
    except ManifestError as exc:
        raise InstallError(ERR_MANIFEST, {"message": str(exc)[:300]}) from exc
    if not safe_version_folder(manifest.version):
        raise InstallError(ERR_MANIFEST, {"message": f"version {manifest.version!r}"})
    reason = addon_catalog.check_compat(manifest, plugin_loader.core_facts())
    if reason:
        raise InstallError(ERR_INCOMPATIBLE, {"reason": reason})
    return manifest


# ---------------------------------------------------------------------------
# The one step that touches the target
# ---------------------------------------------------------------------------

def target_of(manifest: PluginManifest) -> Path:
    return Path(Config.PLUGIN_DIR) / manifest.id / manifest.version


def place(staged: Path, manifest: PluginManifest) -> Path:
    """Rename the complete ``staged`` folder to ``<id>/<version>/``.

    The version the core is running cannot be replaced in place (Windows
    keeps its files open); a copy that is not loaded is swapped atomically."""
    target = target_of(manifest)
    loaded = plugin_loader.loaded_addons().get(manifest.id)
    if loaded is not None and Path(loaded[0]).resolve() == target.resolve():
        raise InstallError(ERR_ALREADY_LOADED, {"id": manifest.id, "version": manifest.version})
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.replace(staged, target)
        return target
    aside = staging_root() / f"replaced-{uuid.uuid4().hex}"
    os.replace(target, aside)
    try:
        os.replace(staged, target)
    except OSError:
        os.replace(aside, target)
        raise
    discard(aside)
    return target


__all__ = [
    "discard",
    "download",
    "extract",
    "new_workdir",
    "place",
    "save_stream",
    "staging_root",
    "target_of",
]
