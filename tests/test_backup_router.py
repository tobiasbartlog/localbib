"""GET /api/backup, PUT /api/backup/settings, POST /api/backup/run and the
automatic schedule (``routers/backup.auto_tick``)."""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backup
import webapp
from config import Config
from routers import backup as backup_router

KEYS = ("BACKUP_DIR", "AUTO_BACKUP", "AUTO_BACKUP_PDFS")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A .env of its own, the backup keys out of the process environment
    before and after, and backups that run inline."""
    path = tmp_path / "settings" / ".env"
    path.parent.mkdir()
    path.write_text("UI_LANGUAGE=en\n", encoding="utf-8")
    monkeypatch.setattr(backup_router, "_ENV_PATH", path)
    for key in KEYS:
        os.environ.pop(key, None)
    Config.reload_from_env()
    backup.runner.reset()
    monkeypatch.setattr(backup.runner, "spawn", lambda target: target())
    yield path
    for key in KEYS:
        os.environ.pop(key, None)
    Config.reload_from_env()
    backup.runner.reset()


@pytest.fixture
def client(env):
    return TestClient(webapp.app)


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "backups"


@pytest.fixture
def a_pdf():
    path = Path(Config.ALL_DIR) / "backup_router_test.pdf"
    path.write_bytes(b"%PDF-1.4 router test")
    yield path
    path.unlink(missing_ok=True)


def _env_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def test_nothing_is_set_up_by_default(client):
    data = client.get("/api/backup").json()
    assert data["folder"] == ""
    assert data["auto"] == "off" and data["auto_effective"] == "off"
    assert data["auto_pdfs"] is None
    assert data["backups"] == [] and data["count"] == 0
    assert data["run"] is None and data["last"] is None
    assert data["next_auto_at"] is None
    assert set(data["estimate"]) == {"data_bytes", "pdf_bytes", "pdf_count"}
    assert data["folder_state"]["exists"] is False


# ---------------------------------------------------------------------------
# PUT /api/backup/settings
# ---------------------------------------------------------------------------

def test_choosing_a_folder_creates_it_and_writes_the_env(client, env, folder):
    resp = client.put("/api/backup/settings", json={"folder": f'"{folder}"'})
    assert resp.status_code == 200
    assert folder.is_dir()
    assert f"BACKUP_DIR={folder}" in _env_lines(env)
    data = resp.json()
    assert data["folder"] == str(folder)
    assert data["folder_state"]["exists"] is True


@pytest.mark.parametrize("value,code", [
    ("relative\\backups", "error.backup.folderNotAbsolute"),
])
def test_a_bad_folder_is_refused_and_nothing_written(client, env, value, code):
    resp = client.put("/api/backup/settings", json={"folder": value})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert _env_lines(env) == ["UI_LANGUAGE=en"]


def test_a_folder_inside_the_library_is_refused(client, env):
    resp = client.put("/api/backup/settings", json={"folder": os.path.join(Config.BASE_DIR, "backups")})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.backup.insideLibrary"
    assert _env_lines(env) == ["UI_LANGUAGE=en"]


def test_automatic_backups_need_a_folder_and_an_answer_to_the_pdf_question(client, env, folder):
    resp = client.put("/api/backup/settings", json={"auto": "daily"})
    assert resp.json()["detail"]["code"] == "error.backup.autoNeedsFolder"
    client.put("/api/backup/settings", json={"folder": str(folder)})
    resp = client.put("/api/backup/settings", json={"auto": "daily"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.backup.autoNeedsScope"
    resp = client.put("/api/backup/settings", json={"auto_pdfs": False, "auto": "weekly"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto"] == "weekly" and data["auto_effective"] == "weekly" and data["auto_pdfs"] is False
    assert "AUTO_BACKUP=weekly" in _env_lines(env) and "AUTO_BACKUP_PDFS=false" in _env_lines(env)
    assert data["next_auto_at"]  # due now: nothing covers it yet


def test_the_folder_cannot_be_cleared_while_automatic_backups_are_on(client, env, folder):
    client.put("/api/backup/settings", json={"folder": str(folder), "auto_pdfs": True, "auto": "daily"})
    resp = client.put("/api/backup/settings", json={"folder": ""})
    assert resp.json()["detail"]["code"] == "error.backup.autoNeedsFolder"
    client.put("/api/backup/settings", json={"auto": "off"})
    resp = client.put("/api/backup/settings", json={"folder": ""})
    assert resp.status_code == 200
    assert not any(line.startswith(("BACKUP_DIR", "AUTO_BACKUP=")) for line in _env_lines(env))
    assert Config.BACKUP_DIR == "" and Config.AUTO_BACKUP == "off"
    assert folder.is_dir()  # clearing the setting never touches the folder


def test_an_unknown_interval_is_refused(client):
    resp = client.put("/api/backup/settings", json={"auto": "hourly"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.settings.invalidChoice"


# ---------------------------------------------------------------------------
# POST /api/backup/run
# ---------------------------------------------------------------------------

def test_a_run_must_say_whether_the_pdfs_go_along(client, folder):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    assert client.post("/api/backup/run", json={}).status_code == 422


def test_a_run_without_a_folder_is_refused(client):
    resp = client.post("/api/backup/run", json={"include_pdfs": False})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.backup.folderRequired"


def test_a_run_with_an_unplugged_folder_is_refused(client, folder):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    folder.rmdir()
    resp = client.post("/api/backup/run", json={"include_pdfs": False})
    assert resp.json()["detail"]["code"] == "error.backup.folderUnreachable"


def test_a_data_only_run_writes_one_backup_with_the_settings_and_the_restore_steps(client, env, folder, a_pdf):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    resp = client.post("/api/backup/run", json={"include_pdfs": False})
    assert resp.status_code == 202
    data = client.get("/api/backup").json()
    assert data["count"] == 1 and data["backups"][0]["include_pdfs"] is False
    assert data["last"]["ok"] is True and data["last"]["kind"] == "manual"
    with zipfile.ZipFile(folder / data["backups"][0]["name"]) as zf:
        names = zf.namelist()
        assert "literatur.db" in names and "settings/.env" in names
        assert not any(n.startswith("pdfs/") for n in names)
        assert b"BACKUP_DIR=" in zf.read("settings/.env")
        readme = zf.read("README.txt").decode("utf-8")
        manifest = json.loads(zf.read("manifest.json"))
    assert "⟦" not in readme  # every restore step has its catalog text
    assert readme.startswith("How to restore a backup")
    assert manifest["library_dir"] == Config.BASE_DIR


def test_a_run_with_pdfs_holds_the_library_files(client, folder, a_pdf):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    client.post("/api/backup/run", json={"include_pdfs": True})
    data = client.get("/api/backup").json()
    assert data["backups"][0]["include_pdfs"] is True
    with zipfile.ZipFile(folder / data["backups"][0]["name"]) as zf:
        assert zf.read("pdfs/" + a_pdf.name) == a_pdf.read_bytes()


def test_a_second_run_while_one_is_under_way_is_refused(client, folder, monkeypatch):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    monkeypatch.setattr(backup.runner, "spawn", lambda target: None)  # never finishes
    assert client.post("/api/backup/run", json={"include_pdfs": False}).status_code == 202
    assert client.get("/api/backup").json()["run"]["kind"] == "manual"
    resp = client.post("/api/backup/run", json={"include_pdfs": True})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "error.backup.running"


def test_a_failed_run_is_reported_with_its_reason(client, folder, monkeypatch):
    client.put("/api/backup/settings", json={"folder": str(folder)})

    class Usage:
        free = 0
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda path: Usage)
    assert client.post("/api/backup/run", json={"include_pdfs": True}).status_code == 202
    last = client.get("/api/backup").json()["last"]
    assert last["ok"] is False and last["error"] == "error.backup.diskFull"


# ---------------------------------------------------------------------------
# The automatic schedule
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 9, 9, 0, 0)


def _auto(client, folder, interval="daily", pdfs=False):
    resp = client.put("/api/backup/settings", json={"folder": str(folder), "auto_pdfs": pdfs, "auto": interval})
    assert resp.status_code == 200


def test_no_schedule_no_backup(client):
    assert backup_router.auto_tick(NOW) == "off"


def test_a_due_backup_starts_and_the_next_look_finds_it_done(client, folder):
    _auto(client, folder)
    assert backup_router.auto_tick(NOW) == "started"
    listing = backup.list_backups(str(folder))["backups"]
    assert len(listing) == 1 and listing[0]["include_pdfs"] is False
    assert backup.runner.status()["last_auto"]["ok"] is True
    later_today = datetime.now() + timedelta(minutes=15)
    if later_today.date() == datetime.now().date():
        assert backup_router.auto_tick(later_today) == "not_due"
    assert backup_router.auto_tick(datetime.now() + timedelta(days=1)) == "started"
    assert len(backup.list_backups(str(folder))["backups"]) == 2


def test_a_manual_backup_with_pdfs_covers_a_data_only_schedule(client, folder):
    client.put("/api/backup/settings", json={"folder": str(folder)})
    client.post("/api/backup/run", json={"include_pdfs": True})
    _auto(client, folder, interval="weekly", pdfs=False)
    assert backup_router.auto_tick(datetime.now() + timedelta(hours=1)) == "not_due"


def test_an_unplugged_folder_fails_then_waits_then_tries_again(client, folder):
    _auto(client, folder)
    folder.rmdir()
    assert backup_router.auto_tick(NOW) == "failed"
    status = client.get("/api/backup").json()
    assert status["auto_problem"]["error"] == "error.backup.folderUnreachable"
    assert backup_router.auto_tick(datetime.now() + timedelta(minutes=30)) == "waiting"
    folder.mkdir()
    assert backup_router.auto_tick(datetime.now() + timedelta(hours=2)) == "started"
    assert client.get("/api/backup").json()["auto_problem"] is None


def test_the_schedule_does_not_start_while_a_run_is_under_way(client, folder, monkeypatch):
    _auto(client, folder)
    monkeypatch.setattr(backup.runner, "spawn", lambda target: None)
    client.post("/api/backup/run", json={"include_pdfs": False})
    assert backup_router.auto_tick(NOW) == "busy"


# ---------------------------------------------------------------------------
# The Diagnosis reads the same state
# ---------------------------------------------------------------------------

def _backup_findings(client):
    findings = client.get("/api/diagnosis", params={"llm": "false"}).json()["findings"]
    return [(f["kind"], f["severity"], f["params"], f["fixes"]) for f in findings if f["kind"].startswith("backup_")]


def test_the_diagnosis_notes_a_missing_backup_until_there_is_one(client, folder):
    assert _backup_findings(client) == [("backup_none", "info", {}, ["open_backup_settings"])]
    client.put("/api/backup/settings", json={"folder": str(folder)})
    assert _backup_findings(client) == [("backup_none", "info", {}, ["open_backup_settings"])]
    client.post("/api/backup/run", json={"include_pdfs": False})
    assert _backup_findings(client) == []


def test_the_diagnosis_warns_when_an_automatic_backup_fails(client, folder):
    _auto(client, folder)
    assert _backup_findings(client) == []  # the first run is still to come
    folder.rmdir()
    backup_router.auto_tick(NOW)
    assert _backup_findings(client) == [("backup_overdue", "warning", {"days": 2}, ["open_backup_settings"])]


def test_the_schedule_is_off_in_the_test_suite():
    assert backup_router.AUTO_ON_STARTUP is False
    assert backup_router.start_schedule() is None
