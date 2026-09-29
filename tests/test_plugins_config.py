"""The plugins.json rule module and the pure compatibility check (#186).

The document itself is the contract here (ADR-0019 pattern): its shape, the
coercion of a damaged file, and the read-only migration of legacy keys.
"""
from __future__ import annotations

import pytest

import plugins_config
from plugin_api import PluginManifest
from services.addon_catalog import CoreFacts, check_compat


def test_damaged_document_is_coerced_not_fatal():
    doc = plugins_config.normalize({
        "plugins": {"x": {"enabled": "yes", "consent": "all", "settings": {"a": 1, "b": [1]},
                          "error": {"message": "no code"}}, "": {}},
        "dev_paths": ["/a", "/a", 3],
        "boot_marker": None,
    })
    entry = doc["plugins"]["x"]
    assert entry["enabled"] is False            # only a real true switches on
    assert entry["consent"] == plugins_config.empty_consent()
    assert entry["settings"] == {"a": "1", "b": None}
    assert entry["error"] is None
    assert list(doc["plugins"]) == ["x"]
    assert doc["dev_paths"] == ["/a"]
    assert doc["boot_marker"] == ""


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "plugins.json"
    doc = plugins_config.empty_document()
    plugins_config.ensure_entry(doc, "x")["settings"]["k"] = "v"
    plugins_config.save(str(path), doc)
    assert plugins_config.normalize(plugins_config.load(str(path))) == plugins_config.normalize(doc)
    path.write_text("{not json", encoding="utf-8")
    assert plugins_config.load(str(path)) is None


def _flaky_replace(monkeypatch, failures: int) -> list:
    """os.replace that raises WinError 5 ``failures`` times, then works —
    a scanner briefly holding the file. Returns the list of sleeps."""
    real_replace = plugins_config.os.replace
    calls = {"n": 0}
    sleeps: list = []

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(13, "Access is denied", dst)
        real_replace(src, dst)

    monkeypatch.setattr(plugins_config.os, "replace", replace)
    monkeypatch.setattr(plugins_config.time, "sleep", sleeps.append)
    return sleeps


def test_save_retries_a_briefly_locked_file(tmp_path, monkeypatch):
    sleeps = _flaky_replace(monkeypatch, failures=2)
    path = tmp_path / "plugins.json"
    doc = plugins_config.empty_document()
    plugins_config.ensure_entry(doc, "x")["enabled"] = True
    plugins_config.save(str(path), doc)
    assert plugins_config.load(str(path))["plugins"]["x"]["enabled"] is True
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]
    assert [p.name for p in tmp_path.iterdir()] == ["plugins.json"]   # no temp file left


def test_save_gives_up_after_bounded_retries_and_cleans_up(tmp_path, monkeypatch):
    sleeps = _flaky_replace(monkeypatch, failures=99)
    path = tmp_path / "plugins.json"
    with pytest.raises(PermissionError):
        plugins_config.save(str(path), plugins_config.empty_document())
    assert len(sleeps) == plugins_config.REPLACE_ATTEMPTS - 1
    assert sum(sleeps) < 1.0
    assert list(tmp_path.iterdir()) == []


def test_the_one_writer_every_caller_uses_retries_too(tmp_path, monkeypatch):
    """Every write to ``plugins.json`` — routers/plugins.py, routers/marketplace.py
    (install, dev-path, consent, lifecycle) and ``plugin_loader``'s boot marker,
    housekeeping and per-Add-on settings — goes through
    ``settings_store.save_plugins_document``, a thin wrapper with no
    ``os.replace`` of its own around ``plugins_config.save``. This proves the
    WinError-5 retry (#198, d5be34a) covers that one writer, not just the leaf
    helper the tests above exercise directly."""
    import settings_store
    from config import Config

    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    monkeypatch.setenv("PLUGINS_CONFIG_PATH", str(tmp_path / "plugins.json"))
    Config.reload_from_env()

    sleeps = _flaky_replace(monkeypatch, failures=1)
    doc = plugins_config.empty_document()
    plugins_config.ensure_entry(doc, "hello")["enabled"] = True
    settings_store.save_plugins_document(doc)
    assert plugins_config.load(str(tmp_path / "plugins.json"))["plugins"]["hello"]["enabled"] is True
    assert len(sleeps) == 1


def test_fresh_install_has_no_legacy_document():
    assert plugins_config.from_legacy_env({"UNRELATED": "1"}) is None


@pytest.mark.skipif(not plugins_config.LEGACY_ENV_KEYS, reason="no legacy plugin keys in this tree")
def test_legacy_keys_round_trip_through_the_document():
    env = {key: ("true" if field == "enabled" else f"value-{key}")
           for key, (_id, field) in plugins_config.LEGACY_ENV_KEYS.items()}
    doc = plugins_config.from_legacy_env(env)
    for key, (addon_id, field) in plugins_config.LEGACY_ENV_KEYS.items():
        entry = doc["plugins"][addon_id]
        if field == "enabled":
            assert entry["enabled"] is True
            assert entry["consent"]["permissions"] == sorted(plugins_config.LEGACY_CONSENT.get(addon_id, ()))
        else:
            assert entry["settings"][field] == f"value-{key}"
    cleared = plugins_config.apply_legacy_env(doc, {k: None for k in env})
    assert all(not e["enabled"] and not e["settings"] for e in cleared["plugins"].values())


