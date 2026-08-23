"""HTTP-seam tests for Lizenzschluessel-Aktivierung against a mocked Polar API.

Replaces the retired Supporter-Key tests (ADR-0015): the key is validated
exactly once at activation; every later status read comes from local state and
must not touch the network — not after a restart, not ever.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import webapp
from literature_manager import Config, Database

LICENSE_SETTINGS = (
    "license_activated",
    "license_key",
    "license_activation_id",
    "license_activated_at",
)


@pytest.fixture(autouse=True)
def _reset_license_state():
    for key in LICENSE_SETTINGS:
        webapp.db.set_app_setting(key, "")
    yield
    for key in LICENSE_SETTINGS:
        webapp.db.set_app_setting(key, "")


class _FakePolar:
    """Records the calls a test expects and answers them with one response."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc
        return self.response


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload,
        request=httpx.Request("POST", "https://api.polar.sh/x"),
    )


def _forbid_network(monkeypatch):
    """Make any outbound POST from the license router an immediate failure."""

    def _boom(*args, **kwargs):  # pragma: no cover - only fires on regression
        raise AssertionError("license status must not call the network")

    monkeypatch.setattr("routers.license.httpx.post", _boom)


ACTIVATION_OK = {
    "id": "activation-42",
    "license_key_id": "lk-1",
    "label": "Test-PC",
    "license_key": {"id": "lk-1", "status": "granted"},
}


def test_activation_persists_key_and_activation_id(client, monkeypatch):
    """A 200 from Polar activates the install and stores key + activation id."""
    polar = _FakePolar(_response(200, ACTIVATION_OK))
    monkeypatch.setattr("routers.license.httpx.post", polar)

    resp = client.post("/api/license/activate", json={"key": "LB--VALID-KEY"})

    assert resp.status_code == 200
    assert resp.json()["activated"] is True
    assert webapp.db.get_app_setting("license_activated") == "1"
    assert webapp.db.get_app_setting("license_key") == "LB--VALID-KEY"
    assert webapp.db.get_app_setting("license_activation_id") == "activation-42"
    assert webapp.db.get_app_setting("license_activated_at") != ""

    # The request went to Polar's activation endpoint, carrying key + org.
    assert len(polar.calls) == 1
    call = polar.calls[0]
    assert call["url"].endswith("/v1/customer-portal/license-keys/activate")
    assert call["json"]["key"] == "LB--VALID-KEY"
    assert call["json"]["organization_id"] == Config.POLAR_ORGANIZATION_ID
    assert call["json"]["label"]  # a device label, so Polar can list activations


