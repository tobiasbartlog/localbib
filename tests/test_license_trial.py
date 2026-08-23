"""HTTP-seam tests for the trial lifecycle and the frozen-build gate (#144).

The trial is derived, never stored: the only persisted fact is the first-run
timestamp. These tests therefore move the *anchor* rather than the clock, and
flip ``sys.frozen`` — the one switch that decides whether this install is a
gated binary or an ungated source checkout.

Two invariants get more than one test each, because breaking them silently is
the expensive failure mode:

* a source install is never trialled and never gated (the AGPL promise), and
* a legacy Lemon-Squeezy install is never locked out without an explanation.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import webapp
from services import license_state

REPO_ROOT = Path(__file__).resolve().parent.parent

TRACKED_SETTINGS = (
    "license_activated",
    "license_key",
    "license_activation_id",
    "license_activated_at",
    "license_first_run_at",
    "is_supporter",
    "app_start_count",
    "banner_dismissed_at",
    "banner_dismiss_count",
)


@pytest.fixture(autouse=True)
def _reset_state():
    for key in TRACKED_SETTINGS:
        webapp.db.set_app_setting(key, "")
    yield
    for key in TRACKED_SETTINGS:
        webapp.db.set_app_setting(key, "")


@pytest.fixture
def frozen(monkeypatch):
    """Pretend this process is the PyInstaller build."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def _anchor(days_ago: float) -> None:
    """Stamp the first run `days_ago` days in the past."""
    stamp = datetime.utcnow() - timedelta(days=days_ago)
    webapp.db.set_app_setting("license_first_run_at", stamp.isoformat())


def _status(client) -> dict:
    return client.get("/api/license/status").json()


# ---------------------------------------------------------------------------
# Trial countdown
# ---------------------------------------------------------------------------

def test_fresh_install_reports_a_full_14_day_trial(client, frozen):
    """Day one must read "14 Tage", not "13" — the anchor is stamped *now*."""
    _anchor(0)
    body = _status(client)

    assert body["is_frozen"] is True
    assert body["trial"]["days_total"] == 14
    assert body["trial"]["days_remaining"] == 14
    assert body["trial"]["expired"] is False
    assert body["blocked"] is False


def test_trial_counts_down_with_the_anchor(client, frozen):
    """Nine days in, five are left — and the last day still reports 1, not 0."""
    _anchor(9)
    assert _status(client)["trial"]["days_remaining"] == 5

    _anchor(13.5)
    late = _status(client)["trial"]
    assert late["days_remaining"] == 1
    assert late["expired"] is False


def test_expired_trial_blocks_the_frozen_build(client, frozen):
    """Past 14 days the frozen build asks for a key and says so."""
    _anchor(15)
    body = _status(client)

    assert body["trial"]["expired"] is True
    assert body["trial"]["days_remaining"] == 0
    assert body["blocked"] is True
    assert body["checkout_url"]  # ... and offers the way out


def test_lost_anchor_grants_a_full_trial_instead_of_locking_out(client, frozen):
    """A missing/garbled timestamp must fail open, never into a lockout."""
    webapp.db.set_app_setting("license_first_run_at", "not-a-date")
    body = _status(client)

    assert body["trial"]["days_remaining"] == 14
    assert body["blocked"] is False


# ---------------------------------------------------------------------------
# The AGPL promise: a source install is never trialled, never gated
# ---------------------------------------------------------------------------

def test_source_install_has_no_trial_at_all(client):
    """No frozen flag -> no counter to show, nothing to count down."""
    _anchor(0)
    body = _status(client)

    assert body["is_frozen"] is False
    assert body["trial"] is None
    assert body["blocked"] is False


def test_expired_anchor_never_gates_a_source_install(client):
    """The same expired anchor that blocks a binary leaves the checkout free."""
    _anchor(400)
    body = _status(client)

    assert body["blocked"] is False
    assert body["trial"] is None


def test_source_install_is_not_gated_even_with_a_legacy_supporter_flag(client):
    """Belt and braces: two "unactivated" reasons still gate nothing here."""
    _anchor(400)
    webapp.db.set_app_setting("is_supporter", "1")
    assert _status(client)["blocked"] is False


# ---------------------------------------------------------------------------
# Activation ends the trial
# ---------------------------------------------------------------------------

