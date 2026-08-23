"""Unit tests for the /api/version-check endpoint."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import webapp
import routers.version as version_router


def _reset_cache() -> None:
    version_router._version_cache = {"ts": 0.0, "data": None}


def test_no_update_when_version_matches() -> None:
    """Latest tag == current version → update_available is False."""
    _reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tag_name": f"v{version_router.APP_VERSION}",
        "html_url": "https://github.com/tobiasbartlog/literature-manager-v3/releases/latest",
    }
    with patch("routers.version.http_requests.get", return_value=mock_resp):
        from fastapi.testclient import TestClient

        client = TestClient(webapp.app)
        resp = client.get("/api/version-check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["update_available"] is False
    assert data["current"] == version_router.APP_VERSION
    _reset_cache()


def test_update_available_when_newer_version() -> None:
    """Latest tag > current version → update_available is True."""
    _reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tag_name": "v99.0.0",
        "html_url": "https://github.com/tobiasbartlog/literature-manager-v3/releases/tag/v99.0.0",
    }
    with patch("routers.version.http_requests.get", return_value=mock_resp):
        from fastapi.testclient import TestClient

        client = TestClient(webapp.app)
        resp = client.get("/api/version-check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["update_available"] is True
    assert data["latest"] == "99.0.0"
    assert data["release_url"] != ""
    _reset_cache()


def test_network_error_degrades_silently() -> None:
    """Network error → update_available False, no exception raised to client."""
    _reset_cache()
    with patch("routers.version.http_requests.get", side_effect=Exception("Connection timeout")):
        from fastapi.testclient import TestClient

        client = TestClient(webapp.app)
        resp = client.get("/api/version-check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["update_available"] is False
    assert data["latest"] is None
    _reset_cache()


# ---------------------------------------------------------------------------
# The VERSION-file mechanism (issue #146)
#
# This is the whole chain that makes an installed build report its release tag:
# the release workflow writes the tag (minus "v") into VERSION, localbib.spec
# bundles that file into the frozen app, and _read_version() reads it from the
# bundle at import. The old release pipeline instead regex-patched
# "APP_VERSION = _read_version()" in the app source — a patch that silently
# stopped matching when the assignment moved to routers/version.py. These tests
# pin the replacement at the version module's own seam.
# ---------------------------------------------------------------------------

import sys
from pathlib import Path


def _freeze(monkeypatch, bundle_dir: Path) -> None:
    """Pretend we are the PyInstaller build whose bundle lives in ``bundle_dir``."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)


def test_frozen_build_reports_the_bundled_version(tmp_path, monkeypatch) -> None:
    """A frozen build reads the VERSION file the release workflow wrote."""
    (tmp_path / "VERSION").write_text("1.2.3", encoding="utf-8")
    _freeze(monkeypatch, tmp_path)

    assert version_router._read_version() == "1.2.3"


def test_leading_v_of_a_tag_is_stripped(tmp_path, monkeypatch) -> None:
    """Tags are written as v0.3.0; the reported version never carries the "v"."""
    (tmp_path / "VERSION").write_text("v0.3.0\n", encoding="utf-8")
    _freeze(monkeypatch, tmp_path)

    assert version_router._read_version() == "0.3.0"


def test_a_byte_order_mark_does_not_reach_the_version(tmp_path, monkeypatch) -> None:
    """A VERSION file written with a BOM still reports a bare version.

    Windows PowerShell 5.1 writes "Set-Content -Encoding utf8" *with* a BOM
    while pwsh 7 (the CI shell) writes it without, so the same build line
    produces different bytes depending on who runs it. U+FEFF is not
    whitespace, so a plain utf-8 read plus .strip() would report "﻿8.0.0"
    and break the update comparison.
    """
    (tmp_path / "VERSION").write_text("8.0.0", encoding="utf-8-sig")
    _freeze(monkeypatch, tmp_path)

    assert version_router._read_version() == "8.0.0"


def test_frozen_build_without_a_version_file_falls_back(tmp_path, monkeypatch) -> None:
    """No VERSION file and no git repo in the bundle → the documented fallback."""
    _freeze(monkeypatch, tmp_path)

    assert version_router._read_version() == "0.1.0"


