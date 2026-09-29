"""PUT /api/settings must never write a value that can crash a later
``Config.reload_from_env()`` (which runs at import AND after every settings
save, so a bad ``.env`` doesn't just break the current request — it takes
down the *next* app start too).

Two lines of defence, both covered here:
- ``routers/settings.py`` rejects an invalid non-empty numeric value with a
  422 before it ever reaches the ``.env`` (an empty value is accepted and
  means "reset to default").
- ``config.py``'s ``_env_int`` stays tolerant regardless, for a bad value
  that got into the ``.env`` some other way (hand edit, an older build,
  cross-process race).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webapp
from literature_manager import Config


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    import routers.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(Config, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    for key in ("WATCH_INTERVAL", "MAX_OCR_PAGES"):
        monkeypatch.setenv(key, "")
    Config.reload_from_env()
    yield tmp_path
    monkeypatch.undo()
    Config.reload_from_env()


@pytest.mark.parametrize("key", ["WATCH_INTERVAL", "MAX_OCR_PAGES"])
def test_invalid_non_empty_number_is_rejected(temp_env, key):
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={key: "abc"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail == {"code": "error.settings.invalidNumber", "params": {"key": key}}
    # Nothing was written — a rejected value never reaches the .env.
    assert not (temp_env / ".env").exists() or key not in (temp_env / ".env").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["WATCH_INTERVAL", "MAX_OCR_PAGES"])
def test_clearing_the_field_resets_to_the_default(temp_env, key):
    with TestClient(webapp.app) as c:
        # First set a real value …
        assert c.put("/api/settings", json={key: "42"}).status_code == 200
        assert getattr(Config, key) == 42
        # … then clear it: this must succeed, not 500, and fall back to the
        # built-in default rather than leaving an unparsable "" behind.
        resp = c.put("/api/settings", json={key: ""})
    assert resp.status_code == 200
    assert getattr(Config, key) == 5
    stored = c.get("/api/settings").json()
    assert stored.get(key, "") == ""


@pytest.mark.parametrize("key", ["WATCH_INTERVAL", "MAX_OCR_PAGES"])
def test_valid_number_is_stored_and_applied(temp_env, key):
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={key: "17"}).status_code == 200
        stored = c.get("/api/settings").json()
    assert stored[key] == "17"
    assert getattr(Config, key) == 17


@pytest.mark.parametrize("key", ["WATCH_INTERVAL", "MAX_OCR_PAGES"])
def test_reload_from_env_tolerates_a_corrupted_env_value(temp_env, key, monkeypatch):
    """Regression for the crash itself: a bad value already sitting in the
    environment (however it got there) must not raise out of
    ``reload_from_env`` — every app start, and every settings save afterwards
    by any other router, calls it."""
    monkeypatch.setenv(key, "not-a-number")
    Config.reload_from_env()  # must not raise
    assert getattr(Config, key) == 5


def test_reload_from_env_tolerates_a_negative_env_value(temp_env, monkeypatch):
    monkeypatch.setenv("WATCH_INTERVAL", "-3")
    Config.reload_from_env()
    assert Config.WATCH_INTERVAL == 5
