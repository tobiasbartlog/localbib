"""Bundle loader, plugins.json and the Add-on endpoints (issue #186, ADR-0021).

Seam: the FastAPI app as a context-managed ``TestClient`` (startup loads,
shutdown unloads). Add-ons come from two places — a Bundle root and a
Dev-Suchpfad, both redirected into ``tmp_path`` — and are switched through
``plugins.json`` or the endpoints. The ``hello`` fixture of the contract
package is the well-behaved Add-on; the broken ones are written per test.
"""
from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import plugin_loader
import webapp
from literature_manager import Config

HELLO = Path(__file__).resolve().parent.parent / "plugin_api" / "tests" / "fixtures" / "hello"
HELLO_PERMISSIONS = ["library.read", "llm", "settings.core", "storage"]


@pytest.fixture
def addons(tmp_path, monkeypatch):
    """Bundle root, plugins.json and .env all under tmp_path."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCALBIB_PLUGIN_DIR", str(bundles))
    monkeypatch.setenv("PLUGINS_CONFIG_PATH", str(tmp_path / "plugins.json"))
    monkeypatch.delenv("LOCALBIB_PLUGIN_DEV_PATHS", raising=False)
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    # The contract package's own tests import the fixture as ``hello`` from
    # its source folder; the loader would rightly refuse that as a module
    # conflict (or find the source copy first on sys.path). Hide both for this
    # test; monkeypatch puts them back.
    for name in [m for m in sys.modules if m == "hello" or m.startswith("hello.")]:
        monkeypatch.delitem(sys.modules, name)
    hello_src = str(HELLO.resolve())
    monkeypatch.setattr(sys, "path", [p for p in sys.path if str(Path(p or ".").resolve()) != hello_src])
    Config.reload_from_env()
    return SimpleNamespace(root=tmp_path, bundles=bundles, env=env, doc=tmp_path / "plugins.json")


def _install(target: Path, source: Path = HELLO) -> Path:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return target


def _write_doc(addons, plugins: dict, **top) -> None:
    addons.doc.write_text(json.dumps({"version": 1, "plugins": plugins, **top}), encoding="utf-8")
    Config.reload_from_env()


def _on(permissions=HELLO_PERMISSIONS, **extra) -> dict:
    return {"enabled": True, "consent": {"version": "0.1.0", "permissions": list(permissions)}, **extra}


def _read_doc(addons) -> dict:
    return json.loads(addons.doc.read_text(encoding="utf-8"))


def _make_addon(folder: Path, addon_id: str, init_code: str, **manifest) -> Path:
    """A Bundle folder: hello's Manifest with overrides, plus a package."""
    data = json.loads((HELLO / "plugin.json").read_text(encoding="utf-8"))
    data.pop("nav", None)
    data.update({"id": addon_id, "name": addon_id.title(), "permissions": [], "settings": []})
    data.update(manifest)
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / addon_id).mkdir()
    (folder / addon_id / "__init__.py").write_text(textwrap.dedent(init_code), encoding="utf-8")
    return folder


PROBE_CODE = '''
from fastapi import APIRouter
from plugin_api import PluginManifest


class Probe:
    manifest = PluginManifest(id="{id}", name="Probe", version="0.1.0")

    async def activate(self, api):
        router = APIRouter(prefix="{prefix}")

        @router.get("/facts")
        def facts():
            core = api.settings.core() if api.settings is not None else None
            return {{
                "llm": api.llm is not None,
                "library": api.library is not None,
                "mailto": core.mailto if core is not None else None,
                "token": api.settings.get("token") if api.settings is not None else None,
            }}

        api.routes.register_router(router)

    async def deactivate(self):
        pass


plugin = Probe()
'''


def _probe(folder: Path, addon_id: str = "probe", prefix: str = "", **manifest) -> Path:
    code = PROBE_CODE.format(id=addon_id, prefix=prefix or f"/api/plugins/{addon_id}")
    return _make_addon(folder, addon_id, code, **manifest)


def _nav_ids(c) -> set[str]:
    return {i["id"] for i in c.get("/api/plugins/nav").json()["items"]}


def _state(c, addon_id: str) -> dict:
    return next(p for p in c.get("/api/plugins").json()["plugins"] if p["id"] == addon_id)


# ---------------------------------------------------------------------------
# Loading from a Bundle and from a Dev-Suchpfad
# ---------------------------------------------------------------------------