def test_secret_is_masked():
    assert plugins_config.mask_setting("abcdefghijkl") == {"has_key": True, "key_hint": "abc…ijkl"}
    assert plugins_config.mask_setting(None) == {"has_key": False, "key_hint": ""}


FACTS = CoreFacts(api_version=2, python_tag="cp313-win_amd64", core_version="0.8.0")


@pytest.mark.parametrize("fields,facts,expected", [
    ({}, FACTS, None),
    ({"python": "cp313-win_amd64"}, FACTS, None),
    ({"python": "cp312-win_amd64"}, FACTS, "error.plugins.pythonMismatch"),
    ({"api_version": 3}, FACTS, "error.plugins.contractMismatch"),
    ({"min_core": "0.9.0"}, FACTS, "error.plugins.coreTooOld"),
    ({"min_core": "0.9.0"}, CoreFacts(api_version=2, python_tag="x"), None),  # source checkout
])
def test_compatibility(fields, facts, expected):
    manifest = PluginManifest(id="x", name="X", version="1.0.0", **fields)
    assert check_compat(manifest, facts) == expected


# ---------------------------------------------------------------------------
# Herkunft of consent
# ---------------------------------------------------------------------------

INDEX = plugins_config.provenance("index")
FILE_A = plugins_config.provenance("file", sha256="a" * 64)
FILE_B = plugins_config.provenance("file", sha256="b" * 64)
DEV = plugins_config.provenance("dev", path="/src/x")


def _consent(prov, permissions=("llm",)) -> dict:
    return {**plugins_config.empty_consent(), "permissions": list(permissions), **prov}


@pytest.mark.parametrize("given,code,binds", [
    (INDEX, INDEX, True),
    (INDEX, FILE_A, False),
    (FILE_A, FILE_A, True),
    (FILE_A, FILE_B, False),       # another file is new code
    (FILE_A, INDEX, False),
    (DEV, DEV, True),
    (DEV, plugins_config.provenance("dev", path="/src/y"), False),
    (DEV, INDEX, False),
])
def test_consent_binds_to_its_herkunft(given, code, binds):
    assert plugins_config.consent_binds(_consent(given), code) is binds
    expected_missing = [] if binds else ["llm"]
    assert plugins_config.missing_for(_consent(given), code, ["llm"]) == expected_missing


def test_file_and_folder_need_confirmation_even_without_permissions():
    unconfirmed = _consent(INDEX, permissions=())
    assert plugins_config.confirmation_needed(unconfirmed, FILE_A, []) is True
    assert plugins_config.confirmation_needed(unconfirmed, DEV, []) is True
    assert plugins_config.confirmation_needed(unconfirmed, INDEX, []) is False  # index: only new Berechtigungen
    assert plugins_config.confirmation_needed(_consent(FILE_A, ()), FILE_A, []) is False


def test_old_consent_is_migrated_unbound_and_keeps_counting():
    """An entry written before Herkunft existed keeps its consent (unbound),
    which counts for what is installed until a file or folder install binds
    it to the Bundle it was given for."""
    doc = plugins_config.normalize({"plugins": {"x": {
        "enabled": True, "consent": {"version": "1.0.0", "permissions": ["llm"]}}}})
    entry = doc["plugins"]["x"]
    assert entry["consent"]["permissions"] == ["llm"] and plugins_config.is_unbound(entry["consent"])
    assert plugins_config.missing_for(entry["consent"], INDEX, ["llm"]) == []
    assert plugins_config.bind_unbound_consent(entry) is True
    assert entry["consent"]["origin"] == "index"
    assert plugins_config.missing_for(entry["consent"], FILE_A, ["llm"]) == ["llm"]
    assert plugins_config.bind_unbound_consent(entry) is False  # once bound, stays


def test_legacy_env_consent_is_unbound():
    for key, (addon_id, field) in plugins_config.LEGACY_ENV_KEYS.items():
        if field != "enabled":
            continue
        doc = plugins_config.apply_legacy_env(plugins_config.empty_document(), {key: "true"})
        consent = doc["plugins"][addon_id]["consent"]
        assert plugins_config.is_unbound(consent)
        assert consent["permissions"] == sorted(plugins_config.LEGACY_CONSENT.get(addon_id, ()))


def test_herkunft_survives_normalize():
    doc = plugins_config.normalize({"plugins": {"x": {
        "source": {"origin": "file", "sha256": "A" * 64},
        "consent": {"version": "1", "permissions": [], "origin": "file", "sha256": "A" * 64},
        "pending_update": {"version": "2", "permissions": ["llm"], "origin": "bogus", "agreed": ["llm"]},
    }}})
    entry = doc["plugins"]["x"]
    assert entry["source"] == {"origin": "file", "sha256": "a" * 64}
    assert entry["consent"]["origin"] == "file" and entry["consent"]["sha256"] == "a" * 64
    assert entry["pending_update"]["origin"] == "index" and entry["pending_update"]["agreed"] == ["llm"]
