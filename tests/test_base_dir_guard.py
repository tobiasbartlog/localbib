"""Startup guard for an already-mangled base folder in ``.env``.

Older versions wrote a quoted base folder raw into ``.env``; dotenv then reads
``\\t`` inside double quotes back as a TAB and the console-less .exe died at
import. A value with a control character, or one that is not absolute after
expansion, now falls back to ``~/Literatur`` and the Startup check reports it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import webapp
from config import Config

REPO = Path(__file__).resolve().parent.parent


def _default():
    return os.path.expanduser(os.path.join("~", "Literatur"))


@pytest.mark.parametrize("raw, reason", [
    ("C:\\Users\\tim\\Bib\tx", "control"),
    ("/data/lit\x00", "control"),
    ("/data/new\nline", "control"),
    ("relative/dir", "relative"),
    ("Literatur", "relative"),
])
def test_an_unusable_value_falls_back_to_the_default(raw, reason):
    path, notice = Config.resolve_base_dir(raw)
    assert path == _default()
    assert notice["code"] == "baseDirIgnored"
    assert notice["params"]["reason"] == reason
    assert notice["params"]["used"] == _default()
    # The shown value names the control character instead of carrying it.
    assert not any(ord(c) < 0x20 for c in notice["params"]["value"])


def test_a_tab_is_shown_as_an_escape():
    _path, notice = Config.resolve_base_dir("/x\tb")
    assert notice["params"]["value"] == "/x\\x09b"


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_means_the_default_without_a_notice(raw):
    assert Config.resolve_base_dir(raw) == (_default(), None)


def test_an_absolute_value_is_used_as_is(tmp_path):
    assert Config.resolve_base_dir(f"  {tmp_path}  ") == (str(tmp_path), None)


def test_home_is_expanded():
    path, notice = Config.resolve_base_dir(os.path.join("~", "Bib"))
    assert notice is None and path == os.path.expanduser(os.path.join("~", "Bib"))


def test_the_startup_check_carries_the_notice(monkeypatch):
    notice = {"code": "baseDirIgnored",
              "params": {"value": "x", "reason": "relative", "used": "/home/u/Literatur"}}
    monkeypatch.setattr(Config, "STARTUP_NOTICES", [notice])
    with TestClient(webapp.app) as c:
        body = c.get("/api/llm/startup-check").json()
    assert body["notices"] == [notice]


def test_the_startup_check_has_no_notice_normally(monkeypatch):
    monkeypatch.setattr(Config, "STARTUP_NOTICES", [])
    with TestClient(webapp.app) as c:
        assert c.get("/api/llm/startup-check").json()["notices"] == []


def test_the_app_imports_with_a_mangled_value(tmp_path):
    """The real boot: webapp imports, uses the default and records the notice."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.update({"HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                "LITERATUR_BASE_DIR": "/x\\Bib\tx"})
    code = ("import webapp; from config import Config; "
            "print(Config.BASE_DIR); print(Config.STARTUP_NOTICES[0]['params']['reason'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = out.stdout.strip().splitlines()
    assert lines[-2] == os.path.join(str(tmp_path), "Literatur")
    assert lines[-1] == "control"
    assert "ignored" in out.stderr
