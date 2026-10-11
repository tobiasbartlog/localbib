"""ADD-4: ``SettingsApi.set`` behaves the same in the core and in the fake.

``str``, ``bool`` and ``None`` are stored as they are, ``int``/``float``
become ``str`` (what the core always kept on disk), anything else raises
``TypeError``. One set of assertions runs against both implementations.
"""
from __future__ import annotations

import pytest

import plugin_loader
from config import Config
from plugin_api import PluginManifest, setting_value
from plugin_api.testing import InMemorySettings


@pytest.fixture
def host_settings(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("PLUGINS_CONFIG_PATH", str(tmp_path / "plugins.json"))
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    Config.reload_from_env()
    yield plugin_loader.AddonSettings("contract", PluginManifest(id="contract", name="C", version="1.0.0"))
    monkeypatch.undo()
    Config.reload_from_env()


@pytest.fixture(params=["host", "fake"])
def settings(request):
    if request.param == "host":
        return request.getfixturevalue("host_settings")
    return InMemorySettings()


@pytest.mark.parametrize("value, stored", [
    ("text", "text"), ("", ""), (True, True), (False, False), (None, None),
    (3, "3"), (0, "0"), (1.5, "1.5"),
])
def test_scalars_are_stored_by_the_rule(settings, value, stored):
    settings.set("k", value)
    got = settings.get("k")
    assert got == stored and type(got) is type(stored)


@pytest.mark.parametrize("value", [[1, 2], {"a": 1}, ("t",), {1}, b"bytes", object()])
def test_anything_else_raises_type_error_and_stores_nothing(settings, value):
    settings.set("k", "before")
    with pytest.raises(TypeError):
        settings.set("k", value)
    assert settings.get("k") == "before"


def test_bool_is_not_turned_into_a_number_string():
    assert setting_value(True) is True and setting_value(False) is False
