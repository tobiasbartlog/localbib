"""The Marketplace-Index: fetch, disk cache, cached images (ADR-0021, #189).

Neutral I/O module (like ``ca_trust.py`` / ``plugin_loader.py``): the core's
one HTTPS client for the Marketplace-Index and its images. The pure
compatibility decision lives in ``services/marketplace.py``; this module owns
the network call, the disk cache (so an offline Marketplace still shows the
last-fetched list with its date, #189 AC2) and image caching (so a browser
``<img>`` tag never points at GitHub directly, #189 AC5).

The index is a JSON file at a fixed URL — the raw ``main`` branch of the
``localbib-plugins`` repo (ADR-0021's ``marketplace/`` folder, exported as
that repo's root, PRD-marketplace.md). Icons and screenshots are paths inside
the same repo, relative to its root.

Cached under ``Config.MARKETPLACE_CACHE_DIR``: ``index.json`` (the last-fetched
index plus its timestamp) and ``assets/<relative path>`` (downloaded images).
Never raises to its callers — a dead network degrades to the cache, or to
"nothing yet", the way ``routers/version.py``'s release poll does.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Optional

import requests

import ca_trust
from config import Config

logger = logging.getLogger(__name__)

# Raw-Datei des main-Zweigs von localbib-plugins (ADR-0021). Releases (and the
# Marketplace-Index) live in the public repo, the only tree ever published
# (ADR-0015) — mirrors routers/version.py's RELEASES_API_URL.
INDEX_URL = "https://raw.githubusercontent.com/tobiasbartlog/localbib-plugins/main/index.json"
ASSET_BASE_URL = "https://raw.githubusercontent.com/tobiasbartlog/localbib-plugins/main/"

#: "at most once a day" (ADR-0021) — the version-check's piggyback gate
#: (``refresh_if_stale``) and any other caller that passes ``force=False``
#: (e.g. the install endpoint's own index lookup, which does not need a fresh
#: round-trip right after the Marketplace view just fetched one). Opening the
#: Marketplace itself always passes ``force=True`` and so never waits on this
#: window (#199) — a freshly published Add-on must not need 24h to show up.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT = 10
MAX_ASSET_BYTES = 10 * 1024 * 1024


def cache_dir() -> Path:
    return Path(Config.MARKETPLACE_CACHE_DIR)


def _index_cache_path() -> Path:
    return cache_dir() / "index.json"


def cached() -> Optional[dict]:
    """``{"fetched_at": float, "index": dict}`` of the last successful fetch,
    or ``None`` without one (fresh install, never online yet)."""
    try:
        data = json.loads(_index_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("index"), dict):
        return None
    if not isinstance(data.get("fetched_at"), (int, float)):
        return None
    return data


def _store(index: dict) -> dict:
    entry = {"fetched_at": time.time(), "index": index}
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".json.tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False)
        os.replace(tmp, str(_index_cache_path()))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return entry


def fetch(force: bool = False) -> tuple[Optional[dict], bool]:
    """``(entry, offline)`` — the index to show, and whether it is the disk
    cache because the network attempt failed (or, without ``force``, was
    skipped as still fresh).

    ``force=True`` always tries the network first and falls back to the disk
    cache only when that attempt fails — this is what ``GET /api/marketplace``
    uses (opening the Marketplace, #199): a freshly published Add-on must show
    up the moment the view is opened, not up to 24h later. Without ``force``,
    a cache younger than a day is returned unchanged (no request at all) —
    ``refresh_if_stale``'s own once-a-day gate already decided the cache is
    fresh enough before it ever calls ``fetch(force=True)``, and a caller that
    just wants the current index without forcing a round-trip (e.g. the
    install endpoint's own lookup) gets this same window. Never raises: a
    network failure degrades to the cache (or ``None`` without one), never a
    dead page.
    """
    cached_entry = cached()
    if not force and cached_entry and time.time() - cached_entry["fetched_at"] < REFRESH_INTERVAL_SECONDS:
        return cached_entry, False
    try:
        resp = requests.get(INDEX_URL, timeout=REQUEST_TIMEOUT, verify=ca_trust.ca_bundle())
        resp.raise_for_status()
        index = resp.json()
        if not isinstance(index, dict):
            raise ValueError("index.json ist kein Objekt")
    except Exception as exc:  # noqa: BLE001 - every network/parse failure degrades the same way
        logger.info("Marketplace-Index nicht erreichbar (%s) — zeige gecachten Stand.", exc)
        return cached_entry, True
    return _store(index), False


def refresh_if_stale() -> None:
    """The version-check's piggyback (ADR-0021): fetch at most once a day.

    Best-effort and silent — called from ``GET /api/version-check`` on every
    poll, so it must never raise or noticeably slow that request down beyond
    ``REQUEST_TIMEOUT``.
    """
    entry = cached()
    if entry and time.time() - entry["fetched_at"] < REFRESH_INTERVAL_SECONDS:
        return
    try:
        fetch(force=True)
    except Exception as exc:  # noqa: BLE001 - fetch() itself already swallows; belt and suspenders
        logger.warning("Taeglicher Marketplace-Index-Abruf fehlgeschlagen: %s", exc)


# ---------------------------------------------------------------------------
# Images (icons, screenshots) — cached so the browser never loads from GitHub.
# ---------------------------------------------------------------------------

def safe_asset_path(rel_path: str) -> Optional[Path]:
    """The local cache path for ``rel_path``, or ``None`` for a traversal
    attempt — same shape of check as ``services.addon_frontend.resolve_file``:
    reject before ever touching the filesystem, not after."""
    if not rel_path or "\x00" in rel_path:
        return None
    posix = PurePosixPath(rel_path.replace("\\", "/"))
    if posix.is_absolute():
        return None
    parts = posix.parts
    if not parts or any(p in ("..", "") for p in parts) or ":" in parts[0]:
        return None
    directory = (cache_dir() / "assets").resolve()
    target = (directory / Path(*parts)).resolve()
    try:
        target.relative_to(directory)
    except ValueError:
        return None
    return target


def cached_image(rel_path: str) -> Optional[Path]:
    """The local file for ``rel_path`` (relative to the index repo root),
    fetching it once if not already cached. ``None`` on a bad path or a
    failed download — the caller answers 404, never a broken image."""
    target = safe_asset_path(rel_path)
    if target is None:
        return None
    if target.is_file():
        return target
    tmp: Optional[Path] = None
    try:
        resp = requests.get(ASSET_BASE_URL + rel_path, timeout=REQUEST_TIMEOUT,
                            verify=ca_trust.ca_bundle(), stream=True)
        resp.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        tmp = target.with_name(target.name + ".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_ASSET_BYTES:
                    raise ValueError("Bild ist unerwartet gross")
                fh.write(chunk)
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001 - a missing image must not break the page
        logger.info("Marketplace-Bild %s nicht ladbar: %s", rel_path, exc)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return None
    return target


__all__ = [
    "ASSET_BASE_URL",
    "INDEX_URL",
    "REFRESH_INTERVAL_SECONDS",
    "cache_dir",
    "cached",
    "cached_image",
    "fetch",
    "refresh_if_stale",
    "safe_asset_path",
]
