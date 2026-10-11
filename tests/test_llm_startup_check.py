"""Startup check of the AI models (#209).

At every start LocalBib asks the connection of each bound role for its model
list — no chat, no embedding, no tokens — and keeps one status per role:
``GET /api/llm/startup-check`` reads it, ``POST`` checks again and replaces
it. The classification is pure (``services/llm_check.py``); the request is
``llm_client.fetch_model_list`` (httpx), faked here with respx. A request
respx does not know fails the test, so "only model lists" is checked by
construction.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import routers.llm as llm_router
import webapp
from llm_client import TASK_MODELS
from services import llm_check
from tests.llm_helpers import TEST_BASE_URL, configure_llm, llm_off

MODELS_URL = f"{TEST_BASE_URL}/models"
OLLAMA = "http://localhost:11434/v1"


def _list(*ids):
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


@pytest.fixture(autouse=True)
def _fresh_check_state():
    """The result lives in the router module; every test starts without one."""
    llm_router._check_state.update(running=False, checked_at=None, roles={})
    yield
    llm_router._check_state.update(running=False, checked_at=None, roles={})


def _check(client) -> dict:
    resp = client.post("/api/llm/startup-check")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# The API, one status at a time
# ---------------------------------------------------------------------------

@respx.mock
def test_ok_names_connection_and_model(client, monkeypatch):
    configure_llm(monkeypatch, reasoning="gpt-4.1", fast="gpt-mini")
    route = respx.get(MODELS_URL).mock(return_value=_list("gpt-4.1", "gpt-mini"))
    body = _check(client)
    assert body["running"] is False
    assert body["checked_at"]
    reasoning = body["roles"]["reasoning"]
    assert {k: reasoning[k] for k in ("status", "connection_label", "model", "detail_code", "fallback")} == {
        "status": "ok", "connection_label": "Test", "model": "gpt-4.1",
        "detail_code": "offered", "fallback": False,
    }
    assert body["roles"]["fast"]["status"] == "ok"
    # One connection, one request — however many roles it serves.
    assert route.call_count == 1
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"


@respx.mock
def test_model_no_longer_offered(client, monkeypatch):
    configure_llm(monkeypatch, reasoning="gpt-4o")
    respx.get(MODELS_URL).mock(return_value=_list("gpt-4.1", "gpt-4.1-mini"))
    role = _check(client)["roles"]["reasoning"]
    assert (role["status"], role["detail_code"], role["model"]) == ("model_missing", "not_offered", "gpt-4o")


@respx.mock
def test_rejected_key(client, monkeypatch):
    configure_llm(monkeypatch)
    respx.get(MODELS_URL).mock(return_value=httpx.Response(401, json={"error": "invalid key"}))
    role = _check(client)["roles"]["reasoning"]
    assert (role["status"], role["detail_code"], role["http_status"]) == ("auth_failed", "key_rejected", 401)


@respx.mock
def test_timeout_is_unreachable(client, monkeypatch):
    configure_llm(monkeypatch)
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectTimeout("no answer"))
    role = _check(client)["roles"]["reasoning"]
    assert (role["status"], role["detail_code"], role["host"]) == ("unreachable", "timeout", "llm.test")


@respx.mock
def test_refused_connection_and_server_error_are_unreachable(client, monkeypatch):
    configure_llm(monkeypatch, embedding="nomic", embed_base_url=OLLAMA, embed_api_key="")
    respx.get(MODELS_URL).mock(return_value=httpx.Response(503))
    respx.get(f"{OLLAMA}/models").mock(side_effect=httpx.ConnectError("connection refused"))
    roles = _check(client)["roles"]
    assert (roles["reasoning"]["status"], roles["reasoning"]["detail_code"]) == ("unreachable", "server_error")
    assert (roles["embedding"]["status"], roles["embedding"]["detail_code"]) == ("unreachable", "connection")
    assert roles["embedding"]["host"] == "localhost:11434"


@respx.mock
def test_provider_without_a_model_list_is_unchecked_not_an_alarm(client, monkeypatch):
    configure_llm(monkeypatch)
    respx.get(MODELS_URL).mock(return_value=httpx.Response(404))
    role = _check(client)["roles"]["reasoning"]
    assert (role["status"], role["detail_code"]) == ("unchecked", "no_model_list")
    assert llm_check.problem_tiers(_check(client)["roles"]) == []


@respx.mock(assert_all_called=False)
def test_unbound_roles_ask_nobody(client, monkeypatch):
    llm_off(monkeypatch)
    route = respx.get(MODELS_URL).mock(return_value=_list("x"))
    roles = _check(client)["roles"]
    assert {t: r["status"] for t, r in roles.items()} == {
        "reasoning": "unbound", "fast": "unbound", "embedding": "unbound"}
    assert roles["embedding"]["detail_code"] == "not_bound"
    assert route.call_count == 0


@respx.mock
def test_fast_without_binding_runs_on_reasoning(client, monkeypatch):
    configure_llm(monkeypatch, reasoning="gpt-gone")   # fast unbound -> uses reasoning
    respx.get(MODELS_URL).mock(return_value=_list("gpt-4.1"))
    roles = _check(client)["roles"]
    assert roles["fast"]["fallback"] is True
    assert roles["reasoning"]["fallback"] is False
    assert (roles["fast"]["status"], roles["fast"]["model"]) == ("model_missing", "gpt-gone")
    # Its features are its own, even while it borrows the reasoning model.
    assert "categorize" in roles["fast"]["tasks"]
    assert "research_chat" in roles["reasoning"]["tasks"]
    assert roles["embedding"]["tasks"] == ["semantic_search"]


@respx.mock
def test_ollama_without_a_key_is_a_valid_connection(client, monkeypatch):
    configure_llm(monkeypatch, reasoning="llama3", base_url=OLLAMA, api_key="",
                  embedding="nomic-embed-text")
    route = respx.get(f"{OLLAMA}/models").mock(
        return_value=_list("llama3:latest", "nomic-embed-text:latest"))
    roles = _check(client)["roles"]
    # Ollama lists "name:latest" for a model bound as "name" — the same model.
    assert roles["reasoning"]["status"] == "ok"
    assert roles["embedding"]["status"] == "ok"
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_checking_again_replaces_the_result(client, monkeypatch):
    configure_llm(monkeypatch, reasoning="gpt-4o")
    route = respx.get(MODELS_URL).mock(return_value=_list("gpt-4.1"))
    first = _check(client)
    assert first["roles"]["reasoning"]["status"] == "model_missing"
    assert client.get("/api/llm/startup-check").json() == first

    route.mock(return_value=_list("gpt-4o", "gpt-4.1"))
    second = _check(client)
    assert second["roles"]["reasoning"]["status"] == "ok"
    assert client.get("/api/llm/startup-check").json()["roles"]["reasoning"]["status"] == "ok"


def test_before_the_first_run_there_is_no_result(client):
    assert client.get("/api/llm/startup-check").json() == {"checked_at": None, "running": False, "roles": {}, "notices": []}


@respx.mock
def test_runs_in_the_background_at_startup(monkeypatch):
    """The start does not wait: while the model list is still on its way the
    app answers, and says the check is running; afterwards the result is there."""
    configure_llm(monkeypatch, reasoning="gpt-4.1")
    monkeypatch.setattr(llm_router, "CHECK_ON_STARTUP", True)
    answer = threading.Event()

    def slow(request):
        answer.wait(5)
        return _list("gpt-4.1")

    respx.get(MODELS_URL).mock(side_effect=slow)
    with TestClient(webapp.app) as c:
        during = c.get("/api/llm/startup-check").json()
        answer.set()
        deadline = time.monotonic() + 5
        body = during
        while body["running"] and time.monotonic() < deadline:
            time.sleep(0.02)
            body = c.get("/api/llm/startup-check").json()
    assert during["running"] is True and during["checked_at"] is None
    assert body["running"] is False
    assert body["roles"]["reasoning"]["status"] == "ok"
    # Only the model list was asked — no chat, no embedding.
    assert [(call.request.method, call.request.url.path) for call in respx.calls] == [("GET", "/v1/models")]


def test_an_older_run_never_overwrites_a_newer_result(monkeypatch):
    """Two runs overlap (the boot's and a "Check again"): the newest wins."""
    results = iter([{"reasoning": {"status": "ok"}}, {"reasoning": {"status": "model_missing"}}])
    monkeypatch.setattr(llm_router.llm_client, "check_roles", lambda: next(results))
    old = llm_router._begin_check()
    new = llm_router._begin_check()
    llm_router._run_check(new)
    llm_router._run_check(old)          # finishes late
    assert llm_router._check_payload()["roles"] == {"reasoning": {"status": "ok"}}


# ---------------------------------------------------------------------------
# The pure classification
# ---------------------------------------------------------------------------

class TestClassify:
    def test_statuses(self):
        ok = llm_check.ModelList(models=("a", "b"), http_status=200)
        assert llm_check.classify("a", ok) == ("ok", "offered")
        assert llm_check.classify("c", ok) == ("model_missing", "not_offered")
        assert llm_check.classify("a", llm_check.ModelList(http_status=403)) == ("auth_failed", "key_rejected")
        assert llm_check.classify("a", llm_check.ModelList(http_status=502)) == ("unreachable", "server_error")
        assert llm_check.classify("a", llm_check.ModelList(http_status=405)) == ("unchecked", "no_model_list")
        # An empty or unreadable list cannot say a model is missing.
        assert llm_check.classify("a", llm_check.ModelList(models=(), http_status=200))[0] == "unchecked"
        assert llm_check.classify("a", llm_check.ModelList(http_status=200))[0] == "unchecked"
        for error, detail in (("timeout", "timeout"), ("connection", "connection"),
                              ("certificate", "certificate"), ("failed", "failed")):
            assert llm_check.classify("a", llm_check.ModelList(error=error)) == ("unreachable", detail)

    def test_ollama_latest_tag(self):
        assert llm_check.offered("llama3", ["llama3:latest"])
        assert llm_check.offered("llama3:latest", ["llama3"])
        assert not llm_check.offered("llama3:8b", ["llama3:latest"])

    def test_diagnosis_probes_group_by_connection_and_model(self):
        roles = {
            "reasoning": {"status": "model_missing", "connection_id": "oai", "connection_label": "OpenAI", "model": "gpt-4o"},
            "fast": {"status": "model_missing", "connection_id": "oai", "connection_label": "OpenAI", "model": "gpt-4o"},
            "embedding": {"status": "auth_failed", "connection_id": "emb", "connection_label": "Emb", "model": "e"},
        }
        assert llm_check.diagnosis_probes(roles) == [
            {"connection_id": "oai", "label": "OpenAI", "tiers": ["reasoning", "fast"],
             "status": "model_missing", "model": "gpt-4o"},
            {"connection_id": "emb", "label": "Emb", "tiers": ["embedding"], "status": "auth", "model": ""},
        ]


def test_every_task_has_a_feature_name_in_both_catalogs():
    """The popup says which features are off, from TASK_MODELS. A task added
    there without a name would show as a raw key path."""
    root = Path(__file__).resolve().parent.parent / "static" / "locales"
    tasks = set(TASK_MODELS) | set(llm_check.EMBEDDING_TASKS)
    for name in ("core.en.js", "core.de.js"):
        text = (root / name).read_text(encoding="utf-8")
        keys = set(re.findall(r'"startupCheck\.feature\.([a-z_]+)"', text))
        assert tasks <= keys, f"{name}: {sorted(tasks - keys)}"
