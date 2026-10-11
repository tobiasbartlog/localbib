"""Router: backups (Settings → Backup).

Serves:
  GET  /api/backup            the settings, the folder's state, what a run would
                              write, the backups in the folder, the run under
                              way and the last outcome
  PUT  /api/backup/settings   folder, automatic interval, automatic scope
  POST /api/backup/run        one backup now — ``include_pdfs`` is required:
                              the UI asks every time, the server never assumes

Writing, listing and the one-at-a-time runner live in ``backup.py``; this
module validates and persists the three ``.env`` keys (``BACKUP_DIR``,
``AUTO_BACKUP``, ``AUTO_BACKUP_PDFS``), fills a run's sources from ``Config``
and owns the automatic schedule: a background thread that looks every
``CHECK_EVERY_SECONDS`` whether a backup is due and starts it through the same
runner as the button. Nothing here deletes a backup — see ``backup.py``.

No import from ``webapp`` or any other router; ``webapp`` hands in the release
version and the catalog folder through ``configure``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import backup
import settings_store
import ui_catalog
from config import Config
from logging_setup import log_dir_for

router = APIRouter()

# The .env this router writes; tests redirect it per test, like the settings
# router's. Its settings files go into every backup.
_ENV_PATH = Path(Config.ENV_PATH)

#: The backups ``GET /api/backup`` lists (newest first); the count is complete.
LIST_LIMIT = 30

_context = {
    "app_version": "",
    "locales_dir": str(Path(__file__).resolve().parent.parent / "static" / "locales"),
}


def configure(*, app_version: str, locales_dir: Optional[str] = None) -> None:
    """Called once by ``webapp``: the release version for the manifest and the
    folder of the UI catalogs the restore steps (``README.txt``) come from."""
    _context["app_version"] = app_version
    if locales_dir:
        _context["locales_dir"] = locales_dir


# ---------------------------------------------------------------------------
# Sources and folders
# ---------------------------------------------------------------------------

def protected_folders() -> dict[str, str]:
    """Folders LocalBib clears or rewrites itself; no backup may lie inside
    one. ``library`` gets its own message, the rest are app folders."""
    folders = {
        "library": Config.BASE_DIR,
        "addons": Config.PLUGIN_DIR,
        "marketplace": Config.MARKETPLACE_CACHE_DIR,
        "logs": log_dir_for(os.path.dirname(Config.ENV_PATH)),
    }
    if getattr(sys, "frozen", False):
        # An update replaces the installation folder's contents.
        folders["app"] = os.path.dirname(sys.executable)
    return folders


def _settings_files() -> list[tuple[str, str]]:
    return [(".env", str(_ENV_PATH)),
            ("llm.json", Config.LLM_CONFIG_PATH),
            ("plugins.json", Config.PLUGINS_CONFIG_PATH)]


_RESTORE_KEYS = ("step1", "step2", "step3", "step4", "step5", "step6")
_catalogs: dict[str, ui_catalog.Catalog] = {}


def restore_text(lang: str) -> str:
    """The restore steps for ``README.txt``, from the SPA's catalog — the same
    text the Backup section shows (ADR-0018: Python keeps no catalog). The
    catalog file is read once per language and folder."""
    head = "settings.backup.restore."
    folder = _context["locales_dir"]
    catalog = _catalogs.setdefault(folder, ui_catalog.Catalog(folder, head))
    lines = [catalog.text(lang, head + "title"), ""]
    lines += [f"{i}. {catalog.text(lang, head + key)}" for i, key in enumerate(_RESTORE_KEYS, 1)]
    lines += ["", catalog.text(lang, head + "keys"), ""]
    return "\r\n".join(lines)


def _sources(with_readme: bool = True) -> backup.Sources:
    return backup.Sources(
        db_path=Config.DB_PATH,
        pdf_dir=Config.ALL_DIR,
        settings_files=_settings_files(),
        library_dir=Config.BASE_DIR,
        app_version=_context["app_version"],
        readme=restore_text(Config.UI_LANGUAGE) if with_readme else "",
    )


def _preflight(folder: str) -> Optional[backup.BackupError]:
    """Why no run can start now: no folder, a folder the app may clear (the
    library can have moved since it was chosen), or one that is not there."""
    if not folder:
        return backup.BackupError("error.backup.folderRequired")
    problem = backup.check_folder(folder, protected_folders())
    if problem:
        return problem
    if not os.path.isdir(folder):
        return backup.BackupError("error.backup.folderUnreachable")
    return None


def _job(folder: str, include_pdfs: bool, kind: str):
    sources = _sources()
    return lambda progress: backup.create(folder, sources, include_pdfs=include_pdfs,
                                          kind=kind, progress=progress)


def _http(status: int, error: backup.BackupError) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": error.code, "params": error.params})


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def _wire_entry(entry: dict) -> dict:
    return {"name": entry["name"], "bytes": entry["bytes"],
            "created_at": _iso(entry["created_at"]), "include_pdfs": entry["include_pdfs"]}


def _wire_outcome(outcome: Optional[dict]) -> Optional[dict]:
    if not outcome:
        return None
    out = dict(outcome)
    out["finished_at"] = _iso(out.get("finished_at"))
    return out


def _wire_run(run: Optional[dict]) -> Optional[dict]:
    if not run:
        return None
    out = dict(run)
    out["started_at"] = _iso(out.get("started_at"))
    return out


def _auto_state(listing: dict, status: dict, now: datetime) -> dict:
    """When the next automatic backup is due, and whether the last automatic
    attempt failed with nothing covering the scope since."""
    interval = Config.auto_backup()
    if interval == "off":
        return {"next_at": None, "problem": None}
    include = bool(Config.AUTO_BACKUP_PDFS)
    newest = backup.newest_covering(listing["backups"], include)
    newest_at = newest["created_at"] if newest else None
    next_at = backup.next_due(interval, newest_at, now)
    last = status["last_auto"]
    problem = None
    if last and not last["ok"] and (newest_at is None or newest_at < last["finished_at"]):
        problem = last
        retry = last["finished_at"] + RETRY_AFTER_FAILURE
        if next_at and next_at < retry:
            next_at = retry
    return {"next_at": next_at, "problem": problem}


def payload(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    folder = Config.backup_dir()
    listing = backup.list_backups(folder)
    status = backup.runner.status()
    auto = _auto_state(listing, status, now)
    return {
        "folder": Config.BACKUP_DIR,
        "auto": Config.AUTO_BACKUP,
        "auto_effective": Config.auto_backup(),
        "auto_pdfs": Config.AUTO_BACKUP_PDFS,
        "folder_state": backup.folder_state(folder, Config.BASE_DIR),
        "estimate": backup.estimate(_sources(with_readme=False)),
        "backups": [_wire_entry(b) for b in listing["backups"][:LIST_LIMIT]],
        "count": len(listing["backups"]),
        "total_bytes": listing["total_bytes"],
        "partial": listing["partial"],
        "run": _wire_run(status["run"]),
        "last": _wire_outcome(status["last"]),
        "auto_problem": _wire_outcome(auto["problem"]),
        "next_auto_at": _iso(auto["next_at"]),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/backup")
def get_backup() -> dict:
    return payload()


class BackupSettings(BaseModel):
    folder: Optional[str] = None
    auto: Optional[str] = None
    auto_pdfs: Optional[bool] = None


def _updates(data: BackupSettings) -> dict[str, Optional[str]]:
    """The ``.env`` lines a settings change writes, after checking the change
    against the state it leads to: an automatic backup needs a folder and an
    explicit answer to the PDF question."""
    updates: dict[str, Optional[str]] = {}
    folder = Config.BACKUP_DIR
    if data.folder is not None:
        cleaned = backup.clean_path(data.folder)
        if cleaned:
            problem = backup.check_folder(cleaned, protected_folders())
            if problem:
                raise _http(422, problem)
            try:
                os.makedirs(backup.expand(cleaned), exist_ok=True)
            except OSError:
                raise _http(422, backup.BackupError("error.backup.folderUnreachable"))
        updates["BACKUP_DIR"] = cleaned or None
        folder = cleaned
    scope = Config.AUTO_BACKUP_PDFS
    if data.auto_pdfs is not None:
        scope = data.auto_pdfs
        updates["AUTO_BACKUP_PDFS"] = "true" if scope else "false"
    interval = Config.AUTO_BACKUP
    if data.auto is not None:
        if data.auto not in backup.INTERVALS:
            raise HTTPException(status_code=422, detail={
                "code": "error.settings.invalidChoice", "params": {"key": "AUTO_BACKUP"}})
        interval = data.auto
        updates["AUTO_BACKUP"] = None if interval == "off" else interval
    if interval != "off":
        if not folder:
            raise _http(422, backup.BackupError("error.backup.autoNeedsFolder"))
        if scope is None:
            raise _http(422, backup.BackupError("error.backup.autoNeedsScope"))
    return updates


@router.put("/api/backup/settings")
async def put_backup_settings(data: BackupSettings) -> dict:
    updates = await asyncio.to_thread(_updates, data)
    if updates:
        settings_store.write_env(_ENV_PATH, updates)
        await settings_store.reload_runtime(_ENV_PATH)
    return await asyncio.to_thread(payload)


class RunRequest(BaseModel):
    # Required on purpose: whether the PDFs go along is asked every time.
    include_pdfs: bool


@router.post("/api/backup/run", status_code=202)
def run_backup(data: RunRequest) -> dict:
    folder = Config.backup_dir()
    problem = _preflight(folder)
    if problem:
        raise _http(409, problem)
    if not backup.runner.start(_job(folder, data.include_pdfs, "manual"),
                               kind="manual", include_pdfs=data.include_pdfs):
        raise HTTPException(status_code=409, detail="error.backup.running")
    return payload()


# ---------------------------------------------------------------------------
# The automatic schedule
# ---------------------------------------------------------------------------

#: Start the schedule with the app. The test suite switches it off (conftest).
AUTO_ON_STARTUP = True
#: The first look after the start, then one every ``CHECK_EVERY_SECONDS``.
FIRST_CHECK_SECONDS = 60
CHECK_EVERY_SECONDS = 15 * 60
#: After a failed automatic run the next attempt waits this long.
RETRY_AFTER_FAILURE = timedelta(hours=1)

_schedule = {"stop": threading.Event()}


def auto_tick(now: Optional[datetime] = None) -> str:
    """One look: start an automatic backup if one is due. Returns what it
    found (``off``, ``busy``, ``waiting``, ``failed``, ``not_due``,
    ``started``) — for the tests and the log."""
    now = now or datetime.now()
    interval = Config.auto_backup()
    if interval == "off":
        return "off"
    if backup.runner.running:
        return "busy"
    include = bool(Config.AUTO_BACKUP_PDFS)
    last = backup.runner.status()["last_auto"]
    if last and not last["ok"] and now - last["finished_at"] < RETRY_AFTER_FAILURE:
        return "waiting"
    folder = Config.backup_dir()
    problem = _preflight(folder)
    if problem:
        logging.warning("Automatisches Backup nicht moeglich: %s", problem.code)
        backup.runner.fail("auto", include, problem)
        return "failed"
    newest = backup.newest_covering(backup.list_backups(folder)["backups"], include)
    if not backup.is_due(interval, newest["created_at"] if newest else None, now):
        return "not_due"
    started = backup.runner.start(_job(folder, include, "auto"), kind="auto", include_pdfs=include)
    return "started" if started else "busy"


def _loop(stop: threading.Event) -> None:
    if stop.wait(FIRST_CHECK_SECONDS):
        return
    while True:
        try:
            auto_tick()
        except Exception:  # noqa: BLE001 - the schedule outlives one bad look
            logging.exception("Automatisches Backup: Pruefung fehlgeschlagen")
        if stop.wait(CHECK_EVERY_SECONDS):
            return


def start_schedule() -> Optional[threading.Thread]:
    """Start the automatic schedule in a background thread."""
    if not AUTO_ON_STARTUP:
        return None
    stop = threading.Event()
    _schedule["stop"] = stop
    thread = threading.Thread(target=_loop, args=(stop,), name="localbib-auto-backup", daemon=True)
    thread.start()
    return thread


def stop_schedule() -> None:
    _schedule["stop"].set()
