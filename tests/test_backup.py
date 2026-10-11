"""backup.py: what a backup holds, its names, the due rule, the folder rule —
and the one promise above all: LocalBib never deletes or overwrites a backup."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backup

NOW = datetime(2026, 10, 9, 14, 30, 12)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "Literatur"
    pdfs = lib / "all"
    (pdfs / "sub").mkdir(parents=True)
    (pdfs / "a.pdf").write_bytes(b"%PDF-1.4 first")
    (pdfs / "sub" / "b.pdf").write_bytes(b"%PDF-1.4 second")
    db = lib / "literatur.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO papers (title) VALUES ('One'), ('Two')")
    conn.commit()
    conn.close()
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / ".env").write_text("UI_LANGUAGE=de\n", encoding="utf-8")
    (settings / "llm.json").write_text("{}", encoding="utf-8")
    sources = backup.Sources(
        db_path=str(db), pdf_dir=str(pdfs),
        settings_files=[(".env", str(settings / ".env")), ("llm.json", str(settings / "llm.json")),
                        ("plugins.json", str(settings / "plugins.json"))],  # missing: skipped
        library_dir=str(lib), app_version="9.9.9", readme="restore me")
    dest = tmp_path / "backups"
    dest.mkdir()
    return {"sources": sources, "dest": dest, "lib": lib, "pdfs": pdfs, "db": db}


def _names(zf: zipfile.ZipFile) -> set[str]:
    return set(zf.namelist())


# ---------------------------------------------------------------------------
# What a backup holds
# ---------------------------------------------------------------------------

def test_data_only_holds_database_settings_manifest_readme_and_no_pdfs(library):
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    assert entry["name"] == "LocalBib-Backup_2026-10-09_14-30-12_data-only.zip"
    with zipfile.ZipFile(library["dest"] / entry["name"]) as zf:
        assert _names(zf) == {"literatur.db", "settings/.env", "settings/llm.json", "manifest.json", "README.txt"}
        manifest = json.loads(zf.read("manifest.json"))
        assert zf.read("README.txt") == b"restore me"
    assert manifest["format"] == backup.FORMAT
    assert manifest["include_pdfs"] is False and manifest["pdfs"] is None
    assert manifest["kind"] == "manual"
    assert manifest["app_version"] == "9.9.9"
    assert manifest["settings"] == [".env", "llm.json"]
    assert manifest["database"]["items"] == 2
    assert manifest["database"]["integrity"] == "ok"


def test_with_pdfs_holds_every_file_of_the_pdf_folder(library):
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=True, kind="manual", now=NOW)
    assert entry["name"].endswith("_with-pdfs.zip")
    with zipfile.ZipFile(library["dest"] / entry["name"]) as zf:
        assert zf.read("pdfs/a.pdf") == b"%PDF-1.4 first"
        assert zf.read("pdfs/sub/b.pdf") == b"%PDF-1.4 second"
        assert zf.getinfo("pdfs/a.pdf").compress_type == zipfile.ZIP_STORED
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["pdfs"] == {"count": 2, "bytes": 29, "skipped": []}


def test_the_database_in_a_backup_opens_and_matches_its_checksum(library, tmp_path):
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="auto", now=NOW)
    out = tmp_path / "restored"
    with zipfile.ZipFile(library["dest"] / entry["name"]) as zf:
        zf.extract("literatur.db", out)
        manifest = json.loads(zf.read("manifest.json"))
    restored = out / "literatur.db"
    assert _sha(restored) == manifest["database"]["sha256"]
    conn = sqlite3.connect(restored)
    try:
        assert [r[0] for r in conn.execute("SELECT title FROM papers ORDER BY id")] == ["One", "Two"]
    finally:
        conn.close()


def test_a_file_that_vanishes_mid_run_is_skipped_and_named(library):
    def vanish(phase, done, total):
        if phase == "database" and done == 1:
            (library["pdfs"] / "a.pdf").unlink()
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=True, kind="manual",
                          now=NOW, progress=vanish)
    assert entry["skipped"] == ["a.pdf"]
    with zipfile.ZipFile(library["dest"] / entry["name"]) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert "pdfs/sub/b.pdf" in zf.namelist() and "pdfs/a.pdf" not in zf.namelist()
    assert manifest["pdfs"]["skipped"] == ["a.pdf"]


def test_progress_reports_each_phase(library):
    seen = []
    backup.create(str(library["dest"]), library["sources"], include_pdfs=True, kind="manual", now=NOW,
                  progress=lambda phase, done, total: seen.append((phase, done, total)))
    assert ("pdfs", 2, 2) in seen
    assert seen[0] == ("database", 0, 1) and seen[-1] == ("verifying", 1, 1)


# ---------------------------------------------------------------------------
# Never delete, never overwrite
# ---------------------------------------------------------------------------

def test_a_second_backup_in_the_same_second_gets_its_own_name(library):
    first = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    before = _sha(library["dest"] / first["name"])
    second = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    assert second["name"] == "LocalBib-Backup_2026-10-09_14-30-12-2_data-only.zip"
    assert _sha(library["dest"] / first["name"]) == before


def test_everything_already_in_the_folder_stays_untouched(library):
    dest = library["dest"]
    old = backup.create(str(dest), library["sources"], include_pdfs=True, kind="manual", now=NOW - timedelta(days=400))
    (dest / "notes.txt").write_text("mine", encoding="utf-8")
    (dest / "LocalBib-Backup_2020-01-01_00-00-00_data-only.zip").write_bytes(b"not even a zip")
    before = {p.name: _sha(p) for p in dest.iterdir()}
    for i in range(3):
        backup.create(str(dest), library["sources"], include_pdfs=bool(i % 2), kind="auto", now=NOW + timedelta(days=i))
    after = {p.name: _sha(p) for p in dest.iterdir()}
    assert {name: after[name] for name in before} == before
    assert old["name"] in after and len(after) == len(before) + 3


def test_a_failed_run_removes_only_its_own_partial_file(library):
    dest = library["dest"]
    kept = backup.create(str(dest), library["sources"], include_pdfs=False, kind="manual", now=NOW - timedelta(days=1))
    before = {p.name: _sha(p) for p in dest.iterdir()}

    def boom(phase, done, total):
        if phase == "pdfs":
            raise RuntimeError("disk gone")
    with pytest.raises(backup.BackupError) as err:
        backup.create(str(dest), library["sources"], include_pdfs=True, kind="manual", now=NOW, progress=boom)
    assert err.value.code == "error.backup.writeFailed"
    assert {p.name: _sha(p) for p in dest.iterdir()} == before
    assert kept["name"] in before


def test_a_briefly_locked_file_is_renamed_on_a_later_try(library, monkeypatch):
    """A sync client or virus scanner may hold the fresh file for a moment."""
    real_rename = os.rename
    tries = []

    def rename(src, dst):
        tries.append(src)
        if len(tries) < 3:
            raise PermissionError("in use by OneDrive")
        real_rename(src, dst)
    monkeypatch.setattr(backup.os, "rename", rename)
    monkeypatch.setattr(backup, "RENAME_PAUSE_SECONDS", 0)
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    assert len(tries) == 3
    assert [p.name for p in library["dest"].iterdir()] == [entry["name"]]


def test_a_file_that_stays_locked_fails_the_run_and_leaves_no_partial(library, monkeypatch):
    def rename(src, dst):
        raise PermissionError("in use")
    monkeypatch.setattr(backup.os, "rename", rename)
    monkeypatch.setattr(backup, "RENAME_PAUSE_SECONDS", 0)
    with pytest.raises(backup.BackupError) as err:
        backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    assert err.value.code == "error.backup.writeFailed"
    assert list(library["dest"].iterdir()) == []


def test_a_finished_backup_is_read_only(library):
    entry = backup.create(str(library["dest"]), library["sources"], include_pdfs=False, kind="manual", now=NOW)
    # The mode bits, not os.access: root may write anything, so os.access
    # would say "writable" in a container that runs the suite as root.
    mode = (library["dest"] / entry["name"]).stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_too_little_room_refuses_before_writing_anything(library, monkeypatch):
    class Usage:
        free = 1024
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda path: Usage)
    with pytest.raises(backup.BackupError) as err:
        backup.create(str(library["dest"]), library["sources"], include_pdfs=True, kind="manual", now=NOW)
    assert err.value.code == "error.backup.diskFull"
    assert list(library["dest"].iterdir()) == []


def test_a_missing_folder_is_reported(library, tmp_path):
    with pytest.raises(backup.BackupError) as err:
        backup.create(str(tmp_path / "unplugged"), library["sources"], include_pdfs=False, kind="auto", now=NOW)
    assert err.value.code == "error.backup.folderUnreachable"


def _removal_calls(path: Path) -> list[str]:
    """Every function in ``path`` that calls something able to remove or
    replace a file: ``os.remove`` & Co., ``shutil.rmtree``/``move``, any
    ``.unlink()``."""
    module_removers = {"remove", "unlink", "rmdir", "removedirs", "replace", "rmtree", "move"}
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            target, attr = node.func.value, node.func.attr
            on_module = isinstance(target, ast.Name) and target.id in {"os", "shutil"}
            if (on_module and attr in module_removers) or attr in {"unlink", "rmtree"}:
                found.append(func.name)
    return found


def test_no_code_path_can_remove_a_backup():
    """The promise, structurally: the only removal in the backup code is the
    failed run's own ``*.partial`` (``_discard_partial``, which refuses any
    other name); the router removes nothing."""
    root = Path(backup.__file__).resolve().parent
    assert set(_removal_calls(root / "backup.py")) == {"_discard_partial"}
    assert _removal_calls(root / "routers" / "backup.py") == []
    with pytest.raises(ValueError):
        backup._discard_partial(str(root / "LocalBib-Backup_2026-10-09_14-30-12_data-only.zip"))


# ---------------------------------------------------------------------------
# Names and listing
# ---------------------------------------------------------------------------

def test_names_parse_back():
    name = backup.backup_name(NOW, True)
    assert backup.parse_name(name) == {"created_at": NOW, "include_pdfs": True}
    assert backup.parse_name("LocalBib-Backup_2026-10-09_14-30-12-3_data-only.zip")["include_pdfs"] is False
    assert backup.parse_name("holiday.zip") is None
    assert backup.parse_name("LocalBib-Backup_2026-13-40_99-99-99_data-only.zip") is None


def test_a_name_held_by_a_partial_file_is_taken_too():
    first = backup.backup_name(NOW, False)
    assert backup.backup_name(NOW, False, [first + backup.PARTIAL_SUFFIX]).endswith("-2_data-only.zip")


def test_listing_is_newest_first_and_names_leftover_partials(library):
    dest = library["dest"]
    a = backup.create(str(dest), library["sources"], include_pdfs=False, kind="manual", now=NOW - timedelta(days=2))
    b = backup.create(str(dest), library["sources"], include_pdfs=True, kind="manual", now=NOW)
    (dest / "LocalBib-Backup_2026-10-10_08-00-00_with-pdfs.zip.partial").write_bytes(b"cut off")
    (dest / "other.zip").write_bytes(b"x")
    listing = backup.list_backups(str(dest))
    assert [x["name"] for x in listing["backups"]] == [b["name"], a["name"]]
    assert listing["partial"] == ["LocalBib-Backup_2026-10-10_08-00-00_with-pdfs.zip.partial"]
    assert listing["total_bytes"] == a["bytes"] + b["bytes"]


def test_listing_a_missing_folder_is_empty(tmp_path):
    assert backup.list_backups(str(tmp_path / "nope")) == {"backups": [], "partial": [], "total_bytes": 0}
    assert backup.list_backups("")["backups"] == []


def test_estimate_counts_data_and_pdfs(library):
    est = backup.estimate(library["sources"])
    assert est["pdf_count"] == 2 and est["pdf_bytes"] == 29
    assert est["data_bytes"] >= os.path.getsize(library["db"])


# ---------------------------------------------------------------------------
# The folder rule
# ---------------------------------------------------------------------------

def test_check_folder(tmp_path):
    lib = tmp_path / "Literatur"
    addons = tmp_path / "LocalBib" / "plugins"
    protected = {"library": str(lib), "addons": str(addons)}
    assert backup.check_folder("", protected).code == "error.backup.folderRequired"
    assert backup.check_folder("backups", protected).code == "error.backup.folderNotAbsolute"
    assert backup.check_folder(str(lib), protected).code == "error.backup.insideLibrary"
    assert backup.check_folder(str(lib / "backups"), protected).code == "error.backup.insideLibrary"
    assert backup.check_folder(str(addons / "x"), protected).code == "error.backup.insideAppFolder"
    # A sibling whose name merely starts with the library's is fine.
    assert backup.check_folder(str(tmp_path / "Literatur-Backups"), protected) is None
    assert backup.check_folder(str(tmp_path), protected) is None


def test_clean_path_strips_explorer_quotes():
    assert backup.clean_path('  "D:\\Backups"  ') == "D:\\Backups"
    assert backup.clean_path("'x'") == "x"
    assert backup.clean_path("   ") == ""


# ---------------------------------------------------------------------------
# When an automatic backup is due
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("interval,newest,due", [
    ("off", None, False),
    ("daily", None, True),
    ("daily", NOW.replace(hour=0, minute=1), False),
    ("daily", NOW - timedelta(days=1), True),
    ("daily", NOW.replace(hour=23) - timedelta(days=1), True),
    ("weekly", NOW - timedelta(days=6, hours=23), False),
    ("weekly", NOW - timedelta(days=7), True),
    ("monthly", NOW - timedelta(days=29), False),
    ("monthly", NOW - timedelta(days=30), True),
    ("hourly", None, False),
])
def test_is_due(interval, newest, due):
    assert backup.is_due(interval, newest, NOW) is due


def test_next_due():
    assert backup.next_due("off", None, NOW) is None
    assert backup.next_due("daily", None, NOW) == NOW
    assert backup.next_due("daily", NOW, NOW) == datetime(2026, 10, 10)
    assert backup.next_due("weekly", NOW - timedelta(days=1), NOW) == NOW + timedelta(days=6)


def test_a_backup_with_pdfs_covers_a_data_only_schedule_but_not_the_reverse():
    data = {"created_at": NOW, "include_pdfs": False}
    full = {"created_at": NOW - timedelta(days=3), "include_pdfs": True}
    assert backup.newest_covering([data, full], False) is data
    assert backup.newest_covering([data, full], True) is full
    assert backup.newest_covering([data], True) is None


# ---------------------------------------------------------------------------
# What the Diagnosis says
# ---------------------------------------------------------------------------

def _health(**over):
    args = {"folder_set": True, "interval": "daily", "include_pdfs": False, "backups": [],
            "last_auto": None, "now": NOW}
    args.update(over)
    return backup.health(**args)


def test_health_without_folder_or_without_any_backup_is_none_yet():
    assert _health(folder_set=False) == {"kind": "backup_none"}
    assert _health(interval="off") == {"kind": "backup_none"}
    assert _health(interval="off", backups=[{"created_at": NOW - timedelta(days=400), "include_pdfs": False}]) is None


def test_health_flags_a_schedule_without_a_recent_covering_backup():
    recent = {"created_at": NOW - timedelta(days=1), "include_pdfs": False}
    stale = {"created_at": NOW - timedelta(days=3), "include_pdfs": True}
    assert _health(backups=[recent]) is None
    assert _health(backups=[stale]) == {"kind": "backup_overdue", "days": 2}
    # A recent data-only backup does not cover a schedule with PDFs.
    assert _health(include_pdfs=True, backups=[recent, stale]) == {"kind": "backup_overdue", "days": 2}
    assert _health(interval="weekly", backups=[stale]) is None


def test_health_waits_for_the_first_automatic_run_unless_it_failed():
    assert _health() is None
    assert _health(last_auto={"ok": False, "error": "error.backup.folderUnreachable"}) == {
        "kind": "backup_overdue", "days": 2}


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def test_runner_runs_one_at_a_time_and_keeps_the_outcomes():
    runner = backup.Runner()
    pending = []
    runner.spawn = pending.append
    assert runner.start(lambda progress: {"name": "x.zip"}, kind="manual", include_pdfs=True)
    assert runner.running
    assert not runner.start(lambda progress: {}, kind="auto", include_pdfs=False)
    pending.pop()()
    status = runner.status()
    assert status["run"] is None
    assert status["last"]["ok"] and status["last"]["name"] == "x.zip"
    assert status["last_auto"] is None


def test_runner_records_a_failed_automatic_run():
    runner = backup.Runner()
    runner.spawn = lambda target: target()

    def job(progress):
        progress("pdfs", 1, 4)
        raise backup.BackupError("error.backup.diskFull", needed=10, free=1)
    runner.start(job, kind="auto", include_pdfs=True)
    status = runner.status()
    assert status["last_auto"]["ok"] is False
    assert status["last_auto"]["error"] == "error.backup.diskFull"
    assert status["last_auto"]["params"] == {"needed": 10, "free": 1}
    assert status["last"] == status["last_auto"]


def test_runner_reports_progress_as_a_percentage():
    runner = backup.Runner()
    seen = {}

    def job(progress):
        progress("pdfs", 1, 4)
        seen.update(runner.status()["run"])
        return {"name": "y.zip"}
    runner.spawn = lambda target: target()
    runner.start(job, kind="manual", include_pdfs=True)
    assert seen["phase"] == "pdfs" and seen["percent"] == 25
