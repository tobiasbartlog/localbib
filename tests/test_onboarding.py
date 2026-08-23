"""First-run onboarding (#140) at the settings HTTP seam.

Onboarding writes nothing of its own: it uses the existing settings endpoints,
so "the answers survive a restart" is the same thing as "GET /api/settings
returns them from the .env file". Each test redirects _ENV_PATH to a temp file
so the developer's real .env is untouched (the pattern from
the plugin test suites).
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
    for key in ("LLM_PROVIDER", "LLM_API_KEY", "KICONNECT_API_KEY", "LLM_MODEL",
                "CROSSREF_MAILTO", "ONBOARDING_COMPLETED"):
        monkeypatch.delenv(key, raising=False)
    Config.reload_from_env()
    yield tmp_path / ".env"
    monkeypatch.undo()
    Config.reload_from_env()


def test_onboarding_answers_persist_and_take_effect(temp_env):
    """Provider, key and mailto survive the round-trip and configure the app."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "sk-user-key",
            "CROSSREF_MAILTO": "me@uni.example",
            "ONBOARDING_COMPLETED": "true",
        }).status_code == 200

        stored = c.get("/api/settings").json()

    assert stored["LLM_PROVIDER"] == "openai"
    assert stored["LLM_API_KEY"] == "sk-user-key"
    assert stored["CROSSREF_MAILTO"] == "me@uni.example"
    assert stored["ONBOARDING_COMPLETED"] == "true"
    # And the running app uses them right away — no restart needed.
    assert Config.LLM_CHAT_URL == "https://api.openai.com/v1/chat/completions"
    assert Config.polite_mailto() == "me@uni.example"


def test_skipping_onboarding_leaves_the_app_usable_without_llm(temp_env):
    """Skip = remember that we asked, configure nothing, keep LLM features off."""
    with TestClient(webapp.app) as c:
        assert c.put("/api/settings", json={"ONBOARDING_COMPLETED": "true"}).status_code == 200
        stored = c.get("/api/settings").json()
        # The app itself keeps working — an unrelated read-only endpoint answers.
        assert c.get("/api/stats").status_code == 200

    assert stored["ONBOARDING_COMPLETED"] == "true"
    # Every LLM call site gates on this key; empty means "feature off", not "crash".
    assert Config.KICONNECT_API_KEY == ""
    assert Config.LLM_CHAT_URL == ""


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

    assert stored.get("ONBOARDING_COMPLETED", "") != "true"
    assert stored.get("LLM_API_KEY", "") == ""
