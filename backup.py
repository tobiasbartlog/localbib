"""Backups of the library: written, listed — and never deleted by LocalBib.

A Backup is one zip file in the user's backup folder (``BACKUP_DIR``)::

    LocalBib-Backup_2026-10-09_14-30-12_with-pdfs.zip
    LocalBib-Backup_2026-10-09_14-30-12_data-only.zip

It holds a consistent snapshot of the database (SQLite's backup API, so a write
in progress never tears it), the three settings documents (``.env``,
``llm.json``, ``plugins.json`` — API keys included, which is why the UI says
so), a ``manifest.json``, a ``README.txt`` with the restore steps and — only if
the user chose so for this run — every file of the library's ``all/`` folder
under ``pdfs/``. The category folders are not in it: they are derived and a
recompute rebuilds them.

**LocalBib never deletes a backup** — the one rule this module exists for.
There is no retention, no rotation, no "keep the last n": old backups stay
until the user removes them. Every write goes to a fresh ``*.partial`` file
opened exclusively (``"xb"``: an existing file is never overwritten), is read
back and CRC-checked, and only then renamed to a name nothing else holds. The
one removal in this module is that ``*.partial`` of the *same* run, when the
run fails — a file that never was a backup. A finished backup is made
read-only on top. ``tests/test_backup.py`` pins both halves.

The backup folder may not lie inside a folder LocalBib itself clears or
rewrites (the library, the Add-on folders, the logs); ``check_folder`` refuses
it at the write site and every run checks again, since the library can move.

Pure rules (names, due dates, the folder check) and the file work sit side by
side here, like ``autostart.py``; ``routers/backup.py`` decides when to run
and persists the settings. ``runner`` is the one place a run happens, manual or
automatic: one at a time, in a worker thread, its progress and last outcome
readable by the router.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

#: The format of ``manifest.json``; a reader refuses one it does not know.
FORMAT = "localbib-backup/1"

#: The automatic interval choices. ``off`` is the default.
INTERVALS = ("off", "daily", "weekly", "monthly")

#: The suffix of a file being written. Never a backup.
PARTIAL_SUFFIX = ".partial"

#: Room left on the target beyond the estimate, so a backup never fills a disk.
FREE_SPACE_MARGIN = 50 * 1024 * 1024

_SCOPE = {True: "with-pdfs", False: "data-only"}
_NAME = re.compile(
    r"^LocalBib-Backup_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(?:-(\d+))?_(with-pdfs|data-only)\.zip$")

#: What a backup's file names in the archive are.
DB_ARCNAME = "literatur.db"
SETTINGS_PREFIX = "settings/"
PDF_PREFIX = "pdfs/"


class BackupError(Exception):
    """A run that cannot go ahead or did not finish; ``code`` is a catalog key
    (ADR-0018), ``params`` fill it."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


# ---------------------------------------------------------------------------
# Rules (pure)
# ---------------------------------------------------------------------------

def backup_name(now: datetime, include_pdfs: bool, taken: Iterable[str] = ()) -> str:
    """The file name for a backup made at ``now``, unique among ``taken``.
    Two runs in one second (or the hour a clock goes back) get ``-2``, ``-3``."""
    taken = set(taken)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    scope = _SCOPE[bool(include_pdfs)]
    name = f"LocalBib-Backup_{stamp}_{scope}.zip"
    n = 2
    while name in taken or name + PARTIAL_SUFFIX in taken:
        name = f"LocalBib-Backup_{stamp}-{n}_{scope}.zip"
        n += 1
    return name


