"""Tests of the fixture Add-on — they need only ``plugin_api`` and its fakes.

The Bundle root goes on ``sys.path`` the way the core loader will put it there,
so ``import hello`` resolves to the package next to ``plugin.json``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(BUNDLE_ROOT))

import hello  # noqa: E402

from plugin_api import CoreSettings, LocalBibPlugin, Permission  # noqa: E402
from plugin_api.testing import DummyLlm, InMemoryLibrary, TempSqliteStorage, make_api  # noqa: E402

REFS = [{"citekey": "doe2024", "title": "Deep Things"}]


@pytest.fixture
def plugin():
    p = hello.HelloPlugin()
    yield p
    asyncio.run(p.deactivate())


def test_module_exposes_a_plugin_with_the_bundle_manifest():
    assert isinstance(hello.plugin, LocalBibPlugin)
    m = hello.plugin.manifest
    assert (m.id, m.version, m.api_version) == ("hello", "0.1.0", 2)
    assert m.declares("llm") and not m.declares(Permission.NETWORK)


def test_activate_registers_nav_item_and_uses_declared_services(plugin, tmp_path):
    llm = DummyLlm("Warm hello, Deep Things!")
    storage = TempSqliteStorage(tmp_path)
    api = make_api(
        plugin.manifest.permissions,
        library=InMemoryLibrary(REFS),
        llm=llm,
        storage=storage,
    )
    asyncio.run(plugin.activate(api))

    assert [n.route for n in api.ui.nav_items] == ["/hello"]
    assert plugin.greeting("doe2024") == "Warm hello, Deep Things!"
    assert llm.requests[0]["tier"] == "fast"
    assert plugin.history() == ["Warm hello, Deep Things!"]
    asyncio.run(plugin.deactivate())
    storage.close()


def test_undeclared_llm_is_none_and_the_addon_degrades(plugin):
    api = make_api(
        ["library.read", "settings.core"],
        library=InMemoryLibrary(REFS),
        llm=DummyLlm("never used"),
        core_settings=CoreSettings("", "", "de", ""),
    )
    assert api.llm is None and api.storage is None
    asyncio.run(plugin.activate(api))

    assert plugin.greeting("doe2024") == "Hallo, Deep Things!"
    assert plugin.history() == []


def test_without_settings_core_the_default_language_applies(plugin):
    api = make_api([])
    asyncio.run(plugin.activate(api))
    assert plugin.language() == "en"
    assert plugin.greeting("x2020") == "Hello, x2020!"


def test_own_setting_overrides_the_salutation(plugin):
    api = make_api([], settings={"salutation": "Ahoy"})
    asyncio.run(plugin.activate(api))
    assert plugin.greeting("x2020") == "Ahoy, x2020!"


def test_deactivate_drops_the_library_subscription(plugin):
    library = InMemoryLibrary(REFS)
    asyncio.run(plugin.activate(make_api(["library.read"], library=library)))
    library.add({"citekey": "roe2023", "title": "New"})
    assert plugin.changes == [{"type": "changed", "citekey": "roe2023"}]

    asyncio.run(plugin.deactivate())
    assert library.listener_count == 0
    with pytest.raises(RuntimeError):
        plugin.greeting("doe2024")


def test_router_answers_under_the_addon_prefix(plugin):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = make_api(["library.read"], library=InMemoryLibrary(REFS))
    asyncio.run(plugin.activate(api))
    app = FastAPI()
    for router in api.routes.routers:
        app.include_router(router)
    response = TestClient(app).get("/api/plugins/hello/greeting/doe2024")
    assert response.json() == {"text": "Hello, Deep Things!"}
