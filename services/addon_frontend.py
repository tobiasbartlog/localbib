"""What the SPA loads for an active Add-on, and which of its files it may fetch.

Pure (ADR-0006): reads the Manifest and resolves paths, never writes. The
router ``routers/plugins.py`` serves both halves — the per-Add-on load list
(``GET /api/plugins/frontend``) and the files themselves
(``GET /plugins/<id>/static/<path>``).

Only the Bundle's ``frontend/`` folder is ever served. A Manifest path
outside it is dropped from the load list, a request outside it (``..``, an
absolute path, a symlink pointing elsewhere) resolves to ``None``. URLs carry
the Add-on version as ``?v=`` so an update is never served from a stale cache.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote

from plugin_api import PluginManifest

FRONTEND_DIR = "frontend"


def static_url(addon_id: str, rel: str, version: str = "") -> str:
    """URL of ``rel`` (relative to ``frontend/``) of one active Add-on."""
    url = f"/plugins/{quote(addon_id)}/static/{quote(rel)}"
    return f"{url}?v={quote(version)}" if version else url


def _in_frontend(bundle_rel: Optional[str]) -> Optional[str]:
    """``frontend/x/y.js`` -> ``x/y.js``; ``None`` for anything outside ``frontend/``."""
    if not bundle_rel:
        return None
    parts = PurePosixPath(bundle_rel.replace("\\", "/")).parts
    if len(parts) < 2 or parts[0] != FRONTEND_DIR or any(p in ("..", "") for p in parts[1:]):
        return None
    return "/".join(parts[1:])


def describe(addon_id: str, manifest: PluginManifest) -> Optional[dict]:
    """The load list of one Add-on, or ``None`` when it ships no frontend.

    Order of loading is the SPA's (assets, stylesheet, script); ``locales``
    maps each shipped language to its file, ``default_language`` is the
    fallback when the UI language is not among them."""
    spec = manifest.frontend
    if spec is None:
        return None
    version = manifest.version

    def url(bundle_rel: Optional[str]) -> Optional[str]:
        rel = _in_frontend(bundle_rel)
        return static_url(addon_id, rel, version) if rel else None

    script = url(spec.script)
    if script is None:
        return None
    locales = {lang: u for lang, path in sorted(spec.locales.items()) if (u := url(path))}
    default = manifest.default_language or (manifest.languages[0] if manifest.languages else "")
    if default not in locales and locales:
        default = "en" if "en" in locales else next(iter(locales))
    nav = manifest.nav
    return {
        "id": addon_id,
        "version": version,
        "assets": [u for a in spec.assets if (u := url(a))],
        "stylesheet": url(spec.stylesheet),
        "script": script,
        "locales": locales,
        "default_language": default,
        "nav": {"id": nav.id, "route": nav.route, "view": nav.view} if nav else None,
    }


def resolve_file(bundle_dir: Path, rel: str) -> Optional[Path]:
    """The file ``rel`` below the Bundle's ``frontend/``, or ``None`` if it is
    missing or would leave that folder."""
    if not rel or "\x00" in rel:
        return None
    rel_path = PurePosixPath(rel.replace("\\", "/"))
    if rel_path.is_absolute() or any(p == ".." for p in rel_path.parts) or ":" in rel_path.parts[0]:
        return None
    root = (bundle_dir / FRONTEND_DIR).resolve()
    try:
        target = (root / Path(*rel_path.parts)).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None