def test_status_reports_activated_after_restart_without_network(client, monkeypatch):
    """After activation the status is served from local state — no request.

    The restart is simulated the only way it can be in-process: a second
    TestClient reading through a fresh Database handle, with the outbound call
    booby-trapped. Everything the activated state is made of lives in the
    settings table, so that is exactly what a restarted app finds.
    """
    monkeypatch.setattr("routers.license.httpx.post", _FakePolar(_response(200, ACTIVATION_OK)))
    client.post("/api/license/activate", json={"key": "LB--VALID-KEY"})

    _forbid_network(monkeypatch)
    with TestClient(webapp.app) as restarted:
        resp = restarted.get("/api/license/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["activated"] is True
    assert body["activated_at"] != ""
    assert "VALID-KEY" not in body["key"]  # masked, never echoed in full
    assert body["key"].endswith("-KEY")
    # ... and the persisted state a fresh process would read is really there.
    fresh_db = Database(Config.DB_PATH)
    assert fresh_db.get_app_setting("license_activated") == "1"
    assert fresh_db.get_app_setting("license_activation_id") == "activation-42"


def test_status_default_not_activated(client, monkeypatch):
    """A fresh install reports not activated and offers the checkout link."""
    _forbid_network(monkeypatch)
    body = client.get("/api/license/status").json()
    assert body["activated"] is False
    assert body["key"] == ""
    assert body["checkout_url"] == Config.POLAR_CHECKOUT_URL


def test_second_activation_is_local_only(client, monkeypatch):
    """An activated install never validates again — not even on a second POST."""
    monkeypatch.setattr("routers.license.httpx.post", _FakePolar(_response(200, ACTIVATION_OK)))
    client.post("/api/license/activate", json={"key": "LB--VALID-KEY"})

    _forbid_network(monkeypatch)
    resp = client.post("/api/license/activate", json={"key": "LB--OTHER-KEY"})

    assert resp.json()["activated"] is True
    assert webapp.db.get_app_setting("license_key") == "LB--VALID-KEY"


def test_invalid_key_reports_german_error(client, monkeypatch):
    """404 ResourceNotFound -> "ungueltig" message, nothing persisted."""
    monkeypatch.setattr(
        "routers.license.httpx.post",
        _FakePolar(_response(404, {"error": "ResourceNotFound",
                                   "detail": "License key does not exist."})),
    )
    body = client.post("/api/license/activate", json={"key": "NOPE"}).json()

    assert body["activated"] is False
    assert "ungültig" in body["error"].lower()
    assert webapp.db.get_app_setting("license_activated") == ""
    assert webapp.db.get_app_setting("license_key") == ""


def test_activation_limit_reports_distinct_error(client, monkeypatch):
    """403 with Polar's limit detail -> the "zwei Geräte" message."""
    monkeypatch.setattr(
        "routers.license.httpx.post",
        _FakePolar(_response(403, {"error": "NotPermitted",
                                   "detail": "License key activation limit already reached."})),
    )
    body = client.post("/api/license/activate", json={"key": "LB--USED-UP"}).json()

    assert body["activated"] is False
    assert "Geräten" in body["error"]
    assert webapp.db.get_app_setting("license_activated") == ""


def test_network_failure_reports_distinct_error(client, monkeypatch):
    """A transport error -> the "nicht erreichbar" message, no persistence."""
    monkeypatch.setattr(
        "routers.license.httpx.post",
        _FakePolar(exc=httpx.ConnectError("no route to host")),
    )
    body = client.post("/api/license/activate", json={"key": "LB--ANY"}).json()

    assert body["activated"] is False
    assert "erreichbar" in body["error"]
    assert webapp.db.get_app_setting("license_activated") == ""


def test_three_failure_paths_carry_distinct_messages(client, monkeypatch):
    """Invalid, exhausted and offline must never read the same to the user."""
    cases = {
        "invalid": _FakePolar(_response(404, {"detail": "License key does not exist."})),
        "limit": _FakePolar(_response(403, {"detail": "License key activation limit already reached."})),
        "offline": _FakePolar(exc=httpx.ConnectTimeout("timed out")),
    }
    messages = set()
    for fake in cases.values():
        monkeypatch.setattr("routers.license.httpx.post", fake)
        messages.add(client.post("/api/license/activate", json={"key": "K"}).json()["error"])
    assert len(messages) == 3


def test_limit_message_does_not_depend_on_polar_wording(client, monkeypatch):
    """403 maps to the limit message by status, not by English detail text.

    Polar documents 403 for this endpoint as "activation not supported or limit
    reached"; grepping the prose would mean a wording change at Polar silently
    tells the customer the wrong next step.
    """
    monkeypatch.setattr(
        "routers.license.httpx.post",
        _FakePolar(_response(403, {"error": "NotPermitted", "detail": "Nope."})),
    )
    body = client.post("/api/license/activate", json={"key": "LB--USED-UP"}).json()

    assert body["activated"] is False
    assert "Geräten" in body["error"]


def test_malformed_request_is_not_blamed_on_the_key(client, monkeypatch):
    """422 is our bug, not a typo of the customer — say so, with the code."""
    monkeypatch.setattr(
        "routers.license.httpx.post",
        _FakePolar(_response(422, {"error": "RequestValidationError",
                                   "detail": [{"loc": ["body", "organization_id"]}]})),
    )
    body = client.post("/api/license/activate", json={"key": "LB--FINE"}).json()

    assert body["activated"] is False
    assert "422" in body["error"]
    assert "ungültig" not in body["error"].lower()
    assert webapp.db.get_app_setting("license_activated") == ""


def test_server_error_is_treated_as_unreachable(client, monkeypatch):
    """A 5xx is Polar's problem, not the customer's key."""
    monkeypatch.setattr(
        "routers.license.httpx.post", _FakePolar(_response(502, {"detail": "Bad gateway"}))
    )
    body = client.post("/api/license/activate", json={"key": "LB--ANY"}).json()

    assert body["activated"] is False
    assert "erreichbar" in body["error"]


def test_empty_key_never_reaches_polar(client, monkeypatch):
    """An empty field is answered locally, without burning an activation."""
    _forbid_network(monkeypatch)
    body = client.post("/api/license/activate", json={"key": "   "}).json()

    assert body["activated"] is False
    assert body["error"]


def test_supporter_endpoints_are_gone(client):
    """ADR-0015: /api/supporter/* is removed, not kept alongside."""
    assert client.get("/api/supporter/status").status_code == 404
    assert client.post("/api/supporter/validate-key", json={"key": "x"}).status_code == 404


def test_shipped_polar_store_survives_empty_env(monkeypatch):
    """Leere .env-Eintraege duerfen den mitgelieferten Store nicht ausknipsen."""
    for var in ("POLAR_API_BASE", "POLAR_ORGANIZATION_ID", "POLAR_CHECKOUT_URL"):
        monkeypatch.setenv(var, "")
    try:
        Config.reload_from_env()
        assert Config.POLAR_ORGANIZATION_ID
        assert Config.POLAR_CHECKOUT_URL.startswith("https://")
        assert Config.POLAR_API_BASE == "https://api.polar.sh"
    finally:
        monkeypatch.undo()
        Config.reload_from_env()
