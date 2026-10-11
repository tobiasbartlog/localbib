"""GET /api/system/info and POST /api/system/open-folder (#211)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import webapp
from config import Config
from routers import version as version_router


@pytest.fixture
def folders(tmp_path, monkeypatch):
    base = tmp_path / "lib"
    settings = tmp_path / "settings"
    logs = settings / "logs"
    for d in (base, settings, logs):
        d.mkdir(parents=True)
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(Config, "BASE_DIR", str(base))
    monkeypatch.setattr(Config, "ENV_PATH", str(settings / ".env"))
    monkeypatch.setattr(Config, "BACKUP_DIR", str(backups))
    opened: list[str] = []
    monkeypatch.setattr(version_router, "_open_in_file_manager", opened.append)
    return {"base": base, "settings": settings, "logs": logs, "backups": backups, "opened": opened}


@pytest.fixture
def client():
    return TestClient(webapp.app)


def test_system_info_reports_version_and_the_named_folders(client, folders, monkeypatch):
    monkeypatch.setattr(version_router, "_running_frozen", lambda: True)
    # Availability follows the Windows .exe check (#214, tests/test_autostart.py).
    monkeypatch.setattr(version_router, "_autostart_available", lambda: False)
    data = client.get("/api/system/info").json()
    assert data["app"] == "LocalBib"
    assert data["version"] == version_router.APP_VERSION
    assert data["is_frozen"] is True
    assert data["folders"] == {
        "library": str(folders["base"]),
        "settings": str(folders["settings"]),
        "logs": str(folders["logs"]),
        "backups": str(folders["backups"]),
    }
    assert data["autostart_available"] is False


@pytest.mark.parametrize("name,key", [("library", "base"), ("settings", "settings"), ("logs", "logs"),
                                      ("backups", "backups")])
def test_open_folder_opens_the_resolved_path(client, folders, name, key):
    resp = client.post("/api/system/open-folder", json={"folder": name})
    assert resp.status_code == 200
    assert folders["opened"] == [str(folders[key])]


@pytest.mark.parametrize("body", [
    {"folder": "C:\\Windows"}, {"folder": "../etc"}, {"folder": ""}, {"folder": "all"}, {},
])
def test_open_folder_never_takes_a_free_path(client, folders, body):
    resp = client.post("/api/system/open-folder", json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "error.unknown_folder"
    assert folders["opened"] == []


def test_open_folder_missing_is_404(client, folders):
    os.rmdir(folders["logs"])
    resp = client.post("/api/system/open-folder", json={"folder": "logs"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.folder_missing"
    assert folders["opened"] == []


def test_open_backups_without_a_chosen_folder_is_404(client, folders, monkeypatch):
    monkeypatch.setattr(Config, "BACKUP_DIR", "")
    resp = client.post("/api/system/open-folder", json={"folder": "backups"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.folder_missing"
    assert folders["opened"] == []


def test_open_failure_is_reported_not_raised(client, folders, monkeypatch):
    def boom(path):
        raise OSError("no file manager")
    monkeypatch.setattr(version_router, "_open_in_file_manager", boom)
    resp = client.post("/api/system/open-folder", json={"folder": "library"})
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "error.folder_open_failed"
