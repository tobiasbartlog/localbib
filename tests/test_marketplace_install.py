"""Installing from the Marketplace, from a file and from a folder (#191, ADR-0021).

Seam: the FastAPI app as a context-managed ``TestClient`` (startup loads,
shutdown unloads), the Marketplace-Index as a fresh disk cache (so no index
request is made), the artifact download mocked with ``respx``. The Bundle is
the contract package's ``hello`` fixture, packed by ``localbib-addon build``
itself — the same Zip a real release would ship. Other fixture ids are generic:
this file is exported with the public core.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import addon_installer
import webapp
from literature_manager import Config
from plugin_api.addon_tool import build_bundle
from services import addon_install

HELLO = Path(__file__).resolve().parent.parent / "plugin_api" / "tests" / "fixtures" / "hello"
HELLO_PERMISSIONS = ["library.read", "llm", "settings.core", "storage"]
URL = "https://downloads.example.org/hello-0.1.0.zip"


@pytest.fixture
def market(tmp_path, monkeypatch):
    """Bundle root, plugins.json, .env and the index cache under tmp_path; the
    ``hello`` fixture hidden from sys.modules/sys.path (see test_plugin_loader)."""
    bundles = tmp_path / "bundles"
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    monkeypatch.setenv("LOCALBIB_PLUGIN_DIR", str(bundles))
    monkeypatch.setenv("PLUGINS_CONFIG_PATH", str(tmp_path / "plugins.json"))
    monkeypatch.setenv("LOCALBIB_MARKETPLACE_CACHE_DIR", str(tmp_path / "marketplace"))
    monkeypatch.delenv("LOCALBIB_PLUGIN_DEV_PATHS", raising=False)
    for name in [m for m in sys.modules if m == "hello" or m.startswith("hello.")]:
        monkeypatch.delitem(sys.modules, name)
    hello_src = str(HELLO.resolve())
    monkeypatch.setattr(sys, "path", [p for p in sys.path if str(Path(p or ".").resolve()) != hello_src])
    Config.reload_from_env()
    zip_path = build_bundle(HELLO, tmp_path / "dist").zip_path
    data = zip_path.read_bytes()
    return SimpleNamespace(root=tmp_path, bundles=bundles, doc=tmp_path / "plugins.json",
                           zip=data, sha=hashlib.sha256(data).hexdigest())


def _write_index(market, *, sha: str, size: int, url: str = URL, trust: str = "third-party") -> None:
    index = {"addons": [{
        "id": "hello", "name": "Hello", "tagline": "t", "trust": trust, "languages": ["en"],
        "versions": [{
            "version": "0.1.0", "api_version": 2, "min_core": "0.0.0", "released": "2026-09-01",
            "permissions": HELLO_PERMISSIONS,
            "artifacts": [{"python": "any", "url": url, "size": size, "sha256": sha}],
        }],
    }]}
    cache = market.root / "marketplace"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "index.json").write_text(json.dumps({"fetched_at": time.time(), "index": index}), encoding="utf-8")


def _events(resp) -> list[dict]:
    return [json.loads(block[len("data: "):]) for block in resp.text.split("\n\n") if block.startswith("data: ")]


def _nothing_placed(market) -> None:
    assert not (market.bundles / "hello").exists()
    staging = market.bundles / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def _nav_ids(c) -> set[str]:
    return {i["id"] for i in c.get("/api/plugins/nav").json()["items"]}


def _evil_zip(member: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", (HELLO / "plugin.json").read_text(encoding="utf-8"))
        zf.writestr(member, "pwned")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Index install: progress in bytes, dialog data, live activation after consent
# ---------------------------------------------------------------------------

def test_index_install_reports_bytes_then_activates_live_after_consent(market):
    _write_index(market, sha=market.sha, size=len(market.zip))
    with respx.mock(assert_all_called=True) as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=market.zip))
        events = _events(c.post("/api/marketplace/install/hello", json={}))

        downloads = [e for e in events if e.get("step") == "download"]
        assert downloads[-1]["received"] == len(market.zip)
        assert downloads[-1]["total"] == len(market.zip)
        done = events[-1]
        assert done["type"] == "complete", events
        assert done["addon"]["state"] == "consent_pending"
        assert done["consent"]["origin"] == "index"
        assert done["consent"]["trust"] == "third-party"
        assert sorted(done["consent"]["missing"]) == HELLO_PERMISSIONS
        assert {p["key"] for p in done["consent"]["permissions"]} == set(HELLO_PERMISSIONS)
        assert (market.bundles / "hello" / "0.1.0" / "plugin.json").is_file()
        assert "hello" not in _nav_ids(c)

        # Zustimmen und aktivieren: the SPA's two calls.
        assert c.post("/api/plugins/hello/consent", json={"permissions": HELLO_PERMISSIONS}).status_code == 200
        state = c.post("/api/plugins/hello/enable").json()
        assert state["state"] == "active"
        assert "hello" in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").status_code == 200
        assert [a["id"] for a in c.get("/api/plugins/frontend").json()["addons"]] == ["hello"]
        consent = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]["consent"]
        assert consent == {"version": "0.1.0", "permissions": HELLO_PERMISSIONS,
                           "origin": "index", "sha256": "", "path": ""}


def test_declining_leaves_the_bundle_installed_inactive_and_consent_open(market):
    _write_index(market, sha=market.sha, size=len(market.zip))
    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=market.zip))
        _events(c.post("/api/marketplace/install/hello"))
        # Declining is: the SPA closes the dialog. Nothing else happens.
        listed = next(p for p in c.get("/api/plugins").json()["plugins"] if p["id"] == "hello")
        assert listed["state"] == "consent_pending"
        assert "hello" not in _nav_ids(c)
        card = next(a for a in c.get("/api/marketplace").json()["addons"] if a["id"] == "hello")
        assert card["installed"] is True
        assert card["addon_state"] == "consent_pending"
    assert (market.bundles / "hello" / "0.1.0").is_dir()


# ---------------------------------------------------------------------------
# Refusals: nothing at the target, one clear code each
# ---------------------------------------------------------------------------

def _install_error(market, content) -> dict:
    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=content))
        events = _events(c.post("/api/marketplace/install/hello"))
    assert events[-1]["type"] == "error", events
    return events[-1]


def test_wrong_checksum_is_refused_before_extraction(market):
    _write_index(market, sha="0" * 64, size=len(market.zip))
    assert _install_error(market, market.zip)["code"] == "error.marketplace.checksumMismatch"
    _nothing_placed(market)


@pytest.mark.parametrize("member", ["../escape.txt", "hello/../../escape.txt", "..\\escape.txt"])
def test_zip_with_parent_path_is_refused(market, member):
    evil = _evil_zip(member)
    _write_index(market, sha=hashlib.sha256(evil).hexdigest(), size=len(evil))
    error = _install_error(market, evil)
    assert error["code"] == "error.marketplace.unsafeArchive"
    _nothing_placed(market)
    assert not (market.root / "escape.txt").exists()
    assert not (market.bundles / "escape.txt").exists()


def test_artifact_larger_than_declared_is_cut_off(market):
    _write_index(market, sha=market.sha, size=100)
    assert _install_error(market, market.zip)["code"] == "error.marketplace.tooLarge"
    _nothing_placed(market)


def test_connection_drop_mid_download_leaves_no_half_folder(market):
    _write_index(market, sha=market.sha, size=len(market.zip))

    async def dropping():
        yield market.zip[:1000]
        raise httpx.ReadError("connection reset")

    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=dropping()))
        events = _events(c.post("/api/marketplace/install/hello"))
    assert events[-1]["code"] == "error.marketplace.downloadFailed"
    _nothing_placed(market)


def test_client_disconnect_mid_download_discards_the_work_folder(market, monkeypatch):
    """The browser going away cancels the stream: the generator is closed
    between two progress events, and its ``finally`` removes everything."""
    from routers import marketplace as router_module

    monkeypatch.setattr(addon_installer, "PROGRESS_STEP", 1)
    artifact = {"url": URL, "size": len(market.zip), "sha256": market.sha}

    async def slow():
        for i in range(0, len(market.zip), 512):
            yield market.zip[i:i + 512]

    async def run():
        with respx.mock() as mock:
            mock.get(URL).mock(return_value=httpx.Response(200, content=slow()))
            gen = router_module._install_events("hello", "0.1.0", artifact, len(market.zip), "official")
            seen = [await gen.__anext__() for _ in range(3)]
            await gen.aclose()
        return seen

    seen = asyncio.run(run())
    assert any('"received": 512' in s for s in seen)
    _nothing_placed(market)


# ---------------------------------------------------------------------------
# The pure member check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["/etc/passwd", "\\windows\\x", "C:/x.txt", "C:x.txt", "a/../../b", "a\x00b", ""])
def test_unsafe_member_names(name):
    assert addon_install.member_parts(name) is None


def test_symlink_member_is_unsafe():
    assert addon_install.member_parts("hello/link", external_attr=0o120777 << 16) is None
    assert addon_install.member_parts("hello/file.py", external_attr=0o100644 << 16) == ("hello", "file.py")


def test_archive_without_manifest_is_not_a_bundle():
    members = [addon_install.Member("hello/__init__.py", 0, 10, False)]
    assert addon_install.check_members(members).code == "error.marketplace.manifestMissing"


# ---------------------------------------------------------------------------
# From a file, from a folder
# ---------------------------------------------------------------------------

def test_install_from_file_returns_the_computed_checksum_and_asks_for_consent(market):
    with TestClient(webapp.app) as c:
        resp = c.post("/api/marketplace/install-file",
                      files={"file": ("hello-0.1.0.zip", market.zip, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["consent"]["sha256"] == market.sha
    assert body["consent"]["origin"] == "file"
    assert body["consent"]["trust"] == "third-party"
    assert body["consent"]["confirm"] is True
    # Off until the consent call - the dialog comes first.
    assert body["addon"]["enabled"] is False
    assert body["addon"]["needs_confirmation"] is True
    assert (market.bundles / "hello" / "0.1.0" / "plugin.json").is_file()


def test_install_from_file_refuses_a_traversal_zip(market):
    with TestClient(webapp.app) as c:
        resp = c.post("/api/marketplace/install-file",
                      files={"file": ("evil.zip", _evil_zip("../evil.txt"), "application/zip")})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.marketplace.unsafeArchive"
    _nothing_placed(market)


def test_load_from_folder_writes_the_dev_path_and_shows_under_development(market):
    with TestClient(webapp.app) as c:
        resp = c.post("/api/marketplace/dev-path", json={"path": str(HELLO)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["consent"]["origin"] == "dev"
        assert resp.json()["consent"]["confirm"] is True
        assert resp.json()["addon"]["enabled"] is False
        card = next(a for a in c.get("/api/marketplace").json()["addons"] if a["id"] == "hello")
    assert card["source"] == "dev"
    assert card["state"] == "dev"
    doc = json.loads(market.doc.read_text(encoding="utf-8"))
    assert str(HELLO.resolve()) in doc["dev_paths"]


# ---------------------------------------------------------------------------
# Consent belongs to a Herkunft (index / this file / this folder), not an id
# ---------------------------------------------------------------------------

def _variant(market, name: str, **manifest) -> tuple[bytes, str]:
    """``hello`` with Manifest overrides, packed as a Zip: ``(bytes, sha256)``."""
    import shutil

    folder = market.root / "variants" / name
    shutil.copytree(HELLO, folder, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    data = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    data.update(manifest)
    (folder / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    zip_bytes = build_bundle(folder, market.root / "variants" / f"{name}-dist").zip_path.read_bytes()
    return zip_bytes, hashlib.sha256(zip_bytes).hexdigest()


def _upload(c, zip_bytes: bytes):
    return c.post("/api/marketplace/install-file", files={"file": ("x.zip", zip_bytes, "application/zip")})


def _listed(c, addon_id: str = "hello") -> dict:
    return next(p for p in c.get("/api/plugins").json()["plugins"] if p["id"] == addon_id)


def _index_install_and_consent(market, c) -> None:
    _events(c.post("/api/marketplace/install/hello", json={}))
    assert c.post("/api/plugins/hello/consent", json={"permissions": HELLO_PERMISSIONS}).status_code == 200
    assert c.post("/api/plugins/hello/enable").json()["state"] == "active"


def test_a_file_without_permissions_still_needs_one_explicit_confirmation(market):
    zip_bytes, sha = _variant(market, "bare", permissions=[])
    with TestClient(webapp.app) as c:
        body = _upload(c, zip_bytes).json()
        # No Berechtigung is missing, yet the dialog is due: it carries the
        # "all rights of the app" warning and the file's checksum.
        assert body["consent"]["missing"] == []
        assert body["consent"]["confirm"] is True
        assert body["consent"]["sha256"] == sha
        assert body["addon"]["enabled"] is False
        assert "hello" not in _nav_ids(c)
        refused = c.post("/api/plugins/hello/enable")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "error.plugins.consentRequired"

        assert c.post("/api/plugins/hello/consent", json={"permissions": []}).status_code == 200
        state = c.post("/api/plugins/hello/enable").json()
        assert state["enabled"] is True and state["needs_confirmation"] is False
    consent = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]["consent"]
    assert consent["origin"] == "file" and consent["sha256"] == sha


def test_a_file_claiming_an_installed_id_does_not_inherit_its_consent(market):
    """A Zip that calls itself ``hello`` with a subset of the Berechtigungen is
    new code: staged, but asking for all it declares, and not switched to on
    the next start until the user agrees."""
    _write_index(market, sha=market.sha, size=len(market.zip))
    imposter, sha = _variant(market, "imposter", version="0.2.0", permissions=["llm"])
    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=market.zip))
        _index_install_and_consent(market, c)
        body = _upload(c, imposter).json()
        assert body["update"]["staged"] is True
        assert body["update"]["missing"] == ["llm"]
        assert body["update"]["confirm"] is True
        assert body["consent"]["origin"] == "file" and body["consent"]["sha256"] == sha
        # The official version keeps running meanwhile.
        assert _listed(c)["state"] == "active"
    with TestClient(webapp.app) as c:  # the next start: nothing switched
        assert _listed(c)["version"] == "0.1.0"
        assert _listed(c)["pending_update"]["confirm"] is True
        assert c.post("/api/plugins/hello/update/consent", json={"permissions": ["llm"]}).status_code == 200
        assert _listed(c)["state"] == "active"  # still the old code, still its consent
    with TestClient(webapp.app) as c:  # agreed: now the file's code runs, with the file's consent
        assert _listed(c)["version"] == "0.2.0"
    consent = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]["consent"]
    assert consent["origin"] == "file" and consent["sha256"] == sha and consent["permissions"] == ["llm"]


def test_a_file_installed_addons_own_update_switches_consent_only_at_restart(market):
    """The running Add-on is already file-origin (installed from a Zip, not the
    index). A second file claims the same id with another checksum and another
    Berechtigung: consenting to it via ``/update/consent`` must not touch the
    stored consent while the old file's version keeps running -- only the
    restart that applies the switch may replace origin/sha256/permissions."""
    zip_v1, sha1 = _variant(market, "own-v1", version="0.1.0", permissions=["llm"])
    zip_v2, sha2 = _variant(market, "own-v2", version="0.2.0", permissions=["llm", "network"])
    with TestClient(webapp.app) as c:
        assert _upload(c, zip_v1).status_code == 200
        assert c.post("/api/plugins/hello/consent", json={"permissions": ["llm"]}).status_code == 200
        assert c.post("/api/plugins/hello/enable").json()["state"] == "active"

        body = _upload(c, zip_v2).json()
        assert body["update"]["staged"] is True
        # Another checksum is another Herkunft: everything the new file
        # declares is unseen, not just the difference to the old permissions.
        assert sorted(body["update"]["missing"]) == ["llm", "network"]
        assert body["update"]["confirm"] is True
        assert body["consent"]["origin"] == "file" and body["consent"]["sha256"] == sha2
        assert _listed(c)["state"] == "active" and _listed(c)["version"] == "0.1.0"

        agreed = c.post("/api/plugins/hello/update/consent", json={"permissions": ["llm", "network"]})
        assert agreed.status_code == 200 and agreed.json()["restart_required"] is True
        # Still the old version running -- the consent must be exactly what it
        # was before the agreement, not the new file's.
        stored = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]
        assert stored["version"] == "0.1.0"
        assert stored["consent"]["sha256"] == sha1 and stored["consent"]["permissions"] == ["llm"]
        assert _listed(c)["state"] == "active" and _listed(c)["version"] == "0.1.0"

    with TestClient(webapp.app) as c:  # the restart applies the switch
        assert _listed(c)["version"] == "0.2.0" and _listed(c)["state"] == "active"
        stored = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]
        assert stored["consent"]["origin"] == "file" and stored["consent"]["sha256"] == sha2
        assert stored["consent"]["permissions"] == ["llm", "network"]


def test_a_zero_permission_file_claiming_an_installed_id_waits_too(market):
    _write_index(market, sha=market.sha, size=len(market.zip))
    imposter, _sha = _variant(market, "silent", version="0.2.0", permissions=[])
    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=market.zip))
        _index_install_and_consent(market, c)
        body = _upload(c, imposter).json()
        assert body["update"]["missing"] == [] and body["update"]["confirm"] is True
    with TestClient(webapp.app) as c:
        assert _listed(c)["version"] == "0.1.0"


def test_a_dev_folder_claiming_an_installed_id_does_not_inherit_its_consent(market):
    _write_index(market, sha=market.sha, size=len(market.zip))
    with respx.mock() as mock, TestClient(webapp.app) as c:
        mock.get(URL).mock(return_value=httpx.Response(200, content=market.zip))
        _index_install_and_consent(market, c)
        assert _listed(c)["dev_shadow_disabled"] is False  # active, nothing to explain yet
        body = c.post("/api/marketplace/dev-path", json={"path": str(HELLO)}).json()
        assert body["addon"]["source"] == "dev"
        assert sorted(body["consent"]["missing"]) == HELLO_PERMISSIONS
        assert body["consent"]["confirm"] is True
        assert body["addon"]["enabled"] is False
        assert "hello" not in _nav_ids(c)
        # The Marketplace can explain why the installed Bundle went dark: it
        # is an index install (`source.origin`) a same-id dev folder shadowed.
        assert body["addon"]["dev_shadow_disabled"] is True
        assert _listed(c)["dev_shadow_disabled"] is True


def test_legacy_consent_keeps_working_but_never_passes_to_a_file(market):
    """A consent recorded before Herkunft existed (an older plugins.json or a
    migrated .env) still counts for the installed Bundle; a file claiming the
    id first binds it to that Bundle, so the file gets none of it."""
    with TestClient(webapp.app) as c:
        _upload(c, market.zip)  # places hello 0.1.0 as a Bundle
    doc = json.loads(market.doc.read_text(encoding="utf-8"))
    doc["plugins"]["hello"].update({"enabled": True, "source": {},
                                    "consent": {"version": "0.1.0", "permissions": HELLO_PERMISSIONS}})
    market.doc.write_text(json.dumps(doc), encoding="utf-8")
    Config.reload_from_env()
    imposter, _sha = _variant(market, "legacy-imposter", version="0.2.0", permissions=["llm"])
    with TestClient(webapp.app) as c:
        assert _listed(c)["state"] == "active"  # legacy consent still counts
        body = _upload(c, imposter).json()
        assert body["update"]["missing"] == ["llm"] and body["update"]["confirm"] is True
    consent = json.loads(market.doc.read_text(encoding="utf-8"))["plugins"]["hello"]["consent"]
    assert consent["origin"] == "index"  # bound to the Bundle it was given for
    with TestClient(webapp.app) as c:
        assert _listed(c)["version"] == "0.1.0"


def test_load_from_folder_refuses_a_folder_without_manifest(market, tmp_path):
    with TestClient(webapp.app) as c:
        resp = c.post("/api/marketplace/dev-path", json={"path": str(tmp_path)})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.marketplace.folderNotABundle"
