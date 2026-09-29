"""Shared persistence for the settings writers, plus the runtime reload.

Neutral module (like ``paper_ingest``): ``routers/settings.py`` (the flat
``.env`` keys), ``routers/llm.py`` (the LLM connection document) and
``routers/plugins.py`` (the Add-on document ``plugins.json``) all
persist here — a router never imports another router, and the reload chain
after a save (re-read the ``.env``, refresh the trust store, rebuild
``Config``, re-sync the plugins) must be *one* chain, or the two writers drift
the way ``config.py`` and the settings router once drifted on defaults.

The ``.env`` writer is line-preserving: comments and unknown lines survive,
known keys are replaced in place, new keys are appended, ``None`` removes a
line. Removing also drops the variable from ``os.environ`` — ``load_dotenv``
never unsets anything, so a key deleted from the file would otherwise live on
in the process until restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterable, Optional

from dotenv import load_dotenv

import ca_trust
import plugin_loader
import plugins_config
from config import Config

# Plugin-router reconcile hook — injected by webapp.py at mount time so no
# router (and not this module) ever imports webapp.
_reconcile_plugin_routers: Optional[Callable[[], None]] = None


def set_reconcile_hook(fn: Callable[[], None]) -> None:
    global _reconcile_plugin_routers
    _reconcile_plugin_routers = fn


def env_path() -> Path:
    return Path(Config.ENV_PATH)


def read_env(path: Path, allowed: Iterable[str]) -> dict[str, str]:
    """The ``KEY=value`` lines of ``path`` restricted to ``allowed`` keys.
    Values are returned raw (stripped, unquoted) — the file is written by this
    module without quoting, so there is nothing to unquote."""
    allowed = set(allowed)
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key in allowed:
                out[key] = value.strip()
    return out


def write_env(path: Path, updates: dict[str, Optional[str]]) -> None:
    """Apply ``updates`` to the file: a string sets or replaces the key's
    line, ``None`` removes it (every occurrence). Other lines are kept
    verbatim and in order; keys not yet present are appended."""
    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in updates:
                    new_val = updates[key]
                    if new_val is None or key in seen:
                        continue
                    lines.append(f"{key}={new_val}")
                    seen.add(key)
                    continue
            lines.append(line)
    for key, new_val in updates.items():
        if key not in seen and new_val is not None:
            lines.append(f"{key}={new_val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, new_val in updates.items():
        if new_val is None:
            os.environ.pop(key, None)


def save_plugins_document(doc: dict, env: Optional[Path] = None) -> None:
    """Persist ``plugins.json`` — the one writer of the Add-on document.

    Every save is also the moment the legacy plugin keys of an old ``.env``
    leave it (``env``, default the app's): from now on the document is the
    truth, and the Add-ons read their values from it through ``SettingsApi``."""
    plugins_config.save(Config.PLUGINS_CONFIG_PATH, doc)
    path = Path(env) if env is not None else env_path()
    if read_env(path, plugins_config.LEGACY_ENV_KEYS):
        write_env(path, {key: None for key in plugins_config.LEGACY_ENV_KEYS})
    Config.load_plugins_document()


async def reload_runtime(path: Path) -> None:
    """The one post-save chain: ``.env`` -> environment -> trust store ->
    ``Config`` -> plugins -> routers. Called after every settings write,
    whichever router did it."""
    load_dotenv(path, override=True)
    ca_trust.install()
    Config.reload_from_env()
    # Plugins und Add-ons live aktivieren/deaktivieren. sync_all ist pro
    # Plugin abgesichert (ADR-0021); der Reconcile laeuft danach in JEDEM Fall
    # — der Folgebug aus ADR-0009 war ein Abbruch, der ihn uebersprang.
    try:
        await plugin_loader.sync_all()
    except Exception as exc:
        logging.warning("Plugin-Sync nach Settings-Aenderung fehlgeschlagen: %s", exc)
    if _reconcile_plugin_routers is not None:
        try:
            _reconcile_plugin_routers()
        except Exception as exc:
            logging.warning("Plugin-Router-Reconcile fehlgeschlagen: %s", exc)
