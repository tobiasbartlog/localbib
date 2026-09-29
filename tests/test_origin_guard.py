"""Cross-site writes are refused (origin_guard): the app has no login and
binds to loopback, but any website the user has open can POST to it."""

from __future__ import annotations

import io

import pytest

from origin_guard import BAD_HOST_CODE, ERROR_CODE, is_cross_site_write, is_foreign_host, is_own_origin

EVIL = "https://evil.example"


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,host,expected", [
    ("http://localhost:8000", "localhost:8000", True),
    ("http://127.0.0.1:8000", "localhost:8000", True),   # loopback names are one host
    ("http://localhost:8000", "127.0.0.1:8000", True),
    ("http://testserver", "testserver", True),
    ("http://localhost:3000", "localhost:8000", False),  # another local app
    ("https://localhost:8000", "localhost:8000", False),  # other scheme
    (EVIL, "localhost:8000", False),
    ("http://evil.example:8000", "localhost:8000", False),
    ("null", "localhost:8000", False),
    ("", "localhost:8000", False),
])
def test_own_origin(origin, host, expected):
    assert is_own_origin(origin, host, "http") is expected


def test_the_rule():
    own = {"host": "localhost:8000", "origin": "http://localhost:8000"}
    assert not is_cross_site_write("POST", "/api/x", own)
    assert is_cross_site_write("POST", "/api/x", {**own, "origin": EVIL})
    assert is_cross_site_write("DELETE", "/api/x", {**own, "origin": EVIL})
    assert is_cross_site_write("POST", "/api/x", {"host": "localhost:8000", "sec-fetch-site": "cross-site"})
    # Reads, and requests without Origin (CLI, scripts), pass.
    assert not is_cross_site_write("GET", "/api/x", {**own, "origin": EVIL})
    assert not is_cross_site_write("POST", "/api/x", {"host": "localhost:8000"})
    assert not is_cross_site_write("POST", "/api/x", {"host": "localhost:8000", "sec-fetch-site": "same-origin"})
    # Outside the prefix the check does not apply.
    assert not is_cross_site_write("POST", "/other", {**own, "origin": EVIL})
    assert is_cross_site_write("POST", "/other", {**own, "origin": EVIL}, prefix="/")


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def _zip_upload() -> dict:
    return {"file": ("bundle.zip", io.BytesIO(b"not a zip"), "application/zip")}


def test_install_file_from_another_site_is_refused(client, monkeypatch):
    import addon_installer

    def boom(*_a, **_k):  # must never be reached
        raise AssertionError("the upload was processed")

    monkeypatch.setattr(addon_installer, "save_stream", boom)
    r = client.post("/api/marketplace/install-file", files=_zip_upload(), headers={"Origin": EVIL})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == ERROR_CODE


def test_restart_from_another_site_is_refused(client):
    r = client.post("/api/app/restart", headers={"Origin": EVIL})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == ERROR_CODE
    r = client.post("/api/app/restart", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_and_no_origin_writes_pass(client):
    # From source the restart answers its own error - the point is: not 403.
    same = client.post("/api/app/restart", headers={"Origin": "http://testserver"})
    assert same.status_code != 403
    bare = client.post("/api/app/restart")
    assert bare.status_code != 403
    # A bad upload reaches the handler (422), it is not refused at the door.
    r = client.post("/api/marketplace/install-file", files=_zip_upload(),
                    headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 422


def test_reads_from_another_site_are_not_blocked(client):
    assert client.get("/api/plugins", headers={"Origin": EVIL}).status_code == 200


# ---------------------------------------------------------------------------
# DNS rebinding: a foreign Host name is refused for every method
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host,foreign", [
    ("localhost:8000", False), ("127.0.0.1:8000", False), ("[::1]:8000", False), ("localhost", False),
    ("testserver", False),
    ("evil.example:8000", True), ("evil.example", True), ("127.0.0.1.evil.example:8000", True),
    ("localhost.evil.example", True), ("", True),
])
def test_foreign_host(host, foreign):
    assert is_foreign_host(host) is foreign


REBOUND = {"Host": "rebind.evil.example:8000", "Origin": "http://rebind.evil.example:8000"}


def test_a_rebound_host_is_refused_on_reads_and_writes(client):
    for resp in (client.get("/api/plugins", headers=REBOUND), client.get("/", headers=REBOUND),
                 client.post("/api/app/restart", headers=REBOUND)):
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == BAD_HOST_CODE


def test_loopback_hosts_are_served(client):
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000"):
        assert client.get("/api/plugins", headers={"Host": host}).status_code == 200
    same = client.post("/api/app/restart", headers={"Host": "localhost:8000", "Origin": "http://localhost:8000"})
    assert same.status_code != 403