def parse_name(name: str) -> Optional[dict]:
    """``{created_at, include_pdfs}`` of one of our file names, else ``None``."""
    match = _NAME.match(name)
    if not match:
        return None
    day, hh, mm, ss, _n, scope = match.groups()
    try:
        created = datetime.strptime(f"{day} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {"created_at": created, "include_pdfs": scope == "with-pdfs"}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_within(path: str, parent: str) -> bool:
    """``path`` is ``parent`` or lies below it (after resolving links)."""
    if not path or not parent:
        return False
    a, b = _norm(path), _norm(parent)
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:  # different drives
        return False


def expand(path: str) -> str:
    """``~`` and ``%VARS%`` expanded, like the library folder at startup."""
    return os.path.expanduser(os.path.expandvars((path or "").strip()))


def clean_path(value: str) -> str:
    """Whitespace and one pair of surrounding quotes off a pasted path
    (Explorer's "Copy as path" yields ``"D:\\Backups"``)."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def check_folder(folder: str, protected: dict[str, str]) -> Optional[BackupError]:
    """Why ``folder`` cannot hold backups, or ``None``. ``protected`` maps a
    name (``library``, ``addons``, ``logs``, …) to a folder LocalBib clears or
    rewrites — a backup inside one would not be safe from the app itself."""
    if not (folder or "").strip():
        return BackupError("error.backup.folderRequired")
    path = expand(folder)
    if not os.path.isabs(path):
        return BackupError("error.backup.folderNotAbsolute")
    for name, root in protected.items():
        if root and is_within(path, root):
            return BackupError("error.backup.insideLibrary" if name == "library"
                               else "error.backup.insideAppFolder")
    return None


def is_due(interval: str, newest: Optional[datetime], now: datetime) -> bool:
    """Whether an automatic backup is due. ``daily`` means one per calendar
    day; ``weekly`` and ``monthly`` count 7 and 30 days from the newest backup
    that covers the scope (``newest``)."""
    if interval not in INTERVALS or interval == "off":
        return False
    if newest is None:
        return True
    if interval == "daily":
        return newest.date() < now.date()
    days = 7 if interval == "weekly" else 30
    return now - newest >= timedelta(days=days)


def next_due(interval: str, newest: Optional[datetime], now: datetime) -> Optional[datetime]:
    """When the next automatic backup becomes due (``now`` if it already is)."""
    if interval not in INTERVALS or interval == "off":
        return None
    if is_due(interval, newest, now):
        return now
    if interval == "daily":
        return datetime.combine(newest.date() + timedelta(days=1), datetime.min.time())
    return newest + timedelta(days=7 if interval == "weekly" else 30)


def newest_covering(backups: list[dict], include_pdfs: bool) -> Optional[dict]:
    """The newest backup that holds at least ``include_pdfs``' scope: one with
    PDFs covers a data-only schedule, never the other way round."""
    fitting = [b for b in backups if b["include_pdfs"] or not include_pdfs]
    return max(fitting, key=lambda b: b["created_at"]) if fitting else None


#: After how many days without a covering backup a schedule counts as overdue:
#: twice its interval, so one missed run (the laptop stayed shut) is no alarm.
OVERDUE_DAYS = {"daily": 2, "weekly": 14, "monthly": 60}


def health(*, folder_set: bool, interval: str, include_pdfs: bool, backups: list[dict],
           last_auto: Optional[dict], now: datetime) -> Optional[dict]:
    """What the Library Diagnosis should say about backups, or ``None``:
    ``{"kind": "backup_none"}`` while there is no folder or (without a
    schedule) not a single backup; ``{"kind": "backup_overdue", "days": n}``
    when a schedule is on but nothing covers its scope for ``n`` days — or its
    first run already failed."""
    if not folder_set or (interval == "off" and not backups):
        return {"kind": "backup_none"}
    if interval not in OVERDUE_DAYS:
        return None
    days = OVERDUE_DAYS[interval]
    newest = newest_covering(backups, include_pdfs)
    if newest is None:
        failed = bool(last_auto) and not last_auto.get("ok")
        return {"kind": "backup_overdue", "days": days} if failed else None
    if now - newest["created_at"] > timedelta(days=days):
        return {"kind": "backup_overdue", "days": days}
    return None


# ---------------------------------------------------------------------------
# Reading the folders
# ---------------------------------------------------------------------------

def tree_size(root: str) -> tuple[int, int]:
    """``(files, bytes)`` below ``root``; a missing folder is ``(0, 0)``."""
    count = total = 0
    for _rel, path in _files(root):
        try:
            total += os.path.getsize(path)
            count += 1
        except OSError:
            pass
    return count, total


def _files(root: str) -> list[tuple[str, str]]:
    """``(posix path relative to root, absolute path)`` of every regular file
    below ``root``, sorted. Links are not followed."""
    out: list[tuple[str, str]] = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            out.append((rel, path))
    return out


def list_backups(folder: str) -> dict:
    """The backups in ``folder``, newest first, plus the ``*.partial`` files
    an interrupted run left behind (shown, never removed). A folder that is
    not there lists nothing."""
    backups: list[dict] = []
    partial: list[str] = []
    path = expand(folder)
    try:
        names = os.listdir(path) if path else []
    except OSError:
        names = []
    for name in names:
        full = os.path.join(path, name)
        if name.endswith(PARTIAL_SUFFIX) and parse_name(name[:-len(PARTIAL_SUFFIX)]):
            partial.append(name)
            continue
        info = parse_name(name)
        if not info or not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        backups.append({"name": name, "bytes": size, **info})
    backups.sort(key=lambda b: (b["created_at"], b["name"]), reverse=True)
    return {"backups": backups, "partial": sorted(partial),
            "total_bytes": sum(b["bytes"] for b in backups)}


def folder_state(folder: str, library_dir: str) -> dict:
    """Is the folder there, how much room is left, and does it share a drive
    with the library (then a failing disk takes both)."""
    path = expand(folder)
    state = {"exists": False, "free_bytes": None, "same_drive": False}
    if not path or not os.path.isdir(path):
        return state
    state["exists"] = True
    try:
        state["free_bytes"] = shutil.disk_usage(path).free
    except OSError:
        pass
    try:
        state["same_drive"] = os.stat(path).st_dev == os.stat(library_dir).st_dev
    except OSError:
        pass
    return state


# ---------------------------------------------------------------------------
# Writing one backup
# ---------------------------------------------------------------------------

@dataclass
class Sources:
    """What a backup is made of. The router fills it from ``Config``; tests
    point it at a temp tree."""

    db_path: str
    pdf_dir: str
    settings_files: list[tuple[str, str]]  # (name in the archive, path)
    library_dir: str
    app_version: str = ""
    readme: str = ""


def _data_bytes(sources: Sources) -> int:
    return sum(_size(p) for p in [sources.db_path] + [p for _a, p in sources.settings_files])


def estimate(sources: Sources) -> dict:
    """``{data_bytes, pdf_bytes, pdf_count}`` — what a run will roughly write."""
    count, pdf_bytes = tree_size(sources.pdf_dir)
    return {"data_bytes": _data_bytes(sources), "pdf_bytes": pdf_bytes, "pdf_count": count}


def snapshot_db(db_path: str, dest: str) -> dict:
    """Copy the live database to ``dest`` through SQLite's backup API (a
    consistent state even while the app writes) and describe the copy."""
    src = sqlite3.connect(db_path)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
            integrity = out.execute("PRAGMA quick_check").fetchone()[0]
            try:
                items = out.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            except sqlite3.Error:
                items = None
        finally:
            out.close()
    finally:
        src.close()
    digest = hashlib.sha256()
    with open(dest, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return {"file": DB_ARCNAME, "bytes": os.path.getsize(dest), "sha256": digest.hexdigest(),
            "items": items, "integrity": integrity}


Progress = Callable[[str, int, int], None]


def create(folder: str, sources: Sources, *, include_pdfs: bool, kind: str,
           now: Optional[datetime] = None, progress: Optional[Progress] = None) -> dict:
    """Write one backup into ``folder`` and return its listing entry plus
    ``skipped`` (files that vanished or could not be read mid-run).

    ``progress(phase, done, total)`` is called as the run goes: ``database``,
    ``pdfs`` (one tick per file), ``verifying``. Raises ``BackupError`` when
    the run cannot start or did not finish; then nothing but this run's own
    ``*.partial`` is touched, and that one is removed again."""
    now = now or datetime.now()
    report = progress or (lambda phase, done, total: None)
    path = expand(folder)
    if not path or not os.path.isdir(path):
        raise BackupError("error.backup.folderUnreachable")
    if not os.path.isfile(sources.db_path):
        raise BackupError("error.backup.noDatabase")

    files = _files(sources.pdf_dir) if include_pdfs else []
    needed = _data_bytes(sources) + sum(_size(p) for _r, p in files)
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        raise BackupError("error.backup.folderUnreachable")
    if free < needed + FREE_SPACE_MARGIN:
        raise BackupError("error.backup.diskFull", needed=needed, free=free)

    try:
        taken = os.listdir(path)
    except OSError:
        raise BackupError("error.backup.folderUnreachable")
    name = backup_name(now, include_pdfs, taken)
    final = os.path.join(path, name)
    partial = final + PARTIAL_SUFFIX
    try:
        handle = open(partial, "xb")  # exclusive: an existing file is never overwritten
    except OSError as exc:
        raise BackupError("error.backup.writeFailed", message=str(exc)[:300])

    skipped: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="localbib-backup-") as tmp:
            report("database", 0, 1)
            db_copy = os.path.join(tmp, DB_ARCNAME)
            database = snapshot_db(sources.db_path, db_copy)
            report("database", 1, 1)
            with handle:
                with zipfile.ZipFile(handle, "w", allowZip64=True) as zf:
                    zf.write(db_copy, DB_ARCNAME, compress_type=zipfile.ZIP_DEFLATED)
                    settings = []
                    for arcname, src in sources.settings_files:
                        if os.path.isfile(src):
                            zf.write(src, SETTINGS_PREFIX + arcname, compress_type=zipfile.ZIP_DEFLATED)
                            settings.append(arcname)
                    pdf_bytes = 0
                    for i, (rel, src) in enumerate(files, 1):
                        try:
                            # PDFs are compressed already: stored, not deflated.
                            zf.write(src, PDF_PREFIX + rel, compress_type=zipfile.ZIP_STORED)
                            pdf_bytes += _size(src)
                        except OSError as exc:
                            # Deleted or locked between listing and copy: the
                            # backup goes on, the manifest and the UI say which.
                            logging.warning("Backup: %s nicht gesichert: %s", src, exc)
                            skipped.append(rel)
                        report("pdfs", i, len(files))
                    manifest = {
                        "format": FORMAT,
                        "created_at": now.isoformat(timespec="seconds"),
                        "kind": kind,
                        "include_pdfs": bool(include_pdfs),
                        "app_version": sources.app_version,
                        "library_dir": sources.library_dir,
                        "database": database,
                        "settings": settings,
                        "pdfs": {"count": len(files) - len(skipped), "bytes": pdf_bytes,
                                 "skipped": skipped} if include_pdfs else None,
                    }
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False),
                                compress_type=zipfile.ZIP_DEFLATED)
                    if sources.readme:
                        zf.writestr("README.txt", sources.readme, compress_type=zipfile.ZIP_DEFLATED)
                # The archive is complete only once its directory is written.
                handle.flush()
                os.fsync(handle.fileno())
        report("verifying", 0, 1)
        with zipfile.ZipFile(partial) as check:
            broken = check.testzip()
        if broken is not None:
            raise BackupError("error.backup.verifyFailed", file=broken)
        report("verifying", 1, 1)
        _rename(partial, final)
    except BaseException as exc:
        _discard_partial(partial)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, Exception):
            raise BackupError("error.backup.writeFailed", message=str(exc)[:300]) from exc
        raise
    _make_read_only(final)
    return {"name": name, "bytes": _size(final), "created_at": now, "include_pdfs": bool(include_pdfs),
            "skipped": skipped, "items": database.get("items"), "integrity": database.get("integrity")}


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


#: How often a rename is tried before the run fails. A sync client (OneDrive &
#: Co.) or a virus scanner may hold a freshly closed file for a moment.
RENAME_ATTEMPTS = 6
RENAME_PAUSE_SECONDS = 1.0


def _rename(partial: str, final: str) -> None:
    """``partial`` → ``final``, never onto an existing file."""
    for attempt in range(RENAME_ATTEMPTS):
        if os.path.exists(final):  # os.rename would replace it on POSIX
            raise BackupError("error.backup.writeFailed", message=final)
        try:
            os.rename(partial, final)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(RENAME_PAUSE_SECONDS)


def _discard_partial(partial: str) -> None:
    """Remove the ``*.partial`` this very run created, after it failed. The
    only removal in this module — guarded so it can never hit a backup."""
    if not partial.endswith(PARTIAL_SUFFIX):
        raise ValueError(f"not a partial file: {partial}")
    try:
        os.remove(partial)
    except OSError as exc:
        logging.warning("Backup: unvollstaendige Datei %s bleibt liegen: %s", partial, exc)


def _make_read_only(path: str) -> None:
    """A finished backup is read-only: no tool overwrites it by accident, and
    deleting it in Explorer asks first."""
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The runner: one run at a time, manual or automatic
# ---------------------------------------------------------------------------

class Runner:
    """Runs one backup at a time in a worker thread and keeps what the UI and
    the scheduler need: the progress of the run under way and the outcome of
    the last one (and of the last automatic one, which nobody watches)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run: Optional[dict] = None
        self._last: Optional[dict] = None
        self._last_auto: Optional[dict] = None
        #: How a job is started; tests swap in ``lambda target: target()``.
        self.spawn: Callable[[Callable[[], None]], None] = self._thread

    @staticmethod
    def _thread(target: Callable[[], None]) -> None:
        threading.Thread(target=target, name="localbib-backup", daemon=True).start()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._run is not None

    def start(self, job: Callable[[Progress], dict], *, kind: str, include_pdfs: bool) -> bool:
        """Start ``job(progress)``; ``False`` if a run is under way already."""
        with self._lock:
            if self._run is not None:
                return False
            self._run = {"kind": kind, "include_pdfs": bool(include_pdfs), "phase": "database",
                         "done": 0, "total": 0, "started_at": datetime.now()}
        self.spawn(lambda: self._execute(job, kind, include_pdfs))
        return True

    def fail(self, kind: str, include_pdfs: bool, error: BackupError) -> None:
        """Record a run that could not even start (e.g. the folder is gone).
        A run under way meanwhile keeps its progress."""
        self._finish({"ok": False, "kind": kind, "include_pdfs": bool(include_pdfs),
                      "finished_at": datetime.now(), "error": error.code, "params": _params(error)},
                     ends_run=False)

    def _progress(self, phase: str, done: int, total: int) -> None:
        with self._lock:
            if self._run is not None:
                self._run.update(phase=phase, done=done, total=total)

    def _execute(self, job: Callable[[Progress], dict], kind: str, include_pdfs: bool) -> None:
        try:
            result = job(self._progress)
            outcome = {"ok": True, "kind": kind, "include_pdfs": bool(include_pdfs),
                       "finished_at": datetime.now(), "name": result.get("name"),
                       "bytes": result.get("bytes"), "skipped": len(result.get("skipped") or [])}
        except BackupError as exc:
            logging.warning("Backup (%s) fehlgeschlagen: %s %s", kind, exc.code, exc.params)
            outcome = {"ok": False, "kind": kind, "include_pdfs": bool(include_pdfs),
                       "finished_at": datetime.now(), "error": exc.code, "params": _params(exc)}
        except Exception as exc:  # noqa: BLE001 - the thread must report, not die
            logging.exception("Backup (%s) fehlgeschlagen", kind)
            outcome = {"ok": False, "kind": kind, "include_pdfs": bool(include_pdfs),
                       "finished_at": datetime.now(), "error": "error.backup.writeFailed",
                       "params": {"message": str(exc)[:300]}}
        self._finish(outcome)

    def _finish(self, outcome: dict, ends_run: bool = True) -> None:
        with self._lock:
            if ends_run:
                self._run = None
            self._last = outcome
            if outcome["kind"] == "auto":
                self._last_auto = outcome

    def status(self) -> dict:
        with self._lock:
            run = dict(self._run) if self._run else None
            last = dict(self._last) if self._last else None
            last_auto = dict(self._last_auto) if self._last_auto else None
        if run:
            total = run["total"] or 0
            run["percent"] = int(100 * run["done"] / total) if total else 0
        return {"run": run, "last": last, "last_auto": last_auto}

    def reset(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._run = self._last = self._last_auto = None


def _params(error: BackupError) -> dict:
    return {k: v for k, v in error.params.items() if isinstance(v, (str, int, float))}


runner = Runner()
