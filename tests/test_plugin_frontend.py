"""Frontend registration, backend half (issue #187, ADR-0021).

``GET /api/plugins/frontend`` names what the SPA loads per active Add-on;
``GET /plugins/<id>/static/<path>`` serves the files of that Add-on's
``frontend/`` folder and nothing else. Same seam as the loader tests: the app
as a context-managed ``TestClient`` with the ``hello`` fixture installed as a
Bundle under ``tmp_path``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import webapp
from plugin_api import load_manifest
from services import addon_frontend
from tests.test_plugin_loader import HELLO, _install, _on, _write_doc, addons  # noqa: F401  (fixture)


def _hello_entry(c) -> dict:
    return next(a for a in c.get("/api/plugins/frontend").json()["addons"] if a["id"] == "hello")


def test_frontend_list_names_script_stylesheet_locales_and_nav(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        entry = _hello_entry(c)
    assert entry["version"] == "0.1.0"
    assert entry["script"] == "/plugins/hello/static/hello.js?v=0.1.0"
    assert entry["stylesheet"] == "/plugins/hello/static/hello.css?v=0.1.0"
    assert entry["locales"] == {
        "de": "/plugins/hello/static/locales/de.json?v=0.1.0",
        "en": "/plugins/hello/static/locales/en.json?v=0.1.0",
    }
    assert entry["default_language"] == "en"
    assert entry["assets"] == []
    assert entry["nav"] == {"id": "hello", "route": "/hello", "view": "hello.main"}


def test_static_route_serves_the_frontend_files_uncached(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        script = c.get(_hello_entry(c)["script"])
        assert script.status_code == 200
        assert "registerPlugin" in script.text
        assert script.headers["content-type"].startswith("text/javascript")
        assert script.headers["cache-control"] == "no-cache"
        de = c.get("/plugins/hello/static/locales/de.json")
        assert de.status_code == 200 and de.json()["hello.view.title"] == "Hallo"


def test_static_route_refuses_anything_outside_frontend(addons):
    bundle = _install(addons.bundles / "hello" / "0.1.0")
    (addons.bundles / "secret.txt").write_text("nope", encoding="utf-8")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        for rel in ("../plugin.json", "..%2Fplugin.json", "%2E%2E/plugin.json",
                    "locales/../../plugin.json", "../../../secret.txt", "missing.js"):
            assert c.get(f"/plugins/hello/static/{rel}").status_code == 404, rel
    # The pure resolver, without the URL layer in between.
    assert addon_frontend.resolve_file(bundle, "../plugin.json") is None
    assert addon_frontend.resolve_file(bundle, str((bundle / "plugin.json").resolve())) is None
    assert addon_frontend.resolve_file(bundle, "hello.js") == (bundle / "frontend" / "hello.js").resolve()


def test_inactive_and_unknown_addons_serve_nothing(addons):
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        assert c.get("/plugins/hello/static/hello.js").status_code == 200
        c.post("/api/plugins/hello/disable")
        assert c.get("/plugins/hello/static/hello.js").status_code == 404
        assert all(a["id"] != "hello" for a in c.get("/api/plugins/frontend").json()["addons"])
        assert c.get("/plugins/nobody/static/hello.js").status_code == 404
        c.post("/api/plugins/hello/enable")
        assert c.get("/plugins/hello/static/hello.js").status_code == 200


def test_describe_drops_paths_outside_frontend_and_fixes_the_default_language():
    manifest = load_manifest(HELLO)
    import dataclasses
    spec = dataclasses.replace(manifest.frontend, assets=("frontend/lib/a.js", "plugin.json", "frontend/../x.js"),
                               locales={"de": "frontend/locales/de.json"})
    odd = dataclasses.replace(manifest, frontend=spec, default_language="fr")
    entry = addon_frontend.describe("hello", odd)
    assert entry["assets"] == ["/plugins/hello/static/lib/a.js?v=0.1.0"]
    assert entry["default_language"] == "de"
    assert addon_frontend.describe("hello", dataclasses.replace(manifest, frontend=None)) is None


def test_index_page_renders_the_load_list_for_the_boot(addons):
    """The SPA loads the Add-on scripts before it mounts its router; the list
    comes with the page, so a reload on an Add-on route needs no round trip."""
    _install(addons.bundles / "hello" / "0.1.0")
    _write_doc(addons, {"hello": _on()})
    with TestClient(webapp.app) as c:
        html = c.get("/").text
        assert "window.LB_ADDONS = [" in html
        assert "/plugins/hello/static/hello.js?v=0.1.0" in html
        c.post("/api/plugins/hello/disable")
        assert "window.LB_ADDONS = [];" in c.get("/").text
