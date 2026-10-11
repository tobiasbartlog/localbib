"""Start with Windows (#214, ADR-0023): the HKCU Run entry is the one truth.

The rules (``autostart.py``) and ``GET``/``PUT /api/system/autostart`` run
against a dict-backed registry - no test touches the user's real one.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

import autostart
import webapp
from routers import version as version_router

EXE = r"C:\Users\me\AppData\Local\Programs\LocalBib\LocalBib.exe"
DISABLED = b"\x03" + bytes(11)  # what Task Manager writes for "Disabled"
ENABLED = b"\x02" + bytes(11)   # ... and for "Enabled" again


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict = {}

    def read(self, key, name):
        return self.values.get((key, name))

    def write_string(self, key, name, value):
        self.values[(key, name)] = value

    def delete(self, key, name):
        self.values.pop((key, name), None)

    def run_value(self):
        return self.values.get((autostart.RUN_KEY, autostart.VALUE_NAME))

    def approved(self, data):
        self.values[(autostart.APPROVED_KEY, autostart.VALUE_NAME)] = data


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def test_off_without_a_run_value():
    assert autostart.is_enabled(FakeRegistry()) is False


def test_enable_writes_the_quoted_exe_with_the_autostart_argument():
    reg = FakeRegistry()
    autostart.enable(reg, EXE)
    assert reg.run_value() == f'"{EXE}" --autostart'
    assert autostart.is_enabled(reg) is True


@pytest.mark.parametrize("data,enabled", [(DISABLED, False), (b"\x07" + bytes(11), False),
                                          (ENABLED, True), (b"\x06" + bytes(11), True), (b"", True)])
def test_task_manager_decides_through_startup_approved(data, enabled):
    reg = FakeRegistry()
    autostart.enable(reg, EXE)
    reg.approved(data)
    assert autostart.is_enabled(reg) is enabled


def test_switching_on_lifts_a_task_manager_disable():
    reg = FakeRegistry()
    autostart.enable(reg, EXE)
    reg.approved(DISABLED)
    assert autostart.set_enabled(reg, True, EXE) is True
    assert reg.read(autostart.APPROVED_KEY, autostart.VALUE_NAME) is None


def test_switching_off_removes_the_value_and_any_record():
    reg = FakeRegistry()
    autostart.enable(reg, EXE)
    reg.approved(DISABLED)
    assert autostart.set_enabled(reg, False, EXE) is False
    assert reg.values == {}


@pytest.mark.parametrize("frozen,platform,available", [
    (True, "win32", True), (False, "win32", False), (True, "linux", False), (True, "darwin", False),
])
def test_only_the_windows_exe_offers_it(frozen, platform, available):
    assert autostart.available(frozen, platform) is available


# ---------------------------------------------------------------------------
# GET / PUT /api/system/autostart
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(webapp.app)


@pytest.fixture
def exe(monkeypatch):
    """This process as the Windows .exe, with a fake registry."""
    reg = FakeRegistry()
    monkeypatch.setattr(version_router, "_autostart_available", lambda: True)
    monkeypatch.setattr(version_router, "_autostart_registry", lambda: reg)
    return reg


def test_get_reports_off_and_put_switches_on(client, exe):
    assert client.get("/api/system/autostart").json() == {"available": True, "enabled": False}

    resp = client.put("/api/system/autostart", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "enabled": True}
    assert exe.run_value() == f'"{sys.executable}" --autostart'
    assert client.get("/api/system/autostart").json()["enabled"] is True


def test_put_off_removes_the_entry(client, exe):
    autostart.enable(exe, EXE)
    resp = client.put("/api/system/autostart", json={"enabled": False})
    assert resp.json() == {"available": True, "enabled": False}
    assert exe.run_value() is None


def test_disabled_in_task_manager_reads_as_off_and_put_on_lifts_it(client, exe):
    autostart.enable(exe, EXE)
    exe.approved(DISABLED)
    assert client.get("/api/system/autostart").json() == {"available": True, "enabled": False}
    assert client.put("/api/system/autostart", json={"enabled": True}).json()["enabled"] is True
    assert exe.read(autostart.APPROVED_KEY, autostart.VALUE_NAME) is None


def test_a_registry_that_refuses_is_a_500_with_a_code(client, exe, monkeypatch):
    def refuse(*_args):
        raise PermissionError("access denied")
    monkeypatch.setattr(exe, "write_string", refuse)
    resp = client.put("/api/system/autostart", json={"enabled": True})
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "error.autostart_failed"


def test_outside_the_exe_it_is_unavailable_and_put_is_409(client, monkeypatch):
    monkeypatch.setattr(version_router, "_running_frozen", lambda: False)

    def no_registry():
        raise AssertionError("the registry must not be touched outside the .exe")
    monkeypatch.setattr(version_router, "_autostart_registry", no_registry)

    assert client.get("/api/system/autostart").json() == {"available": False, "enabled": False}
    resp = client.put("/api/system/autostart", json={"enabled": True})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.autostart_unavailable"
    assert client.get("/api/system/info").json()["autostart_available"] is False


def test_system_info_reports_the_availability(client, exe):
    info = client.get("/api/system/info").json()
    assert info["autostart_available"] is True
    assert info["app"] == "LocalBib"


# ---------------------------------------------------------------------------
# Leaving through os._exit takes the tray icon along
# ---------------------------------------------------------------------------

def test_exit_app_runs_the_exit_hooks_first(monkeypatch):
    order: list = []
    monkeypatch.setattr(version_router, "_exit_hooks", [])
    monkeypatch.setattr(version_router.os, "_exit", lambda code: order.append(("exit", code)))
    version_router.add_exit_hook(lambda: order.append("icon removed"))
    version_router.add_exit_hook(lambda: 1 / 0)  # a failing hook never blocks the exit
    version_router._exit_app()
    assert order == ["icon removed", ("exit", 0)]
