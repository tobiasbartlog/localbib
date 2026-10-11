"""The ``.env`` writer must write what python-dotenv reads back (SET-B2), the
base-dir write site must refuse unusable paths (SET-B2/B10), and the reload
must not override the real process environment (SET-B5)."""
from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import settings_store
import webapp
from literature_manager import Config

NASTY = [
    "C:\\Users\\tim\\Literatur",
    "C:\\Users\\tim\\Literatur\\",
    "D:\\Bib #2",
    "tab\\there \\n newline \\\\ double",
    'say "hi"',
    "it's",
    "back\\'slash-quote",
    "${X}/y $Z",
    "  padded  ",
    "a=b",
    "x # y 'z' \"w\" \\t",
    "",
]


@pytest.mark.parametrize("value", NASTY)
def test_write_env_round_trips_through_dotenv(tmp_path, value):
    env = tmp_path / ".env"
    settings_store.write_env(env, {"KEY": value, "OTHER": "plain"})
    assert dotenv_values(env, interpolate=False)["KEY"] == value
    assert dotenv_values(env, interpolate=False)["OTHER"] == "plain"
    assert settings_store.read_env(env, {"KEY"}) == {"KEY": value}
    # Rewriting the same key in place keeps it intact too.
    settings_store.write_env(env, {"KEY": value})
    assert dotenv_values(env, interpolate=False)["KEY"] == value


def test_embedded_newline_cannot_corrupt_the_file(tmp_path):
    env = tmp_path / ".env"
    settings_store.write_env(env, {"A": "x\ny=z", "B": "ok"})
    values = dotenv_values(env, interpolate=False)
    assert set(values) == {"A", "B"}
    assert values["B"] == "ok"


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    import routers.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(Config, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    yield tmp_path
    monkeypatch.undo()
    Config.reload_from_env()


def test_quoted_pasted_path_is_stored_clean(temp_env):
    # A Windows folder like ``D:\Bib\tx``: inside raw double quotes dotenv
    # would read the backslash-t back as a TAB. Spelled out with literal
    # backslashes so the test bites on every OS, not only on Windows.
    target = str(temp_env) + "\\Literatur\\tx"
    pasted = f'  "{target}"  '
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={"LITERATUR_BASE_DIR": pasted})
    assert resp.status_code == 200
    assert dotenv_values(temp_env / ".env", interpolate=False)["LITERATUR_BASE_DIR"] == target


@pytest.mark.parametrize("bad", ["", "   ", '""', "relative/dir", "Literatur"])
def test_base_dir_must_be_absolute(temp_env, bad):
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={"LITERATUR_BASE_DIR": bad})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.settings.baseDirNotAbsolute"
    assert not (temp_env / ".env").exists()


def test_reload_does_not_override_the_real_process_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PROC_KEY=from_file\nFILE_KEY=from_file\n", encoding="utf-8")
    monkeypatch.setenv("PROC_KEY", "from_process")
    monkeypatch.delenv("FILE_KEY", raising=False)
    monkeypatch.setattr(settings_store, "_process_env_keys", {"PROC_KEY"})
    settings_store._load_env_file(env)
    assert os.environ["PROC_KEY"] == "from_process"
    assert os.environ["FILE_KEY"] == "from_file"
    # A key the user saves explicitly stops being protected.
    settings_store.write_env(env, {"PROC_KEY": "saved"})
    settings_store._load_env_file(env)
    assert os.environ["PROC_KEY"] == "saved"
    monkeypatch.delenv("FILE_KEY", raising=False)
