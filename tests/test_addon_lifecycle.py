"""An installed Add-on's lifecycle: update, rollback, removal, states, restart (#193).

Seam: the FastAPI app as a context-managed ``TestClient`` — leaving the
``with`` block is the shutdown, entering a new one the next start, so a
"simulated restart" runs exactly the start-time housekeeping of the real app.
The Bundles are the contract package's ``hello`` fixture and copies of it with
another version (and, for one test, one more Berechtigung), packed by
``localbib-addon build``. Fixture ids stay generic: this file ships with the
public core.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import plugins_config
import webapp
from literature_manager import Config
from plugin_api.addon_tool import build_bundle
from routers import version as version_router
from services import addon_lifecycle, marketplace

HELLO = Path(__file__).resolve().parent.parent / "plugin_api" / "tests" / "fixtures" / "hello"
HELLO_PERMISSIONS = ["library.read", "llm", "settings.core", "storage"]


def _url(version: str) -> str:
    return f"https://downloads.example.org/hello-{version}.zip"


def _variant(tmp_path: Path, version: str, extra_permissions=()) -> Path:
    """A copy of the fixture with another version (and more Berechtigungen)."""
    folder = tmp_path / "src" / version
    shutil.copytree(HELLO, folder, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest["permissions"] = sorted(set(manifest["permissions"]) | set(extra_permissions))
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def life(tmp_path, monkeypatch):
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
    return SimpleNamespace(root=tmp_path, bundles=bundles, doc=tmp_path / "plugins.json", zips={})


def _publish(life, *sources: Path) -> None:
    """Build each source Bundle and list all of them in the index cache."""
    versions = []
    for src in sources:
        manifest = json.loads((src / "plugin.json").read_text(encoding="utf-8"))
        data = build_bundle(src, life.root / "dist").zip_path.read_bytes()
        life.zips[manifest["version"]] = data
        versions.append({
            "version": manifest["version"], "api_version": 2, "min_core": "0.0.0", "released": "2026-09-01",
            "permissions": manifest["permissions"],
            "artifacts": [{"python": "any", "url": _url(manifest["version"]), "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}],
        })
    index = {"addons": [{"id": "hello", "name": "Hello", "tagline": "t", "trust": "third-party",
                         "languages": ["en"], "versions": versions}]}
    cache = life.root / "marketplace"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "index.json").write_text(json.dumps({"fetched_at": time.time(), "index": index}), encoding="utf-8")


def _events(resp) -> list[dict]:
    return [json.loads(b[len("data: "):]) for b in resp.text.split("\n\n") if b.startswith("data: ")]


def _install(c, life, version: str) -> dict:
    with respx.mock() as mock:
        mock.get(_url(version)).mock(return_value=httpx.Response(200, content=life.zips[version]))
        events = _events(c.post("/api/marketplace/install/hello", json={"version": version}))
    assert events[-1]["type"] == "complete", events
    return events[-1]


def _install_active(c, life, version: str = "0.1.0") -> None:
    _install(c, life, version)
    assert c.post("/api/plugins/hello/consent", json={"permissions": HELLO_PERMISSIONS}).status_code == 200
    assert c.post("/api/plugins/hello/enable").json()["state"] == "active"


def _hello(c) -> dict:
    return next(p for p in c.get("/api/plugins").json()["plugins"] if p["id"] == "hello")


def _card(c) -> dict:
    return next(a for a in c.get("/api/marketplace").json()["addons"] if a["id"] == "hello")


def _running_from(version: str) -> bool:
    module = sys.modules.get("hello")
    return module is not None and f"{version}" in Path(module.__file__).parts


def _stored(life) -> dict:
    return json.loads(life.doc.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Switch
# ---------------------------------------------------------------------------

def test_switch_off_and_on_again_without_restart(life):
    _publish(life, HELLO)
    with TestClient(webapp.app) as c:
        _install_active(c, life)
        assert c.post("/api/plugins/hello/disable").json()["state"] == "inactive"
        assert _card(c)["enabled"] is False
        assert "hello" not in {i["id"] for i in c.get("/api/plugins/nav").json()["items"]}
        assert c.post("/api/plugins/hello/enable").json()["state"] == "active"
        assert _card(c)["enabled"] is True
        assert "hello" in {i["id"] for i in c.get("/api/plugins/nav").json()["items"]}


# ---------------------------------------------------------------------------
# Update without a new Berechtigung -> next start, predecessor kept, rollback
# ---------------------------------------------------------------------------

def test_update_becomes_active_on_the_next_start_and_rollback_restores_the_predecessor(life):
    _publish(life, HELLO, _variant(life.root, "0.2.0"))
    with TestClient(webapp.app) as c:
        _install_active(c, life, "0.1.0")
        card = _card(c)
        assert card["update_available"] is True and card["state"] == "update_available"

        done = _install(c, life, "0.2.0")
        assert done["update"] == {"from": "0.1.0", "to": "0.2.0", "staged": True, "missing": [],
                                  "confirm": False, "restart_required": True}
        # The old version keeps running until the restart.
        assert _hello(c)["version"] == "0.1.0" and _hello(c)["state"] == "active"
        assert _running_from("0.1.0")
        card = _card(c)
        assert card["pending_update"] == {"version": "0.2.0", "missing": [], "confirm": False,
                                          "origin": "index", "sha256": ""}
        assert card["update_available"] is False

    # An older leftover the document does not keep is removed at the start.
    (life.bundles / "hello" / "0.0.1").mkdir()
    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert (hello["version"], hello["state"]) == ("0.2.0", "active")
        assert _running_from("0.2.0")
        assert hello["previous_version"] == "0.1.0"
        assert hello["pending_update"] is None
        assert sorted(p.name for p in (life.bundles / "hello").iterdir()) == ["0.1.0", "0.2.0"]

        back = c.post("/api/plugins/hello/rollback").json()
        assert back["restart_required"] is True
        assert back["version"] == "0.2.0"  # still running until the restart

    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert (hello["version"], hello["state"]) == ("0.1.0", "active")
        assert _running_from("0.1.0")
        assert hello["previous_version"] == "0.2.0"


def test_rollback_to_a_kept_version_a_file_replaced_needs_its_own_confirmation(life):
    """Herkunft is per version folder: a file that replaces the kept
    predecessor's folder must not inherit the running index version's consent
    through a rollback."""
    _publish(life, HELLO, _variant(life.root, "0.2.0"))
    imposter_src = _variant(life.root / "imposter", "0.1.0")
    (imposter_src / "imposter.txt").write_text("not from the index", encoding="utf-8")
    imposter = build_bundle(imposter_src, life.root / "imposter-dist").zip_path.read_bytes()
    sha = hashlib.sha256(imposter).hexdigest()
    with TestClient(webapp.app) as c:
        _install_active(c, life, "0.1.0")
        _install(c, life, "0.2.0")
    with TestClient(webapp.app) as c:
        assert (_hello(c)["version"], _hello(c)["previous_version"]) == ("0.2.0", "0.1.0")
        up = c.post("/api/marketplace/install-file", files={"file": ("x.zip", imposter, "application/zip")})
        assert up.status_code == 200 and up.json()["update"]["confirm"] is True
        # The user never confirms the file, and rolls back instead.
        assert c.post("/api/plugins/hello/rollback").status_code == 200
        pending = _hello(c)["pending_update"]
        assert (pending["origin"], pending["sha256"], pending["confirm"]) == ("file", sha, True)
    with TestClient(webapp.app) as c:  # the next start: the file's code does not run
        hello = _hello(c)
        assert (hello["version"], hello["state"]) == ("0.2.0", "active")
        assert _running_from("0.2.0")
        assert hello["pending_update"]["confirm"] is True
    assert _stored(life)["plugins"]["hello"]["source"]["origin"] == "index"


def test_rollback_without_predecessor_is_refused(life):
    _publish(life, HELLO)
    with TestClient(webapp.app) as c:
        _install_active(c, life)
        resp = c.post("/api/plugins/hello/rollback")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.plugins.noPreviousVersion"


def test_rollback_unavailable_when_a_legacy_document_lost_the_predecessors_herkunft(life):
    """A ``plugins.json`` written before 4e25609 has a ``previous_version`` but
    no ``previous_source``. The card must say so (``rollback_available``
    False) instead of offering a rollback that then 409s."""
    _publish(life, HELLO, _variant(life.root, "0.2.0"))
    with TestClient(webapp.app) as c:
        _install_active(c, life, "0.1.0")
        _install(c, life, "0.2.0")
    with TestClient(webapp.app) as c:
        assert _hello(c)["previous_version"] == "0.1.0"

    doc = _stored(life)
    doc["plugins"]["hello"].pop("previous_source", None)
    life.doc.write_text(json.dumps(doc), encoding="utf-8")
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert hello["previous_version"] == "0.1.0"
        assert hello["rollback_available"] is False
        assert _card(c)["rollback_available"] is False
        resp = c.post("/api/plugins/hello/rollback")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.plugins.noPreviousVersion"


def test_rollback_available_when_the_predecessors_herkunft_is_known(life):
    _publish(life, HELLO, _variant(life.root, "0.2.0"))
    with TestClient(webapp.app) as c:
        _install_active(c, life, "0.1.0")
        _install(c, life, "0.2.0")
    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert hello["previous_version"] == "0.1.0"
        assert hello["rollback_available"] is True
        assert _card(c)["rollback_available"] is True
        assert c.post("/api/plugins/hello/rollback").status_code == 200


# ---------------------------------------------------------------------------
# Update with a new Berechtigung -> only the difference, old version runs on
# ---------------------------------------------------------------------------

def test_update_with_a_new_permission_asks_for_the_difference_and_waits(life):
    _publish(life, HELLO, _variant(life.root, "0.2.0", extra_permissions=["network"]))
    with TestClient(webapp.app) as c:
        _install_active(c, life, "0.1.0")
        done = _install(c, life, "0.2.0")
        assert done["update"]["missing"] == ["network"]
        assert done["consent"]["update"] is True
        assert [p["key"] for p in done["consent"]["permissions"]] == ["network"]
        assert _card(c)["pending_update"] == {"version": "0.2.0", "missing": ["network"], "confirm": True,
                                              "origin": "index", "sha256": ""}

    # Restarted without consent: the old version runs on, the update waits.
    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert (hello["version"], hello["state"]) == ("0.1.0", "active")
        assert hello["pending_update"] == {"version": "0.2.0", "missing": ["network"], "confirm": True,
                                           "origin": "index", "sha256": ""}
        assert (life.bundles / "hello" / "0.2.0").is_dir()

        stale = c.post("/api/plugins/hello/update/consent", json={"permissions": []})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "error.plugins.consentStale"
        agreed = c.post("/api/plugins/hello/update/consent", json={"permissions": ["network"]}).json()
        assert agreed["restart_required"] is True
        assert agreed["state"] == "active" and agreed["version"] == "0.1.0"

    with TestClient(webapp.app) as c:
        hello = _hello(c)
        assert (hello["version"], hello["state"]) == ("0.2.0", "active")
        assert _stored(life)["plugins"]["hello"]["consent"]["permissions"] == sorted(HELLO_PERMISSIONS + ["network"])


# ---------------------------------------------------------------------------
# Removal: the Bundle goes on the next start, the Add-on's data stays
# ---------------------------------------------------------------------------

def test_removal_deletes_the_bundle_on_the_next_start_and_keeps_the_data(life):
    _publish(life, HELLO)
    data = life.root / "addon-data" / "hello"
    data.mkdir(parents=True)
    (data / "greetings.md").write_text("keep me", encoding="utf-8")
    with TestClient(webapp.app) as c:
        _install_active(c, life)
        c.put("/api/plugins/hello/settings", json={"values": {"greetings_folder": str(data)}})
        removed = c.post("/api/plugins/hello/remove").json()
        assert removed["restart_required"] is True
        assert removed["state"] == "inactive" and removed["pending_removal"] is True
        assert _card(c)["pending_removal"] is True
        assert "hello" not in {i["id"] for i in c.get("/api/plugins/nav").json()["items"]}
        assert (life.bundles / "hello").is_dir()  # Windows would still hold it

    with TestClient(webapp.app) as c:
        assert all(p["id"] != "hello" for p in c.get("/api/plugins").json()["plugins"])
        assert _card(c)["installed"] is False
    assert not (life.bundles / "hello").exists()
    assert (data / "greetings.md").read_text(encoding="utf-8") == "keep me"
    stored = _stored(life)
    assert stored["pending_removals"] == []
    assert stored["plugins"]["hello"]["settings"] == {"greetings_folder": str(data)}
    assert stored["plugins"]["hello"]["consent"]["permissions"] == []


def test_removing_a_dev_path_never_deletes_the_source_folder(life):
    with TestClient(webapp.app) as c:
        assert c.post("/api/marketplace/dev-path", json={"path": str(HELLO)}).status_code == 200
        resp = c.post("/api/plugins/hello/remove").json()
        assert resp["restart_required"] is False
        assert all(p["id"] != "hello" for p in c.get("/api/plugins").json()["plugins"])
    assert (HELLO / "plugin.json").is_file()


def test_a_dev_path_from_the_environment_cannot_be_removed_only_switched_off(life, monkeypatch):
    monkeypatch.setenv("LOCALBIB_PLUGIN_DEV_PATHS", str(HELLO))
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        assert _hello(c)["dev_origin"] == "env"
        assert _card(c)["dev_origin"] == "env"
        resp = c.post("/api/plugins/hello/remove")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "error.plugins.devPathFromEnv"
        assert _hello(c)["source"] == "dev"  # still there
        assert c.post("/api/plugins/hello/disable").json()["enabled"] is False


def test_removing_a_dev_path_keeps_the_switch_of_the_bundle_it_shadowed(life):
    _publish(life, HELLO)
    dev = _variant(life.root, "0.3.0")
    with TestClient(webapp.app) as c:
        _install_active(c, life)
    doc = _stored(life)
    doc["dev_paths"] = [str(dev)]
    life.doc.write_text(json.dumps(doc), encoding="utf-8")
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        assert _hello(c)["source"] == "dev" and _hello(c)["dev_origin"] == "document"
        assert c.post("/api/plugins/hello/remove").json()["removed"] is True
        hello = _hello(c)
        assert hello["source"] == "bundle"
        assert hello["enabled"] is True and hello["state"] == "active"
    assert _stored(life)["dev_paths"] == []


# ---------------------------------------------------------------------------
# States on the card: error, boot marker, incompatible with an update offer
# ---------------------------------------------------------------------------

def test_boot_marker_shows_as_error_on_the_card(life):
    _publish(life, HELLO)
    with TestClient(webapp.app) as c:
        _install_active(c, life)
    doc = _stored(life)
    doc["boot_marker"] = "hello"
    life.doc.write_text(json.dumps(doc), encoding="utf-8")
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        card = _card(c)
    assert card["addon_state"] == "error"
    assert card["enabled"] is False
    assert card["error"]["code"] == "error.plugins.crashedDuringLoad"


def test_incompatible_bundle_is_offered_the_fitting_update(life):
    _publish(life, HELLO)
    old = life.bundles / "hello" / "0.0.5"
    shutil.copytree(HELLO, old, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    manifest = json.loads((old / "plugin.json").read_text(encoding="utf-8"))
    manifest.update(version="0.0.5", api_version=1)
    (old / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    doc = plugins_config.empty_document()
    entry = plugins_config.ensure_entry(doc, "hello")
    entry.update(version="0.0.5", enabled=True, consent={"version": "0.0.5", "permissions": HELLO_PERMISSIONS})
    plugins_config.save(str(life.doc), doc)
    Config.reload_from_env()
    with TestClient(webapp.app) as c:
        card = _card(c)
        assert card["addon_state"] == "incompatible"
        assert card["installed_version"] == "0.0.5"
        assert card["update_available"] is True and card["offered_version"] == "0.1.0"
        # Not running, so the update switches at once — no restart needed.
        done = _install(c, life, "0.1.0")
        assert done["update"]["staged"] is False and done["update"]["restart_required"] is False
        assert _hello(c)["state"] == "active"


# ---------------------------------------------------------------------------
# Restart endpoint
# ---------------------------------------------------------------------------

def _restart(monkeypatch, *, frozen: bool):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(version_router, "EXIT_DELAY_SECONDS", 0)
    calls = {"launched": 0, "exited": 0}
    monkeypatch.setattr(version_router, "_relaunch_self", lambda: calls.__setitem__("launched", calls["launched"] + 1))
    monkeypatch.setattr(version_router, "_exit_app", lambda: calls.__setitem__("exited", calls["exited"] + 1))
    return TestClient(webapp.app).post("/api/app/restart"), calls


def test_restart_relaunches_the_exe_and_exits_when_frozen(monkeypatch):
    resp, calls = _restart(monkeypatch, frozen=True)
    assert resp.status_code == 200 and resp.json()["status"] == "restarting"
    assert calls == {"launched": 1, "exited": 1}


def test_restart_is_not_available_from_source(monkeypatch):
    resp, calls = _restart(monkeypatch, frozen=False)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.restart_unavailable"
    assert calls == {"launched": 0, "exited": 0}


def test_restarted_process_waits_for_the_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        started = time.monotonic()
        assert webapp.wait_for_free_port("127.0.0.1", port, timeout=0.5) is False
        assert time.monotonic() - started >= 0.5
    assert webapp.wait_for_free_port("127.0.0.1", port, timeout=5) is True


# ---------------------------------------------------------------------------
# The pure rules
# ---------------------------------------------------------------------------

def _doc(**entry) -> dict:
    doc = plugins_config.empty_document()
    plugins_config.ensure_entry(doc, "a").update(entry)
    return doc


def test_switch_of_a_running_addon_waits_for_the_next_start():
    doc = _doc(version="1.0.0", consent={"version": "1.0.0", "permissions": ["llm"]})
    addon_lifecycle.switch_version(doc, "a", "2.0.0", ["llm"], running=True)
    assert doc["plugins"]["a"]["version"] == "1.0.0"
    assert addon_lifecycle.apply_pending(doc) == ["a"]
    assert (doc["plugins"]["a"]["version"], doc["plugins"]["a"]["previous_version"]) == ("2.0.0", "1.0.0")


def test_pending_update_without_consent_stays_pending():
    doc = _doc(version="1.0.0", consent={"version": "1.0.0", "permissions": ["llm"]})
    addon_lifecycle.switch_version(doc, "a", "2.0.0", ["llm", "network"], running=True)
    assert addon_lifecycle.pending_missing(doc["plugins"]["a"]) == ["network"]
    assert addon_lifecycle.apply_pending(doc) == []
    assert addon_lifecycle.agree_to_update(doc, "a", ["network"]) == []
    assert doc["plugins"]["a"]["consent"]["permissions"] == ["llm", "network"]
    assert addon_lifecycle.apply_pending(doc) == ["a"]


INDEX = {"origin": "index", "sha256": ""}
FILE = {"origin": "file", "sha256": "f" * 64}


def _index_running(**extra) -> dict:
    """``a`` 0.3.0 from the index runs with index consent; 0.2.0 (index) kept."""
    fields = {"version": "0.3.0", "source": dict(INDEX), "previous_version": "0.2.0",
              "previous_source": dict(INDEX),
              "consent": {**plugins_config.empty_consent(), "version": "0.3.0", "permissions": ["llm"],
                          "origin": "index"}}
    return _doc(**{**fields, **extra})


def test_a_switch_keeps_the_predecessors_herkunft():
    doc = _doc(version="1.0.0", source=dict(FILE))
    addon_lifecycle.switch_version(doc, "a", "2.0.0", [], running=False, source=INDEX)
    entry = doc["plugins"]["a"]
    assert (entry["previous_version"], entry["previous_source"]) == ("1.0.0", FILE)
    assert entry["source"] == INDEX


@pytest.mark.parametrize("running", [True, False])
def test_rollback_to_a_folder_a_file_replaced_does_not_inherit_the_index_consent(running):
    doc = _index_running()
    # A file claiming the kept 0.2.0 replaced its folder; the user never confirmed it.
    addon_lifecycle.record_placed(doc, "a", "0.2.0", FILE)
    addon_lifecycle.switch_version(doc, "a", "0.2.0", ["llm"], running=True, source=FILE)
    entry = doc["plugins"]["a"]
    assert addon_lifecycle.pending_needs_confirmation(entry) is True
    # Rollback: the predecessor's own Herkunft, never the running version's.
    target = addon_lifecycle.rollback_target(entry, ["0.2.0", "0.3.0"])
    addon_lifecycle.switch_version(doc, "a", target, sorted(addon_lifecycle.granted(entry)), running=running)
    if running:
        assert addon_lifecycle.pending_provenance(entry)["origin"] == "file"
        assert addon_lifecycle.pending_needs_confirmation(entry) is True
        assert addon_lifecycle.apply_pending(doc) == []
        assert entry["version"] == "0.3.0"
    else:
        assert entry["source"] == FILE
        assert not plugins_config.consent_binds(entry["consent"], plugins_config.source_provenance(entry))


def test_rolling_back_a_file_update_restores_the_index_predecessors_herkunft():
    doc = _index_running()
    addon_lifecycle.switch_version(doc, "a", "0.4.0", ["llm"], running=False, source=FILE)
    entry = doc["plugins"]["a"]
    assert entry["previous_source"] == INDEX
    addon_lifecycle.switch_version(doc, "a", "0.3.0", ["llm"], running=False)
    assert entry["source"] == INDEX and entry["previous_source"] == FILE


def test_a_predecessor_of_unknown_herkunft_is_no_rollback_target():
    doc = _index_running(previous_source={"origin": "", "sha256": ""})
    with pytest.raises(ValueError, match="noPreviousVersion"):
        addon_lifecycle.rollback_target(doc["plugins"]["a"], ["0.2.0", "0.3.0"])
    with pytest.raises(ValueError):
        addon_lifecycle.switch_version(doc, "a", "0.2.0", ["llm"], running=True)
    assert doc["plugins"]["a"]["pending_update"] is None


def test_placing_the_kept_version_forgets_or_replaces_its_herkunft():
    doc = _index_running()
    assert addon_lifecycle.record_placed(doc, "a", "0.2.0", None) is True
    assert not addon_lifecycle.previous_provenance(doc["plugins"]["a"])
    assert addon_lifecycle.record_placed(doc, "a", "0.2.0", FILE) is True
    assert doc["plugins"]["a"]["previous_source"] == FILE
    # Any other folder is not the kept predecessor.
    assert addon_lifecycle.record_placed(doc, "a", "0.3.0", FILE) is False
    assert addon_lifecycle.record_placed(doc, "a", "0.9.0", FILE) is False


def test_the_predecessors_herkunft_survives_a_save():
    doc = _index_running(previous_source=dict(FILE))
    assert plugins_config.normalize(doc)["plugins"]["a"]["previous_source"] == FILE
    doc["plugins"]["a"]["previous_source"] = {"origin": "bogus"}
    assert plugins_config.normalize(doc)["plugins"]["a"]["previous_source"] == {"origin": "", "sha256": ""}


def test_prune_keeps_exactly_one_predecessor_and_unknown_addons():
    doc = _doc(version="3.0.0", previous_version="2.0.0")
    doc["pending_removals"] = ["gone"]
    removals, versions = addon_lifecycle.prune_plan(doc, {
        "a": ["1.0.0", "2.0.0", "3.0.0"], "gone": ["1.0.0"], "unknown": ["0.1.0", "0.2.0"],
    })
    assert removals == ["gone"]
    assert versions == [("a", "1.0.0")]


def test_forgetting_a_removed_addon_keeps_only_its_settings():
    doc = _doc(version="1.0.0", enabled=True, settings={"base_dir": "D:/data"},
               consent={"version": "1.0.0", "permissions": ["files"]})
    addon_lifecycle.mark_removal(doc, "a")
    addon_lifecycle.forget_after_removal(doc, "a")
    assert doc["pending_removals"] == []
    assert doc["plugins"]["a"]["settings"] == {"base_dir": "D:/data"}
    assert doc["plugins"]["a"]["version"] == "" and doc["plugins"]["a"]["consent"]["permissions"] == []


def test_card_with_a_pending_update_is_not_offered_it_again():
    from services.addon_catalog import CoreFacts

    facts = CoreFacts(api_version=2, python_tag="cp313-win_amd64")
    entry = {"id": "a", "versions": [{"version": "2.0.0", "api_version": 2,
                                      "artifacts": [{"python": "any", "url": "https://x", "sha256": "s"}]}]}
    card = marketplace.build_card(entry, facts, {
        "version": "1.0.0", "source": "bundle", "state": "active", "enabled": True,
        "pending_update": {"version": "2.0.0", "missing": []}, "previous_version": "0.9.0",
    })
    assert card["update_available"] is False
    assert card["pending_update"] == {"version": "2.0.0", "missing": [], "confirm": False,
                                      "origin": "index", "sha256": ""}
    assert card["previous_version"] == "0.9.0" and card["enabled"] is True


def test_lifecycle_fields_reports_rollback_available_from_the_flag_it_is_given():
    """``lifecycle_fields`` trusts the ``rollback_available`` flag its caller
    computed (``plugin_loader.describe_one``, from ``previous_provenance``) —
    it never re-derives it from ``previous_version`` alone."""
    from services.marketplace import lifecycle_fields

    unavailable = lifecycle_fields({"previous_version": "0.9.0", "rollback_available": False})
    assert unavailable["previous_version"] == "0.9.0"
    assert unavailable["rollback_available"] is False

    available = lifecycle_fields({"previous_version": "0.9.0", "rollback_available": True})
    assert available["rollback_available"] is True