def _source_root(monkeypatch, root: Path) -> None:
    """Unfrozen, _read_version() derives the repo root from the module's __file__.

    Pointing __file__ at a throwaway tree is what makes this testable at all:
    the real repo root has no VERSION (it is generated from the tag and
    gitignored), so a test reading it would pass only on a machine that had
    built once — and fail in CI.
    """
    (root / "routers").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(version_router, "__file__", str(root / "routers" / "version.py"))


def test_source_checkout_reads_the_repo_version_file(tmp_path, monkeypatch) -> None:
    """Unfrozen, the same function reads the repo-root VERSION — no git needed."""
    _source_root(monkeypatch, tmp_path)
    (tmp_path / "VERSION").write_text("v2.5.0\n", encoding="utf-8")

    assert version_router._read_version() == "2.5.0"


def test_source_checkout_without_a_version_file_does_not_crash(tmp_path, monkeypatch) -> None:
    """The normal state of a fresh clone: VERSION is generated, so it is absent.

    _read_version() must fall through to the git tag and finally to the
    documented "0.1.0" — never raise, or importing routers.version would take
    the whole app down.
    """
    _source_root(monkeypatch, tmp_path)

    assert version_router._read_version()


def test_version_check_reports_the_bundled_version_as_current(monkeypatch) -> None:
    """The endpoint answers with whatever the VERSION mechanism produced."""
    _reset_cache()
    monkeypatch.setattr(version_router, "APP_VERSION", "1.2.3")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"tag_name": "v1.2.3", "html_url": "https://example.invalid"}
    with patch("routers.version.http_requests.get", return_value=mock_resp):
        from fastapi.testclient import TestClient

        resp = TestClient(webapp.app).get("/api/version-check")

    assert resp.json()["current"] == "1.2.3"
    assert resp.json()["update_available"] is False
    _reset_cache()


def test_update_check_polls_the_repo_that_publishes_the_installer() -> None:
    """Releases are cut in the public repo, so that is the repo we ask (ADR-0015)."""
    assert version_router.RELEASES_API_URL == (
        "https://api.github.com/repos/tobiasbartlog/localbib/releases/latest"
    )


# ---------------------------------------------------------------------------
# One-click update (issue #148)
#
# The chain under test: the release payload names an asset, services.update_offer
# decides what that means for THIS installation, and the router does the three
# things a pure module may not - download, launch, exit. Every test below mocks
# the download/launch/exit seam; nothing here ever spawns an installer or kills
# the test process.
# ---------------------------------------------------------------------------

import pytest
from fastapi.testclient import TestClient

from services import update_offer


def _release(tag: str = "99.0.0", assets: list | None = None) -> dict:
    return {
        "tag_name": f"v{tag}",
        "html_url": f"https://github.com/tobiasbartlog/localbib/releases/tag/v{tag}",
        "assets": assets if assets is not None else [],
    }


def _installer_asset(tag: str = "99.0.0") -> dict:
    return {
        "name": f"LocalBib-Setup-{tag}.exe",
        "browser_download_url": (
            f"https://github.com/tobiasbartlog/localbib/releases/download/"
            f"v{tag}/LocalBib-Setup-{tag}.exe"
        ),
    }


def _version_check(release: dict, frozen: bool, monkeypatch) -> dict:
    _reset_cache()
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    with patch("routers.version._fetch_release", return_value=release):
        data = TestClient(webapp.app).get("/api/version-check").json()
    _reset_cache()
    return data


def test_installer_asset_becomes_the_download_url(monkeypatch) -> None:
    """A release with LocalBib-Setup-<version>.exe resolves that asset."""
    asset = _installer_asset()
    data = _version_check(_release(assets=[asset]), frozen=True, monkeypatch=monkeypatch)

    assert data["update_available"] is True
    assert data["installer_url"] == asset["browser_download_url"]
    assert data["download_url"] == asset["browser_download_url"]
    assert data["can_auto_update"] is True