def test_bundle_addon_loads_and_unloads_without_restart(addons):
    bundle = _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on(version="0.1.0")})
    path_before = list(sys.path)

    with TestClient(webapp.app) as c:
        assert "hello" in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").json() == {"greetings": []}
        state = _state(c, "hello")
        assert (state["state"], state["source"]) == ("active", "bundle")
        # Exactly two module-search-path entries: the Bundle and its vendor/.
        assert [p for p in sys.path if p not in path_before] == [str(bundle), str(bundle / "vendor")]

        assert c.post("/api/plugins/hello/disable").json()["state"] == "inactive"
        assert "hello" not in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").status_code == 404
        assert "hello" not in sys.modules
        assert sys.path == path_before

        assert c.post("/api/plugins/hello/enable").json()["state"] == "active"
        assert "hello" in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").status_code == 200

    assert "hello" not in sys.modules
    assert sys.path == path_before


def test_mounting_addon_routers_does_not_nest_the_app_lifespan(addons):
    """FastAPI's include_router wraps the app lifespan per mount; unguarded,
    every activation nested one level deeper until startup hit the recursion
    limit after a few hundred toggles."""
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    lifespan = webapp.app.router.lifespan_context
    with TestClient(webapp.app) as c:
        for _ in range(3):
            c.post("/api/plugins/hello/disable")
            c.post("/api/plugins/hello/enable")
    assert webapp.app.router.lifespan_context is lifespan


@pytest.mark.parametrize("frozen", [False, True])
def test_dev_path_addon_loads_the_same_in_source_and_frozen_mode(addons, monkeypatch, frozen):
    source = _install(addons.root / "src" / "hello-addon")
    monkeypatch.setenv("LOCALBIB_PLUGIN_DEV_PATHS", str(source))
    monkeypatch.setattr(plugin_loader, "is_frozen", lambda: frozen)
    monkeypatch.setattr(plugin_loader, "_core_version", "0.9.0")
    _write_doc(addons, {"hello": _on()})

    with TestClient(webapp.app) as c:
        assert "hello" in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").status_code == 200
        state = _state(c, "hello")
        assert (state["state"], state["source"]) == ("active", "dev")
        assert c.get("/api/plugins").json()["core"]["frozen"] is frozen

        c.post("/api/plugins/hello/disable")
        assert "hello" not in _nav_ids(c)
        assert c.get("/api/plugins/hello/history").status_code == 404


def test_dev_paths_from_the_document_are_searched_too(addons):
    source = _install(addons.root / "src" / "hello-addon")
    _write_doc(addons, {"hello": _on()}, dev_paths=[str(source)])
    with TestClient(webapp.app) as c:
        assert _state(c, "hello")["source"] == "dev"
        assert "hello" in _nav_ids(c)


def test_min_core_is_enforced_only_in_a_frozen_build(addons, monkeypatch):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    monkeypatch.setattr(plugin_loader, "_core_version", "0.7.0")  # hello needs 0.8.0
    monkeypatch.setattr(plugin_loader, "is_frozen", lambda: True)
    with TestClient(webapp.app) as c:
        state = _state(c, "hello")
        assert state["state"] == "incompatible"
        assert state["error"]["code"] == "error.plugins.coreTooOld"
    monkeypatch.setattr(plugin_loader, "is_frozen", lambda: False)
    with TestClient(webapp.app) as c:
        assert _state(c, "hello")["state"] == "active"


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

def test_broken_import_is_an_error_state_and_leaves_the_others_running(addons, monkeypatch):
    _install(addons.bundles / "hello" / "0.1.0")
    _make_addon(addons.bundles / "broken" / "1.0.0", "broken", "raise ImportError('vendored lib missing')\n")
    _write_doc(addons, {"hello": _on(), "broken": _on([])})

    with TestClient(webapp.app) as c:
        broken = _state(c, "broken")
        assert broken["state"] == "error"
        assert broken["error"]["code"] == "error.plugins.importFailed"
        assert "vendored lib missing" in broken["error"]["message"]
        # The first Add-on is untouched and its router was reconciled.
        assert _state(c, "hello")["state"] == "active"
        assert c.get("/api/plugins/hello/history").status_code == 200
        assert "broken" not in sys.modules
        assert not any(p.startswith(str(addons.bundles / "broken")) for p in sys.path)

    assert _read_doc(addons)["plugins"]["broken"]["error"]["code"] == "error.plugins.importFailed"


@pytest.mark.parametrize("manifest,code", [
    ({"api_version": 1}, "error.plugins.contractMismatch"),
    ({"python": "cp27-nowhere_x86"}, "error.plugins.pythonMismatch"),
])
def test_incompatible_addon_is_never_imported(addons, manifest, code):
    _make_addon(addons.bundles / "oldie" / "1.0.0", "oldie", "raise RuntimeError('must not be imported')\n", **manifest)
    _write_doc(addons, {"oldie": _on([])})
    with TestClient(webapp.app) as c:
        state = _state(c, "oldie")
        assert state["state"] == "incompatible"
        assert state["error"]["code"] == code
        assert "oldie" not in sys.modules
        resp = c.post("/api/plugins/oldie/enable")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "error.plugins.incompatible"