def test_activation_ends_the_trial_permanently(client, frozen, monkeypatch):
    """Once activated there is no counter left — not even an expired one."""
    _anchor(30)
    monkeypatch.setattr(
        "routers.license.httpx.post",
        lambda url, **kw: httpx.Response(
            200, json={"id": "activation-1"},
            request=httpx.Request("POST", "https://api.polar.sh/x"),
        ),
    )
    client.post("/api/license/activate", json={"key": "LB--VALID"})

    body = _status(client)
    assert body["activated"] is True
    assert body["trial"] is None
    assert body["blocked"] is False


def test_activation_response_carries_the_masked_key_for_the_confirmation(client, frozen, monkeypatch):
    """#156: the SPA reuses this masking for its own-key confirmation — it must
    never see the key in full and never mask it itself."""
    _anchor(30)
    monkeypatch.setattr(
        "routers.license.httpx.post",
        lambda url, **kw: httpx.Response(
            200, json={"id": "activation-1"},
            request=httpx.Request("POST", "https://api.polar.sh/x"),
        ),
    )
    body = client.post("/api/license/activate", json={"key": "LB--SECRET-KEY-BA08"}).json()

    assert body["key"] == "••••BA08"
    assert "LB--SECRET-KEY-BA08" not in str(body)


def test_activation_response_omits_remaining_slots_when_polar_does_not_send_them(client, frozen, monkeypatch):
    """#156: no guessing — if Polar's activation reply carries no figure, the
    field stays absent rather than being computed or invented."""
    _anchor(30)
    monkeypatch.setattr(
        "routers.license.httpx.post",
        lambda url, **kw: httpx.Response(
            200, json={"id": "activation-1"},
            request=httpx.Request("POST", "https://api.polar.sh/x"),
        ),
    )
    body = client.post("/api/license/activate", json={"key": "LB--VALID"}).json()

    assert "activations_remaining" not in body


def test_activation_response_carries_remaining_slots_when_polar_sends_them(client, frozen, monkeypatch):
    """#156: when the figure IS in Polar's reply, it is passed through as-is."""
    _anchor(30)
    monkeypatch.setattr(
        "routers.license.httpx.post",
        lambda url, **kw: httpx.Response(
            200, json={"id": "activation-1", "activations_remaining": 1},
            request=httpx.Request("POST", "https://api.polar.sh/x"),
        ),
    )
    body = client.post("/api/license/activate", json={"key": "LB--VALID"}).json()

    assert body["activations_remaining"] == 1


def test_activation_is_the_only_way_out_of_the_gate(client, frozen, monkeypatch):
    """A rejected key leaves the gate standing — no accidental unlock."""
    _anchor(30)
    monkeypatch.setattr(
        "routers.license.httpx.post",
        lambda url, **kw: httpx.Response(
            404, json={"detail": "Not found"},
            request=httpx.Request("POST", "https://api.polar.sh/x"),
        ),
    )
    assert client.post("/api/license/activate", json={"key": "NOPE"}).json()["activated"] is False
    assert _status(client)["blocked"] is True


# ---------------------------------------------------------------------------
# Legacy Lemon Squeezy installs
# ---------------------------------------------------------------------------

def test_legacy_supporter_counts_as_unactivated_but_gets_the_note(client, frozen):
    """The old receipt key does not activate — and the user is told why."""
    _anchor(30)
    webapp.db.set_app_setting("is_supporter", "1")
    body = _status(client)

    assert body["activated"] is False
    assert body["legacy_supporter"] is True
    assert body["legacy_note"]
    assert "support@localbib.com" in body["legacy_note"]
    assert "kostenlos" in body["legacy_note"]


def test_legacy_supporter_is_never_locked_out_silently(client, frozen):
    """If the gate stands for a legacy install, it carries the explanation."""
    _anchor(30)
    webapp.db.set_app_setting("is_supporter", "1")
    body = _status(client)

    assert body["blocked"] is True
    assert body["legacy_note"], "a blocked legacy install without a note is the forbidden case"


def test_legacy_supporter_still_gets_the_full_trial(client, frozen):
    """Upgrading from the old build starts the 14 days, it does not skip them."""
    _anchor(1)
    webapp.db.set_app_setting("is_supporter", "1")
    body = _status(client)

    assert body["blocked"] is False
    assert body["trial"]["days_remaining"] == 13


def test_activated_install_shows_no_legacy_note(client, frozen):
    """A legacy flag next to a real activation is history, not a message."""
    webapp.db.set_app_setting("is_supporter", "1")
    webapp.db.set_app_setting("license_activated", "1")
    body = _status(client)

    assert body["legacy_supporter"] is False
    assert body["legacy_note"] == ""


# ---------------------------------------------------------------------------
# The anchor itself
# ---------------------------------------------------------------------------