def test_release_without_an_installer_degrades_to_the_release_page(monkeypatch) -> None:
    """No installer asset -> the manual link, never a blank action."""
    release = _release(assets=[])
    data = _version_check(release, frozen=True, monkeypatch=monkeypatch)

    assert data["installer_url"] == ""
    assert data["download_url"] == release["html_url"]
    assert data["can_auto_update"] is False


def test_a_foreign_exe_is_not_mistaken_for_the_installer(monkeypatch) -> None:
    """We execute the asset localbib.iss names, not just any .exe in the release."""
    stray = {"name": "debug-symbols.exe", "browser_download_url": "https://x.invalid/d.exe"}
    release = _release(assets=[stray])
    data = _version_check(release, frozen=True, monkeypatch=monkeypatch)

    assert data["installer_url"] == ""
    assert data["download_url"] == release["html_url"]


def test_source_install_is_never_offered_the_one_click_update(monkeypatch) -> None:
    """Same principle as the #144 gate: an installer must not touch a git tree."""
    data = _version_check(
        _release(assets=[_installer_asset()]), frozen=False, monkeypatch=monkeypatch
    )

    assert data["update_available"] is True
    assert data["installer_url"] != ""  # the link stays offered ...
    assert data["can_auto_update"] is False  # ... the one-click path does not


def test_offer_is_pure_on_a_missing_payload() -> None:
    """Network failure -> we know nothing, not a half-filled offer."""
    offer = update_offer.decide("1.0.0", None, is_frozen=True)

    assert offer.update_available is False
    assert offer.can_auto_update is False
    assert offer.download_url == ""


# --- parse_version ---------------------------------------------------------
#
# The comparison is the last thing between a correct install and a permanent
# "Update verfuegbar" nag, and it is fed from two sources with different
# habits: the VERSION file the release workflow writes, and the tag_name of a
# GitHub payload. Anything it cannot read counts as the oldest version, so a
# parse failure does not fail loudly - it nags forever.

# Spelled via chr() on purpose: an invisible U+FEFF in a test source is exactly
# the kind of character a reformatter eats without anyone noticing.
BOM = chr(0xFEFF)


@pytest.mark.parametrize("raw, expected", [
    ("1.2.3", (1, 2, 3, 1)),
    ("v1.2.3", (1, 2, 3, 1)),
    ("  1.2.3\n", (1, 2, 3, 1)),
    (BOM + "1.2.3", (1, 2, 3, 1)),      # a BOM is not whitespace
    ("0.3", (0, 3, 0, 1)),              # padded - not "shorter, therefore smaller"
    ("1.2.3+build7", (1, 2, 3, 1)),     # build metadata says nothing about order
    ("0.3.0-rc1", (0, 3, 0, 0)),        # a pre-release ranks below its release
    ("nonsense", (0, 0, 0, 0)),         # documented: unreadable = oldest
    ("", (0, 0, 0, 0)),
])
def test_parse_version_normalises_what_actually_arrives(raw, expected) -> None:
    assert update_offer.parse_version(raw) == expected


def test_a_release_candidate_is_not_offered_an_update_to_itself() -> None:
    """release.yml matches its tag by prefix, so v0.3.0-rc1 builds and reaches
    VERSION verbatim. int("0-rc1") used to throw, the install counted as the
    oldest version there is, and every poll offered an update to what it was."""
    offer = update_offer.decide("0.3.0-rc1", _release(tag="0.3.0-rc1"), is_frozen=True)

    assert offer.update_available is False


def test_a_release_candidate_is_offered_the_finished_release() -> None:
    """The other half of the same ordering: rc1 really is older than 0.3.0."""
    offer = update_offer.decide("0.3.0-rc1", _release(tag="0.3.0"), is_frozen=True)

    assert offer.update_available is True


def test_a_bom_in_the_running_version_does_not_fake_an_update() -> None:
    """The reader uses utf-8-sig, but a version can reach the comparison from
    elsewhere; the same install must not suddenly look out of date."""
    offer = update_offer.decide(BOM + "0.3.0", _release(tag="0.3.0"), is_frozen=True)

    assert offer.update_available is False


# --- POST /api/update/install ---------------------------------------------


