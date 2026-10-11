"""Dark mode and the thesis area are server settings (#208), and the custom
icon feature is gone: its three endpoints no longer exist."""
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
    Config.reload_from_env()
    yield tmp_path
    monkeypatch.undo()
    Config.reload_from_env()


@pytest.mark.parametrize("key", ["DARK_MODE", "THESIS_MODE"])
def test_the_key_is_stored_and_read_back(temp_env, key):
    with TestClient(webapp.app) as c:
        assert key not in c.get("/api/settings").json()  # "no value yet" stays visible
        assert c.put("/api/settings", json={key: "true"}).status_code == 200
        assert c.get("/api/settings").json()[key] == "true"
        assert c.put("/api/settings", json={key: "false"}).status_code == 200
        assert c.get("/api/settings").json()[key] == "false"
    assert f"{key}=false" in (temp_env / ".env").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["DARK_MODE", "THESIS_MODE"])
def test_anything_but_true_or_false_is_refused_and_not_written(temp_env, key):
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={key: "maybe"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "error.settings.invalidBoolean", "params": {"key": key}}
    assert not (temp_env / ".env").exists() or key not in (temp_env / ".env").read_text(encoding="utf-8")


def test_the_page_carries_the_server_values_for_the_first_paint(temp_env):
    with TestClient(webapp.app) as c:
        assert '"darkMode": ""' in c.get("/").text  # nothing held yet
        c.put("/api/settings", json={"DARK_MODE": "true", "THESIS_MODE": "false"})
        html = c.get("/").text
    assert '"darkMode": "true"' in html and '"thesisMode": "false"' in html


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_the_icon_endpoints_are_gone(temp_env, method):
    with TestClient(webapp.app) as c:
        resp = getattr(c, method)("/api/appearance/icon")
    assert resp.status_code in (404, 405)
    assert not any("/api/appearance" in getattr(r, "path", "") for r in webapp.app.routes)
