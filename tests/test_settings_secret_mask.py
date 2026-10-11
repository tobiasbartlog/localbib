"""SET-B9: OPENALEX_API_KEY never reaches the SPA in plain text.

GET /api/settings sends "" and a hint; a PUT of that hint is "unchanged" and
keeps the key; a new value replaces it; "" removes it.
"""
from __future__ import annotations

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import webapp
from literature_manager import Config

KEY = "oa-1234567890abcd"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import routers.settings as settings_mod

    path = tmp_path / ".env"
    path.write_text(f"OPENALEX_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "_ENV_PATH", path)
    monkeypatch.setattr(Config, "ENV_PATH", str(path))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    # Nothing from the developer's own environment: the key in use is the
    # fixture's .env entry (or what a test puts into the process env itself).
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    Config.reload_from_env()
    yield path
    monkeypatch.undo()
    Config.reload_from_env()


def _stored(path):
    return dotenv_values(path, interpolate=False).get("OPENALEX_API_KEY")


def test_get_sends_a_hint_never_the_key(env):
    with TestClient(webapp.app) as c:
        body = c.get("/api/settings").json()
    assert body["OPENALEX_API_KEY"] == ""
    assert body["OPENALEX_API_KEY_HINT"] == "oa-…abcd"
    assert KEY not in str(body)


def test_putting_the_hint_back_keeps_the_key(env):
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={"OPENALEX_API_KEY": "oa-…abcd"}).status_code == 200
    assert _stored(env) == KEY


def test_a_new_value_replaces_and_empty_removes(env):
    with TestClient(webapp.app) as c:
        c.put("/api/settings", json={"OPENALEX_API_KEY": "  new-key-0000wxyz "})
        assert _stored(env) == "new-key-0000wxyz"
        assert c.get("/api/settings").json()["OPENALEX_API_KEY_HINT"] == "new…wxyz"
        c.put("/api/settings", json={"OPENALEX_API_KEY": ""})
        assert not _stored(env)
        assert c.get("/api/settings").json()["OPENALEX_API_KEY_HINT"] == ""


def test_removing_leaves_no_line_behind(env):
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={"OPENALEX_API_KEY": ""}).status_code == 200
    # The key is deleted, not written back empty (like a cleared numeric key).
    assert "OPENALEX_API_KEY" not in env.read_text(encoding="utf-8")
    assert Config.OPENALEX_API_KEY == ""


def test_a_key_from_the_process_env_counts_as_in_use(env, monkeypatch):
    """A key supplied through the process environment (SET-B5: it wins over
    the .env) is the one in use: GET hints at it, and a PUT of that hint
    changes nothing — neither the .env nor the running value."""
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENALEX_API_KEY", "env-key-0000qrst")
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        body = c.get("/api/settings").json()
        assert body["OPENALEX_API_KEY"] == ""
        assert body["OPENALEX_API_KEY_HINT"] == "env…qrst"
        assert c.put("/api/settings", json={"OPENALEX_API_KEY": "env…qrst"}).status_code == 200
        assert c.get("/api/settings").json()["OPENALEX_API_KEY_HINT"] == "env…qrst"
    assert Config.OPENALEX_API_KEY == "env-key-0000qrst"
    assert "OPENALEX_API_KEY" not in env.read_text(encoding="utf-8")
