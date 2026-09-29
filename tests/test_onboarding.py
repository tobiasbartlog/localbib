"""First-run onboarding (#140) at the HTTP seam.

Onboarding writes nothing of its own: the scalar answers (language, mailto,
"we asked") go through the settings endpoint, the chosen provider through the
LLM configuration endpoint as one connection ``default`` bound to all three
roles. Each test redirects ``.env`` and ``llm.json`` to a temp dir so the
developer's real files are untouched.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import llm_config
import webapp
from literature_manager import Config


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    import routers.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(Config, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    for key in llm_config.LEGACY_ENV_KEYS + ("CROSSREF_MAILTO", "ONBOARDING_COMPLETED"):
        monkeypatch.setenv(key, "")
    Config.reload_from_env()
    yield tmp_path
    monkeypatch.undo()
    Config.reload_from_env()


def _default_connection(**overrides):
    conn = {"id": "default", "label": "OpenAI", "provider": "openai", "base_url": "", "api_key": "sk-user-key"}
    conn.update(overrides)
    bind = {"connection_id": "default", "model": ""}
    return {"connections": [conn], "roles": {"reasoning": bind, "fast": bind, "embedding": bind}}


def test_onboarding_answers_persist_and_take_effect(temp_env):
    """Mailto + provider survive the round-trip and configure the app."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={
            "CROSSREF_MAILTO": "me@uni.example",
            "ONBOARDING_COMPLETED": "true",
        }).status_code == 200
        resp = c.put("/api/llm/config", json=_default_connection())
        assert resp.status_code == 200, resp.text

        stored = c.get("/api/settings").json()
        config = c.get("/api/llm/config").json()

    assert stored["CROSSREF_MAILTO"] == "me@uni.example"
    assert stored["ONBOARDING_COMPLETED"] == "true"
    # The provider lives in llm.json, never in the .env.
    assert "LLM_API_KEY" not in stored
    assert "sk-user-key" not in (temp_env / ".env").read_text(encoding="utf-8")
    assert json.loads((temp_env / "llm.json").read_text(encoding="utf-8"))["connections"][0]["api_key"] == "sk-user-key"
    assert config["connections"][0]["key_hint"] == "sk-…-key"
    assert config["status"]["connections"] == 1
    # And the running app uses them right away — no restart needed.
    assert Config.polite_mailto() == "me@uni.example"
    assert Config.llm_endpoint("reasoning") is None          # no model chosen yet …
    assert Config.LLM_DOCUMENT["connections"][0]["provider"] == "openai"  # … but the connection is live


def test_keyless_local_provider_counts_as_configured(temp_env):
    """Ollama has no key. That must not read as "nothing configured"."""
    with TestClient(webapp.app) as c:
        resp = c.put("/api/llm/config", json=_default_connection(
            provider="custom", base_url="http://localhost:11434/v1", api_key="",
        ))
        assert resp.status_code == 200, resp.text
        status = c.get("/api/llm/status").json()
    assert status["connections"] == 1


def test_skipping_onboarding_leaves_the_app_usable_without_llm(temp_env):
    """Skip = remember that we asked, configure nothing, keep LLM features off."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={"ONBOARDING_COMPLETED": "true"}).status_code == 200
        stored = c.get("/api/settings").json()
        status = c.get("/api/llm/status").json()
        # The app itself keeps working — an unrelated read-only endpoint answers.
        assert c.get("/api/stats").status_code == 200

    assert stored["ONBOARDING_COMPLETED"] == "true"
    assert status == {"reasoning": False, "fast": False, "embedding": False, "connections": 0}
    # Every LLM call site gates on readiness; off means "feature off", not "crash".
    assert Config.llm_ready("reasoning") is False
    assert Config.llm_status()["connections"] == 0


def test_saving_an_empty_mailto_sends_no_polite_header(temp_env):
    """Onboarding's mailto field is optional: empty means nothing is sent."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={
            "CROSSREF_MAILTO": "",
            "ONBOARDING_COMPLETED": "true",
        }).status_code == 200

    assert Config.polite_mailto() == ""
    assert "mailto" not in Config.user_agent().lower()


def test_fresh_install_reports_onboarding_pending(temp_env):
    """Nothing configured yet -> the SPA knows to show the dialog."""
    with TestClient(webapp.app) as c:
        stored = c.get("/api/settings").json()
        status = c.get("/api/llm/status").json()

    assert stored.get("ONBOARDING_COMPLETED", "") != "true"
    assert status["connections"] == 0


def test_legacy_llm_keys_are_no_longer_writable_settings(temp_env):
    """PUT /api/settings ignores the old flat keys: the document is the one
    truth, and a stale writer must not resurrect a second one."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={"LLM_API_KEY": "sk-x", "LLM_MODEL": "m"}).status_code == 200
    assert "LLM_" not in (temp_env / ".env").read_text(encoding="utf-8")
