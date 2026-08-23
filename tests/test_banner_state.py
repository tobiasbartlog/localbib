"""Unit tests for purchase-prompt banner state machine."""
from __future__ import annotations

import datetime

import pytest

import webapp


@pytest.fixture(autouse=True)
def _reset_banner_state():
    """Clear all banner-related app_settings before and after each test."""
    keys = [
        "app_start_count",
        "banner_dismissed_at",
        "banner_dismiss_count",
        "license_activated",
    ]
    for k in keys:
        webapp.db.set_app_setting(k, "")
    yield
    for k in keys:
        webapp.db.set_app_setting(k, "")


def test_banner_hidden_on_first_start(client):
    """Banner not shown on first start (start_count <= 1)."""
    webapp.db.set_app_setting("app_start_count", "1")
    resp = client.get("/api/banner/state")
    assert resp.status_code == 200
    assert resp.json()["show"] is False


def test_banner_shown_on_second_start(client):
    """Banner shows from second start if never dismissed and not activated."""
    webapp.db.set_app_setting("app_start_count", "2")
    resp = client.get("/api/banner/state")
    assert resp.status_code == 200
    assert resp.json()["show"] is True


def test_banner_suppressed_within_90_days(client):
    """Banner hidden within 90-day dismiss window."""
    webapp.db.set_app_setting("app_start_count", "5")
    recent = (datetime.datetime.utcnow() - datetime.timedelta(days=45)).isoformat()
    webapp.db.set_app_setting("banner_dismissed_at", recent)
    webapp.db.set_app_setting("banner_dismiss_count", "1")
    resp = client.get("/api/banner/state")
    assert resp.json()["show"] is False


def test_banner_reappears_after_90_days(client):
    """Banner reappears once after 90 days if dismiss_count < 2."""
    webapp.db.set_app_setting("app_start_count", "10")
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=91)).isoformat()
    webapp.db.set_app_setting("banner_dismissed_at", old)
    webapp.db.set_app_setting("banner_dismiss_count", "1")
    resp = client.get("/api/banner/state")
    assert resp.json()["show"] is True


def test_banner_hidden_after_second_dismiss(client):
    """Banner permanently hidden after 2 dismisses, even after 90 days."""
    webapp.db.set_app_setting("app_start_count", "10")
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=200)).isoformat()
    webapp.db.set_app_setting("banner_dismissed_at", old)
    webapp.db.set_app_setting("banner_dismiss_count", "2")
    resp = client.get("/api/banner/state")
    assert resp.json()["show"] is False


def test_banner_suppressed_when_activated(client):
    """An activated license suppresses the banner regardless of other state."""
    webapp.db.set_app_setting("app_start_count", "10")
    webapp.db.set_app_setting("license_activated", "1")
    resp = client.get("/api/banner/state")
    assert resp.json()["show"] is False


def test_dismiss_records_timestamp(client):
    """POST /api/banner/dismiss records timestamp and increments dismiss count."""
    resp = client.post("/api/banner/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert webapp.db.get_app_setting("banner_dismissed_at") != ""
    assert webapp.db.get_app_setting("banner_dismiss_count") == "1"


def test_dismiss_increments_count(client):
    """Each POST /api/banner/dismiss bumps the dismiss counter."""
    client.post("/api/banner/dismiss")
    client.post("/api/banner/dismiss")
    assert webapp.db.get_app_setting("banner_dismiss_count") == "2"
