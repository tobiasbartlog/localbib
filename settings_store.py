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
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from dotenv import dotenv_values

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


# Names that were in the real process environment before the first
# ``load_dotenv`` (startup does not override them). The reload after a save
# must not override them either, or such a key would change value at the first
# settings save. Set once by webapp.py; a key the user saves explicitly leaves
# the set (``write_env``), so the saved value wins from then on.
_process_env_keys: set[str] = set()


def remember_process_env(keys: Iterable[str]) -> None:
    _process_env_keys.clear()
    _process_env_keys.update(keys)


def _load_env_file(path: Path) -> None:
    """``load_dotenv(override=True)`` minus the real process environment."""
    for key, value in dotenv_values(path, encoding="utf-8", interpolate=False).items():
        if value is not None and key not in _process_env_keys:
            os.environ[key] = value


def env_path() -> Path:
    return Path(Config.ENV_PATH)


_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_@%+=:,./\\-]*")


def encode_env_value(value: str) -> str:
    """``value`` as python-dotenv reads it back unchanged.

    A plain value (no space, ``#``, quote, ``$``, ...) is written bare, as
    before. Anything else goes in single quotes, where dotenv does no escape
    processing except ``\\\\`` and ``\\'`` — so exactly those two are escaped.
    A bare backslash path (``C:\\Users\\tim``) is plain: dotenv leaves it alone
    unquoted. Newlines cannot be written on one line and become spaces."""
    value = value.replace("\r", " ").replace("\n", " ")
    if value == value.strip() and _PLAIN_VALUE.fullmatch(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def read_env(path: Path, allowed: Iterable[str]) -> dict[str, str]:
    """The ``KEY=value`` lines of ``path`` restricted to ``allowed`` keys,
    parsed exactly the way the startup ``load_dotenv`` parses them (quotes,
    escapes, inline comments)."""
    allowed = set(allowed)
    if not path.exists():
        return {}
    values = dotenv_values(path, encoding="utf-8", interpolate=False)
    return {k: v for k, v in values.items() if k in allowed and v is not None}


class EnvLocked(OSError):
    """The ``.env`` could not be replaced (another process holds it). The old
    file is untouched; the HTTP layer maps this to ``error.settings.locked``."""


_REPLACE_ATTEMPTS = 3
_REPLACE_DELAY = 0.3  # seconds; tests set it to 0
_env_lock = threading.Lock()  # write_env is called from the tray thread and the event loop


def _write_bytes(path: Path, data: bytes) -> None:
    """Unique temp file next to ``path`` (written, flushed, fsynced) +
    ``os.replace``, so neither a reader nor a crash ever sees a half-written or
    empty file. A refused replace (OneDrive / another process holds the file) is
    retried; if it stays refused ``EnvLocked`` is raised and the old file is
    never opened for writing. The temp file never survives."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp_name, path)
                return
            except OSError as exc:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise EnvLocked(str(exc)) from exc
                time.sleep(_REPLACE_DELAY)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def write_env(path: Path, updates: dict[str, Optional[str]]) -> None:
    """Apply ``updates`` to the file: a string sets or replaces the key's
    line, ``None`` removes it (every occurrence). Other lines are kept
    verbatim and in order; keys not yet present are appended."""
    with _env_lock:
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
                        lines.append(f"{key}={encode_env_value(new_val)}")
                        seen.add(key)
                        continue
                lines.append(line)
        for key, new_val in updates.items():
            if key not in seen and new_val is not None:
                lines.append(f"{key}={encode_env_value(new_val)}")
        # Encode BEFORE the target is opened: a value that cannot be UTF-8 (a lone
        # surrogate) must fail while the old file is still whole.
        data = ("\n".join(lines) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes(path, data)
    for key, new_val in updates.items():
        _process_env_keys.discard(key)
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
    _load_env_file(path)
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
