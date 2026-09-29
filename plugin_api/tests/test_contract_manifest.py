"""Contract 2: the Manifest schema, its stdlib validator and the permissions.

Runs with the ``localbib-plugin-api`` package alone (no core import).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import plugin_api
from plugin_api import (
    API_VERSION,
    GATED_SERVICES,
    PERMISSIONS,
    ManifestError,
    Permission,
    PluginManifest,
    granted_services,
    load_manifest,
    load_schema,
    validate_manifest,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hello"


def fixture_manifest() -> dict:
    return json.loads((FIXTURE / "plugin.json").read_text(encoding="utf-8"))


def _without(key: str) -> dict:
    data = fixture_manifest()
    del data[key]
    return data


def _with(**changes) -> dict:
    data = fixture_manifest()
    data.update(changes)
    return data


INVALID = {
    "unknown permission": _with(permissions=["library.read", "camera"]),
    "missing id": _without("id"),
    "missing api_version": _without("api_version"),
    "missing frontend": _without("frontend"),
    "contract 1": _with(api_version=1),
    "tagline too long": _with(tagline="x" * 81),
    "bad id": _with(id="Hello-World"),
    "bad python tag": _with(python="py3"),
    "unknown slot": _with(slots=["toolbar"]),
    "unknown field": _with(colour="red"),
    "bad semver": _with(version="1.0"),
    "frontend outside frontend/": _with(frontend={"script": "../evil.js"}),
    "frontend traversal": _with(frontend={"script": "frontend/../../evil.js"}),
    "bad setting type": _with(settings=[{"key": "k", "type": "int", "label": "hello.k"}]),
}


def test_api_version_is_2():
    assert API_VERSION == 2
    assert PluginManifest(id="x", name="X", version="0.1.0").api_version == 2


def test_schema_accepts_the_fixture_manifest():
    assert validate_manifest(fixture_manifest()) == []


@pytest.mark.parametrize("case", sorted(INVALID))
def test_schema_rejects(case):
    assert validate_manifest(INVALID[case]) != []


def test_unknown_permission_is_named_in_the_error():
    errors = validate_manifest(INVALID["unknown permission"])
    assert any("camera" in e for e in errors)


def test_missing_required_field_is_named_in_the_error():
    assert "(root): missing required field 'id'" in validate_manifest(INVALID["missing id"])


def test_default_language_must_be_a_shipped_language():
    assert validate_manifest(_with(default_language="fr")) == [
        "default_language: must be one of languages"
    ]


def test_jsonschema_agrees_with_the_stdlib_validator():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(load_schema())
    assert list(validator.iter_errors(fixture_manifest())) == []
    for case, data in INVALID.items():
        assert list(validator.iter_errors(data)), case


def test_load_manifest_parses_all_fields():
    m = load_manifest(FIXTURE)
    assert m.id == "hello" and m.api_version == 2
    assert m.languages == ("en", "de") and m.default_language == "en"
    assert m.permissions == frozenset(
        {Permission.LIBRARY_READ, Permission.LLM, Permission.SETTINGS_CORE, Permission.STORAGE}
    )
    assert m.settings[0].key == "salutation" and m.settings[0].type == "string"
    assert m.frontend.script == "frontend/hello.js"
    assert m.frontend.locales == {"en": "frontend/locales/en.json", "de": "frontend/locales/de.json"}
    assert (m.nav.id, m.nav.route, m.nav.view) == ("hello", "/hello", "hello.main")
    assert m.slots == ("item-list-filter", "item-detail-aside", "research-chat-context", "settings")


def test_load_manifest_raises_with_every_problem(tmp_path):
    data = _without("author")
    data["permissions"] = ["root"]
    (tmp_path / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError) as exc:
        load_manifest(tmp_path)
    assert len(exc.value.errors) == 2


def test_the_seven_permissions_and_their_enforcement():
    assert {p.value for p in Permission} == {
        "library.read", "library.write", "llm", "settings.core", "network", "files", "storage",
    }
    assert set(PERMISSIONS) == set(Permission)
    enforced = {p.value for p, info in PERMISSIONS.items() if info.enforced}
    assert enforced == {"library.read", "library.write", "llm", "settings.core", "files", "storage"}
    schema_enum = load_schema()["properties"]["permissions"]["items"]["enum"]
    assert set(schema_enum) == {p.value for p in Permission}


def test_library_write_needs_library_read():
    data = fixture_manifest()
    data["permissions"] = ["library.write"]
    assert validate_manifest(data) == ["permissions: library.write needs library.read"]
    data["permissions"] = ["library.read", "library.write"]
    assert validate_manifest(data) == []


def test_granted_services_follow_the_permissions():
    assert set(GATED_SERVICES) == {"library", "llm", "files", "storage"}
    assert granted_services(["llm", "network", "library.write"]) == {"llm"}
    with pytest.raises(ValueError):
        granted_services(["camera"])


def test_package_is_stdlib_only(tmp_path):
    """Importing the contract and its fakes pulls in nothing outside the stdlib."""
    root = str(Path(plugin_api.__file__).resolve().parent.parent)
    code = "\n".join([
        f"import sys; sys.path.insert(0, {root!r})",
        "import plugin_api, plugin_api.testing",
        "names = {m.split('.')[0] for m in sys.modules}",
        "print(','.join(sorted(names - set(sys.stdlib_module_names) - {'plugin_api', '__main__'})))",
    ])
    out = subprocess.run(
        [sys.executable, "-S", "-c", code],
        capture_output=True, text=True, cwd=tmp_path, check=True,
    )
    assert out.stdout.strip() == ""


def test_fixture_bundle_has_the_bundle_shape():
    """The shape the later check tool (#185) and core loader (#186) rely on."""
    m = load_manifest(FIXTURE)
    frontend = FIXTURE / "frontend"
    assert [p.name for p in frontend.glob("*.js")] == ["hello.js"]
    assert (FIXTURE / m.id / "__init__.py").is_file()
    catalogs = {
        lang: json.loads((FIXTURE / path).read_text(encoding="utf-8"))
        for lang, path in m.frontend.locales.items()
    }
    assert set(catalogs) == set(m.languages)
    keys = [set(c) for c in catalogs.values()]
    assert all(k == keys[0] for k in keys)
    assert all(k.startswith(f"{m.id}.") for k in keys[0])
    assert m.nav.label in keys[0] and all(s.label in keys[0] for s in m.settings)