def test_routes_outside_the_addon_prefix_are_refused(addons):
    _probe(addons.bundles / "probe" / "0.1.0", prefix="/api/elsewhere")
    _write_doc(addons, {"probe": _on([])})
    with TestClient(webapp.app) as c:
        state = _state(c, "probe")
        assert state["state"] == "error"
        assert state["error"]["code"] == "error.plugins.routePrefix"
        assert c.get("/api/elsewhere/facts").status_code == 404


def test_crash_during_load_switches_the_addon_off_on_the_next_start(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    # What a crash mid-import leaves behind: the marker the loader wrote first.
    _write_doc(addons, {"hello": _on()}, boot_marker="hello")
    with TestClient(webapp.app) as c:
        state = _state(c, "hello")
        assert state["enabled"] is False
        assert state["error"]["code"] == "error.plugins.crashedDuringLoad"
        assert "hello" not in _nav_ids(c)
    doc = _read_doc(addons)
    assert doc["boot_marker"] == ""
    assert doc["plugins"]["hello"]["enabled"] is False


def test_the_boot_marker_is_cleared_after_a_good_load(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app):
        assert _read_doc(addons)["boot_marker"] == ""


# ---------------------------------------------------------------------------
# Permissions, consent, settings
# ---------------------------------------------------------------------------

def test_host_services_follow_the_declared_permissions(addons):
    _probe(addons.bundles / "plain" / "0.1.0", "plain")
    _probe(addons.bundles / "trusted" / "0.1.0", "trusted", permissions=["llm", "settings.core"])
    _write_doc(addons, {"plain": _on([]), "trusted": _on(["llm", "settings.core"])})
    with TestClient(webapp.app) as c:
        assert c.get("/api/plugins/plain/facts").json() == {
            "llm": False, "library": False, "mailto": None, "token": None}
        trusted = c.get("/api/plugins/trusted/facts").json()
        assert trusted["llm"] is True
        assert trusted["library"] is False
        assert trusted["mailto"] == Config.polite_mailto() != ""


WRITER_CODE = '''
from fastapi import APIRouter
from plugin_api import PluginManifest


class Writer:
    manifest = PluginManifest(id="{id}", name="Writer", version="0.1.0")

    async def activate(self, api):
        router = APIRouter(prefix="/api/plugins/{id}")

        @router.post("/create")
        def create(doi: str):
            try:
                return api.library.create_by_doi(doi, title="Via contract", authors=["Doe, Jane"])
            except PermissionError:
                return {{"denied": True}}

        api.routes.register_router(router)

    async def deactivate(self):
        pass


plugin = Writer()
'''


def test_create_by_doi_needs_library_write(addons, db):
    reader, writer = ["library.read"], ["library.read", "library.write"]
    for addon_id, perms in (("reader", reader), ("writer", writer)):
        _make_addon(addons.bundles / addon_id / "0.1.0", addon_id,
                    WRITER_CODE.format(id=addon_id), permissions=perms)
    _write_doc(addons, {"reader": _on(reader), "writer": _on(writer)})
    with TestClient(webapp.app) as c:
        assert c.post("/api/plugins/reader/create", params={"doi": "10.5555/r"}).json() == {"denied": True}
        body = c.post("/api/plugins/writer/create", params={"doi": "https://doi.org/10.5555/W"}).json()
        assert body["created"] is True and body["doi"] == "10.5555/w" and body["citekey"]
        again = c.post("/api/plugins/writer/create", params={"doi": "10.5555/w"}).json()
        assert again["created"] is False and again["paper_id"] == body["paper_id"]
        # The same intake as POST /api/papers/by-doi: the REST path sees the Item.
        exists = c.post("/api/papers/by-doi/exists", json={"dois": ["10.5555/r", "10.5555/w"]}).json()
        assert exists["papers"] == {"10.5555/r": None, "10.5555/w": body["paper_id"]}
    conn = db._connect()
    try:
        row = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (body["paper_id"],)).fetchone())
    finally:
        conn.close()
    assert (row["title"], row["authors"], row["import_source"]) == ("Via contract", "Doe, Jane", "writer")
    assert row["cite_key"] == body["citekey"]


