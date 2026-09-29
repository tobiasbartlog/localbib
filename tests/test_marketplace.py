"""GET /api/marketplace + its asset cache (#189, ADR-0021).

Seam: the FastAPI TestClient over the app (conftest's isolation), the index
URL mocked at ``marketplace_client.requests.get`` the way ``routers/version.py``'s
own GitHub poll is mocked in ``test_version_check.py``. The Dev-Suchpfad
fixture is the contract package's well-behaved ``hello`` Add-on, reused from
``test_plugin_loader.py`` -- we only ever discover it here, never load it, so
none of that test's module-hygiene dance is needed. Fixture Add-on ids are
generic ("demo1"/"demo2"), not real plugin names -- this file is exported
with the public core and must never leak a plugin identifier.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import marketplace_client
import webapp
from literature_manager import Config
from services import addon_catalog

HELLO = Path(__file__).resolve().parent.parent / "plugin_api" / "tests" / "fixtures" / "hello"


@pytest.fixture
def market(tmp_path, monkeypatch):
    """Bundle root, plugins.json, .env and the Marketplace cache all under
    ``tmp_path`` -- no test ever touches the developer's %LOCALAPPDATA%."""
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    monkeypatch.setenv("LOCALBIB_PLUGIN_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("PLUGINS_CONFIG_PATH", str(tmp_path / "plugins.json"))
    monkeypatch.setenv("LOCALBIB_MARKETPLACE_CACHE_DIR", str(tmp_path / "marketplace"))
    monkeypatch.delenv("LOCALBIB_PLUGIN_DEV_PATHS", raising=False)
    Config.reload_from_env()
    return tmp_path


def _index(*addons: dict) -> dict:
    return {"updated": "2026-09-20T00:00:00Z", "addons": list(addons)}


def _addon(addon_id: str, *, requires_source: bool = False, version: str = "1.0.0",
           trust: str = "official", **over) -> dict:
    entry = {
        "id": addon_id,
        "name": addon_id.title(),
        "tagline": f"{addon_id} tagline",
        "description": f"# {addon_id}\n\nMarkdown.",
        "trust": trust,
        "languages": ["en"],
        "tags": [],
        "icon": f"assets/{addon_id}/icon.png",
        "screenshots": [f"assets/{addon_id}/shot1.png"],
        "versions": [{
            "version": version,
            "api_version": 2,
            "min_core": "0.0.0",
            "released": "2026-09-01",
            "changelog": "initial",
            "requires_source": requires_source,
            "permissions": ["library.read", "network"],
            "artifacts": [] if requires_source else [
                {"python": "any", "url": f"https://x.invalid/{addon_id}.zip", "size": 12345, "sha256": "abc"},
            ],
        }],
    }
    entry.update(over)
    return entry


def _resp(payload: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = lambda: None
    m.json.return_value = payload
    return m


# ---------------------------------------------------------------------------
# AC1: two mocked entries -> two cards with the right badges
# ---------------------------------------------------------------------------

def test_two_index_entries_render_as_two_cards_with_badges(market):
    index = _index(_addon("demo1"), _addon("demo2", requires_source=True, trust="third-party"))
    with patch("marketplace_client.requests.get", return_value=_resp(index)):
        data = TestClient(webapp.app).get("/api/marketplace").json()

    assert data["has_index"] is True
    assert data["offline"] is False
    cards = {c["id"]: c for c in data["addons"]}
    assert set(cards) == {"demo1", "demo2"}
    assert cards["demo1"]["trust"] == "official"
    assert cards["demo1"]["requires_source"] is False
    assert cards["demo1"]["state"] == "not_installed"
    assert cards["demo2"]["requires_source"] is True
    assert cards["demo2"]["trust"] == "third-party"
    assert cards["demo2"]["size"] is None  # no artifact -- nothing to size


# ---------------------------------------------------------------------------
# #199: a not-installed, incompatible card carries a machine-readable reason
# ---------------------------------------------------------------------------

def test_incompatible_card_carries_a_machine_readable_reason(market):
    """The live bug: the only artifact is built for a Python this interpreter
    is not -- the card must say which, not just "Incompatible"."""
    real_tag = addon_catalog.python_tag()
    index = _index(_addon("demo1", versions=[{
        "version": "1.0.0", "api_version": 2, "min_core": "0.0.0",
        "released": "2026-09-01", "changelog": "", "requires_source": False,
        "permissions": [],
        "artifacts": [{"python": "cp99-win_amd64", "url": "https://x.invalid/demo1.zip", "size": 1, "sha256": "a"}],
    }]))
    with patch("marketplace_client.requests.get", return_value=_resp(index)):
        data = TestClient(webapp.app).get("/api/marketplace").json()

    card = {c["id"]: c for c in data["addons"]}["demo1"]
    assert card["state"] == "incompatible"
    assert card["incompatible_reason"] == {"code": "python", "needed": ["cp99-win_amd64"], "have": real_tag}


def test_dev_search_path_addon_appears_even_without_an_index_entry(market, monkeypatch):
    monkeypatch.setenv("LOCALBIB_PLUGIN_DEV_PATHS", str(HELLO))
    Config.reload_from_env()
    index = _index(_addon("demo1"))
    with patch("marketplace_client.requests.get", return_value=_resp(index)):
        data = TestClient(webapp.app).get("/api/marketplace").json()

    cards = {c["id"]: c for c in data["addons"]}
    assert "hello" in cards
    assert cards["hello"]["state"] == "dev"
    assert cards["hello"]["in_index"] is False
    assert cards["hello"]["source"] == "dev"


# ---------------------------------------------------------------------------
# AC2: offline shows the cache with its date; no cache is a clear empty state
# ---------------------------------------------------------------------------

def _write_stale_cache(cache_dir: Path, index: dict, age_seconds: float) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.json").write_text(
        json.dumps({"fetched_at": time.time() - age_seconds, "index": index}), encoding="utf-8"
    )


def test_offline_serves_the_stale_cache_with_its_date(market):
    index = _index(_addon("demo1"))
    _write_stale_cache(market / "marketplace", index, age_seconds=marketplace_client.REFRESH_INTERVAL_SECONDS + 3600)

    with patch("marketplace_client.requests.get", side_effect=OSError("offline")):
        data = TestClient(webapp.app).get("/api/marketplace").json()

    assert data["offline"] is True
    assert data["has_index"] is True
    assert data["index_date"] is not None
    assert {c["id"] for c in data["addons"]} == {"demo1"}


def test_no_cache_and_no_network_is_a_clear_empty_state(market):
    with patch("marketplace_client.requests.get", side_effect=OSError("offline")):
        data = TestClient(webapp.app).get("/api/marketplace").json()

    assert data["has_index"] is False
    assert data["offline"] is True
    assert data["addons"] == []


# ---------------------------------------------------------------------------
# #199: opening the Marketplace always tries the network first, so a freshly
# published Add-on shows up without waiting for the once-a-day window.
# ---------------------------------------------------------------------------

def test_open_refetches_a_fresh_cache_instead_of_serving_it_unchanged(market):
    """A cache written seconds ago is still younger than a day -- ``fetch()``
    without ``force`` would serve it unchanged, exactly the bug (#199): an
    Add-on published minutes after the user's first open did not show for 24h."""
    stale_view = _index(_addon("demo1"))
    _write_stale_cache(market / "marketplace", stale_view, age_seconds=5)
    fresh_view = _index(_addon("demo1"), _addon("demo2"))

    with patch("marketplace_client.requests.get", return_value=_resp(fresh_view)) as mock_get:
        data = TestClient(webapp.app).get("/api/marketplace").json()

    assert mock_get.called
    assert data["offline"] is False
    assert {c["id"] for c in data["addons"]} == {"demo1", "demo2"}


def test_open_falls_back_to_cache_when_the_network_fails_even_if_it_is_fresh(market):
    """Opening still tries the network first even with a fresh cache on disk
    -- a failed attempt degrades to that cache, marked ``offline``."""
    fresh_view = _index(_addon("demo1"))
    _write_stale_cache(market / "marketplace", fresh_view, age_seconds=5)

    with patch("marketplace_client.requests.get", side_effect=OSError("offline")) as mock_get:
        data = TestClient(webapp.app).get("/api/marketplace").json()

    assert mock_get.called
    assert data["offline"] is True
    assert data["has_index"] is True
    assert {c["id"] for c in data["addons"]} == {"demo1"}


def test_refresh_if_stale_still_skips_a_fetch_within_a_day(market):
    """``refresh_if_stale`` (the version-check's piggyback) keeps the
    once-a-day gate -- unlike opening the Marketplace, it must not hit the
    network on every poll."""
    _write_stale_cache(market / "marketplace", _index(_addon("demo1")), age_seconds=5)

    with patch("marketplace_client.requests.get", side_effect=AssertionError("must not fetch")) as mock_get:
        marketplace_client.refresh_if_stale()

    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# AC5: icons are cached through the core, never re-fetched, never traversable
# ---------------------------------------------------------------------------

def test_icon_is_cached_and_only_fetched_once(market):
    png = MagicMock()
    png.status_code = 200
    png.raise_for_status = lambda: None
    png.iter_content = lambda chunk_size: [b"\x89PNG\r\n"]

    with patch("marketplace_client.requests.get", return_value=png) as mock_get:
        client = TestClient(webapp.app)
        r1 = client.get("/api/marketplace/assets/assets/demo1/icon.png")
        r2 = client.get("/api/marketplace/assets/assets/demo1/icon.png")

    assert r1.status_code == 200
    assert r1.content == b"\x89PNG\r\n"
    assert r2.status_code == 200
    assert mock_get.call_count == 1  # the second request is served from disk


def test_icon_path_traversal_is_rejected(market):
    """Direct, like addon_frontend.resolve_file's own tests: the guard is
    checked before ever touching the filesystem, not through HTTP/URL
    encoding, which a client library may normalise before the guard sees it."""
    for rel in ("../secret.png", "assets/../../secret.png", "/etc/passwd", "a\x00b"):
        assert marketplace_client.safe_asset_path(rel) is None
    assert marketplace_client.safe_asset_path("assets/demo1/icon.png") is not None


def test_missing_icon_answers_404_without_raising(market):
    with patch("marketplace_client.requests.get", side_effect=OSError("gone")):
        resp = TestClient(webapp.app).get("/api/marketplace/assets/assets/nope/icon.png")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC4: the version-check triggers at most one index fetch a day
# ---------------------------------------------------------------------------

def _dispatching_get(index: dict, release: dict):
    """``routers.version``'s ``http_requests`` and ``marketplace_client``'s
    ``requests`` are the *same* module object -- patching either patches the
    process-wide ``requests.get``, so a test exercising both call sites needs
    one mock that tells them apart by URL, not two independent patches."""
    def _get(url, *args, **kwargs):
        if url == marketplace_client.INDEX_URL:
            return _resp(index)
        return _resp(release)
    return _get


def test_version_check_refreshes_the_index_at_most_once(market):
    index = _index(_addon("demo1"))
    release = {"tag_name": "v0.1.0", "html_url": "x", "assets": []}
    with patch("marketplace_client.requests.get", side_effect=_dispatching_get(index, release)) as mock_get:
        client = TestClient(webapp.app)
        client.get("/api/version-check")
        client.get("/api/version-check")

    index_calls = [c for c in mock_get.call_args_list if c.args and c.args[0] == marketplace_client.INDEX_URL]
    assert len(index_calls) == 1


def test_version_check_never_raises_when_the_index_is_unreachable(market):
    def _boom(url, *args, **kwargs):
        raise OSError("offline")

    with patch("marketplace_client.requests.get", side_effect=_boom):
        resp = TestClient(webapp.app).get("/api/version-check")

    assert resp.status_code == 200
