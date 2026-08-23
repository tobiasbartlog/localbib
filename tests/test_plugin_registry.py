"""Tests for the Phase-0b plugin registry (Weg A).

Covers the acceptance criterion: a plugin registers a NavItem and can be
deactivated residue-free. ``registry.PluginRegistry`` methods are async, so we
drive them with ``asyncio.run`` rather than depending on pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import sys
import types

from plugin_api import NavItem, PluginManifest
from registry import PluginRegistry


def _make_dummy_module(name: str):
    """Register an in-memory plugin module so importlib can find it."""
    mod = types.ModuleType(name)

    class DummyPlugin:
        manifest = PluginManifest(id="dummy", name="Dummy", version="0.1.0")

        def __init__(self) -> None:
            self.activated = False
            self.deactivated = False

        async def activate(self, api) -> None:
            self.activated = True
            api.ui.register_nav_item(
                NavItem(id="dummy", label="Dummy", icon="<svg/>", route="/dummy", view="dummy")
            )

        async def deactivate(self) -> None:
            self.deactivated = True

    mod.plugin = DummyPlugin()
    sys.modules[name] = mod
    return mod


def test_activate_registers_nav_item():
    reg = PluginRegistry()
    mod = _make_dummy_module("dummy_plugin_a")
    asyncio.run(reg.activate("dummy_plugin_a"))
    assert [item.id for item in reg.nav_items()] == ["dummy"]
    assert reg.is_active("dummy_plugin_a")
    assert mod.plugin.activated


def test_deactivate_is_residue_free():
    reg = PluginRegistry()
    mod = _make_dummy_module("dummy_plugin_b")
    asyncio.run(reg.activate("dummy_plugin_b"))
    assert reg.nav_items()  # registered

    asyncio.run(reg.deactivate("dummy_plugin_b"))
    assert reg.nav_items() == []
    assert not reg.is_active("dummy_plugin_b")
    assert mod.plugin.deactivated


def test_activate_is_idempotent():
    reg = PluginRegistry()
    _make_dummy_module("dummy_plugin_d")
    asyncio.run(reg.activate("dummy_plugin_d"))
    asyncio.run(reg.activate("dummy_plugin_d"))  # second call must not double-register
    assert len(reg.nav_items()) == 1


def test_sync_toggles_plugin():
    reg = PluginRegistry()
    _make_dummy_module("dummy_plugin_c")
    asyncio.run(reg.sync("dummy_plugin_c", True))
    assert reg.is_active("dummy_plugin_c")
    asyncio.run(reg.sync("dummy_plugin_c", False))
    assert not reg.is_active("dummy_plugin_c")
    assert reg.nav_items() == []




def test_plugins_nav_endpoint_empty_by_default(client):
    """With every plugin disabled (default), the endpoint reports no nav items."""
    resp = client.get("/api/plugins/nav")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