def test_enabling_needs_consent_to_every_permission(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    with TestClient(webapp.app) as c:
        assert _state(c, "hello")["missing_consent"] == sorted(HELLO_PERMISSIONS)
        resp = c.post("/api/plugins/hello/enable")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "error.plugins.consentRequired"

        stale = c.post("/api/plugins/hello/consent", json={"permissions": ["llm"]})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "error.plugins.consentStale"

        assert c.post("/api/plugins/hello/consent", json={"permissions": HELLO_PERMISSIONS}).status_code == 200
        assert c.post("/api/plugins/hello/enable").json()["state"] == "active"
        assert "hello" in _nav_ids(c)


def test_enabled_addon_with_a_new_permission_waits_for_consent(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on(["llm"])})
    with TestClient(webapp.app) as c:
        state = _state(c, "hello")
        assert state["state"] == "consent_pending"
        assert state["missing_consent"] == ["library.read", "settings.core", "storage"]
        assert "hello" not in _nav_ids(c)


def test_addon_settings_mask_secrets_and_reach_the_addon(addons):
    settings = [{"key": "token", "type": "secret", "label": "probe.settings.token"},
                {"key": "greeting", "type": "string", "label": "probe.settings.greeting", "default": "hi"}]
    _probe(addons.bundles / "probe" / "0.1.0", settings=settings)
    _write_doc(addons, {"probe": _on([])})
    with TestClient(webapp.app) as c:
        got = c.get("/api/plugins/probe/settings").json()
        assert got["values"] == {"token": {"has_key": False, "key_hint": ""}, "greeting": "hi"}

        put = c.put("/api/plugins/probe/settings", json={"values": {"token": "sk-abcdefghijkl"}}).json()
        assert put["values"]["token"] == {"has_key": True, "key_hint": "sk-…ijkl"}
        assert c.get("/api/plugins/probe/facts").json()["token"] == "sk-abcdefghijkl"

        # null = unchanged; the secret never travels back to the SPA.
        c.put("/api/plugins/probe/settings", json={"values": {"token": None, "greeting": "hello"}})
        again = c.get("/api/plugins/probe/settings").json()["values"]
        assert again == {"token": {"has_key": True, "key_hint": "sk-…ijkl"}, "greeting": "hello"}
        assert "sk-abcdefghijkl" not in json.dumps(c.get("/api/plugins").json())

        bad = c.put("/api/plugins/probe/settings", json={"values": {"nope": "x"}})
        assert bad.status_code == 422
        assert bad.json()["detail"]["code"] == "error.plugins.settingUnknown"


def test_hello_settings_land_in_its_namespace_of_plugins_json(addons):
    """The fixture's declared fields (string, secret, path, bool) — what the
    SPA's generic settings section renders and PUTs (#190)."""
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        got = c.get("/api/plugins/hello/settings").json()
        assert [(f["key"], f["type"]) for f in got["fields"]] == [
            ("salutation", "string"), ("api_key", "secret"), ("greetings_folder", "path"), ("shout", "bool")]
        assert got["fields"][1]["label"] == "hello.settings.api_key"

        put = c.put("/api/plugins/hello/settings", json={"values": {
            "salutation": "Ahoy", "api_key": "hk-0123456789ab",
            "greetings_folder": "C:/greetings", "shout": True}}).json()
        assert put["values"] == {
            "salutation": "Ahoy", "api_key": {"has_key": True, "key_hint": "hk-…89ab"},
            "greetings_folder": "C:/greetings", "shout": True}

        stored = _read_doc(addons)["plugins"]["hello"]["settings"]
        assert stored == {"salutation": "Ahoy", "api_key": "hk-0123456789ab",
                          "greetings_folder": "C:/greetings", "shout": True}

        # The generic form sends the secret as null when left empty: unchanged.
        c.put("/api/plugins/hello/settings", json={"values": {"api_key": None, "shout": False}})
        stored = _read_doc(addons)["plugins"]["hello"]["settings"]
        assert stored["api_key"] == "hk-0123456789ab" and stored["shout"] is False


def test_unknown_addon_is_a_404_code(addons):
    with TestClient(webapp.app) as c:
        resp = c.post("/api/plugins/nothing_here/enable")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "error.plugins.notFound"


def test_version_check_reports_contract_and_python_tag(client, monkeypatch):
    import routers.version as version_mod
    from plugin_api import API_VERSION
    from services.addon_catalog import python_tag

    monkeypatch.setattr(version_mod, "_fetch_release", lambda: None)
    monkeypatch.setattr(version_mod, "_version_cache", {"ts": 0.0, "data": None})
    data = client.get("/api/version-check").json()
    assert data["api_version"] == API_VERSION == 2
    assert data["python_tag"] == python_tag()
    assert python_tag().startswith(f"cp{sys.version_info[0]}{sys.version_info[1]}-")