def test_startup_stamps_the_first_run_exactly_once():
    """The anchor is set on the first start and never moved afterwards."""
    webapp.db.set_app_setting("license_first_run_at", "")
    with TestClient(webapp.app):
        pass
    first = webapp.db.get_app_setting("license_first_run_at")
    assert first

    with TestClient(webapp.app):
        pass
    assert webapp.db.get_app_setting("license_first_run_at") == first


def test_status_derives_the_trial_without_touching_the_network(client, frozen, monkeypatch):
    """Trial state is local arithmetic — a phone-home here would be a bug."""
    def _boom(*args, **kwargs):  # pragma: no cover - only fires on regression
        raise AssertionError("trial state must not call the network")

    monkeypatch.setattr("routers.license.httpx.post", _boom)
    _anchor(5)
    assert _status(client)["trial"]["days_remaining"] == 9


# ---------------------------------------------------------------------------
# Banner mirrors the trial
# ---------------------------------------------------------------------------

def test_banner_reports_the_days_remaining(client, frozen):
    """The banner gets the same numbers the gate is derived from."""
    webapp.db.set_app_setting("app_start_count", "3")
    _anchor(4)
    body = client.get("/api/banner/state").json()

    assert body["show"] is True
    assert body["trial"]["days_remaining"] == 10
    assert body["blocked"] is False


def test_banner_goes_quiet_once_the_gate_stands(client, frozen):
    """Blocking dialog and nagging banner at once would be one ask too many."""
    webapp.db.set_app_setting("app_start_count", "9")
    _anchor(30)
    body = client.get("/api/banner/state").json()

    assert body["blocked"] is True
    assert body["show"] is False


def test_banner_disappears_once_activated(client, frozen):
    """Paid is paid — no counter, no banner."""
    webapp.db.set_app_setting("app_start_count", "9")
    _anchor(4)
    webapp.db.set_app_setting("license_activated", "1")
    body = client.get("/api/banner/state").json()

    assert body["show"] is False
    assert body["trial"] is None


def test_banner_keeps_its_dismiss_backoff_during_the_trial(client, frozen):
    """The 90-day suppression survives the trial rework."""
    webapp.db.set_app_setting("app_start_count", "9")
    _anchor(4)
    client.post("/api/banner/dismiss")

    body = client.get("/api/banner/state").json()
    assert body["show"] is False
    assert body["trial"]["days_remaining"] == 10  # ... but the state is still reported


def test_banner_on_a_source_install_carries_no_trial(client):
    """No trial to mirror -> the SPA falls back to its neutral purchase text."""
    webapp.db.set_app_setting("app_start_count", "3")
    _anchor(4)
    body = client.get("/api/banner/state").json()

    assert body["show"] is True
    assert body["trial"] is None


# ---------------------------------------------------------------------------
# The pure derivation, exercised directly where the HTTP seam cannot reach
# ---------------------------------------------------------------------------

def test_derive_is_pure_and_clock_injectable():
    """The service takes its clock as an argument — no hidden global time."""
    start = datetime(2026, 1, 1, 12, 0, 0)
    state = license_state.derive(
        activated=False,
        first_run_at=start.isoformat(),
        is_frozen=True,
        now=start + timedelta(days=14, seconds=1),
    )
    assert state.trial["expired"] is True
    assert state.blocked is True


def test_derive_tolerates_a_timezone_aware_stamp():
    """A stamp written by some other tool must not crash the gate open or shut."""
    state = license_state.derive(
        activated=False,
        first_run_at="2026-01-01T12:00:00+00:00",
        is_frozen=True,
        now=datetime(2026, 1, 5, 12, 0, 0),
    )
    assert state.trial["days_remaining"] == 10


# ---------------------------------------------------------------------------
# Price — one constant, one place (#154)
# ---------------------------------------------------------------------------

def test_status_carries_the_price_display_alongside_the_checkout_url(client):
    """GET /api/license/status is the SPA's only source for the price — no new
    endpoint, the existing response just grows one field."""
    body = _status(client)
    assert body["price_display"] == license_state.PRICE_DISPLAY
    assert "checkout_url" in body  # still present, unchanged shape


def test_context_glossary_pins_the_price_to_the_constant():
    """CONTEXT.md is private and never reaches the export, so the price copy
    the constant cannot travel to (the ``Lizenz`` glossary entry) is pinned
    here instead: editing the constant without editing the glossary fails."""
    context = (REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    assert license_state.PRICE_DISPLAY in context