@pytest.fixture()
def fake_installer(tmp_path):
    """A file that passes the "is this really a Windows executable?" check."""
    path = tmp_path / "LocalBib-Setup-99.0.0.exe"
    path.write_bytes(b"MZ" + b"\x00" * 64)
    return path


def _install(monkeypatch, *, frozen=True, release=None, download=None):
    """POST /api/update/install with every side-effecting seam mocked out."""
    _reset_cache()
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(version_router, "EXIT_DELAY_SECONDS", 0)
    calls = {"launched": [], "exited": 0}

    monkeypatch.setattr(version_router, "_launch_installer", calls["launched"].append)
    monkeypatch.setattr(
        version_router, "_exit_app", lambda: calls.__setitem__("exited", calls["exited"] + 1)
    )
    monkeypatch.setattr(
        version_router,
        "_fetch_release",
        lambda: release if release is not None else _release(assets=[_installer_asset()]),
    )
    if download is not None:
        monkeypatch.setattr(version_router, "_download_installer", download)

    resp = TestClient(webapp.app).post("/api/update/install")
    _reset_cache()
    return resp, calls


def test_one_click_downloads_launches_and_then_exits(monkeypatch, fake_installer) -> None:
    """The happy path, in the order that makes it safe: download, launch, exit."""
    resp, calls = _install(monkeypatch, download=lambda url: fake_installer)

    assert resp.status_code == 200
    assert resp.json()["status"] == "installing"
    assert resp.json()["version"] == "99.0.0"
    assert calls["launched"] == [fake_installer]
    assert calls["exited"] == 1


def test_one_click_is_refused_on_a_source_install(monkeypatch, fake_installer) -> None:
    """A git checkout is never handed an installer - and never exits."""
    resp, calls = _install(monkeypatch, frozen=False, download=lambda url: fake_installer)

    assert resp.status_code == 400
    assert "git pull" in resp.json()["detail"]
    assert calls["launched"] == []
    assert calls["exited"] == 0


def test_release_without_installer_refuses_and_names_the_manual_way(monkeypatch) -> None:
    resp, calls = _install(monkeypatch, release=_release(assets=[]))

    assert resp.status_code == 400
    assert "Release-Seite" in resp.json()["detail"]
    assert calls["exited"] == 0


def test_download_error_surfaces_in_german_and_keeps_the_app_alive(monkeypatch) -> None:
    """A failed download degrades to the manual link, not to a dead app."""

    def _boom(url):
        raise OSError("Verbindung abgebrochen")

    resp, calls = _install(monkeypatch, download=_boom)

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Installer konnte nicht geladen werden" in detail
    assert "Release-Seite" in detail
    assert calls["launched"] == []
    assert calls["exited"] == 0


def test_a_corrupt_download_is_never_executed(monkeypatch, tmp_path) -> None:
    """Whatever we are about to run must at least look like a Windows binary."""
    junk = tmp_path / "LocalBib-Setup-99.0.0.exe"
    junk.write_bytes(b"<!doctype html>rate limited")

    resp, calls = _install(monkeypatch, download=lambda url: junk)

    assert resp.status_code == 502
    assert "Release-Seite" in resp.json()["detail"]
    assert calls["launched"] == []
    assert calls["exited"] == 0


def test_no_newer_version_is_refused(monkeypatch, fake_installer) -> None:
    """The endpoint re-checks; it does not trust a stale banner."""
    current = version_router.APP_VERSION
    resp, calls = _install(
        monkeypatch,
        release=_release(tag=current, assets=[_installer_asset(current)]),
        download=lambda url: fake_installer,
    )

    assert resp.status_code == 400
    assert calls["exited"] == 0


def test_installer_is_launched_silently_with_a_relaunch() -> None:
    """The silent flags and the relaunch are a contract with installer/localbib.iss."""
    assert "/SILENT" in version_router.INSTALLER_SILENT_ARGS
    assert "/SUPPRESSMSGBOXES" in version_router.INSTALLER_SILENT_ARGS
    iss = (
        Path(version_router.__file__).parent.parent / "installer" / "localbib.iss"
    ).read_text(encoding="utf-8")
    # Without the relaunch entry the user's window would simply vanish.
    assert "skipifnotsilent" in iss
