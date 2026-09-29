"""localbib.spec ships what an installed Add-on needs at runtime (#183, #198).

The #183 spike found two gaps in the release exe: ``plugin_api``'s Manifest
schema was not bundled (every Manifest check died, ``/api/plugins`` answered
500, no Add-on loaded at all), and only the stdlib modules the core itself
imports were bundled (a Bundle's ``vendor/`` stack died on ``fileinput``).

These tests execute the real spec file with PyInstaller's build objects
stubbed out, so they need no PyInstaller and cost milliseconds. The
frozen-build smoke in CI (``.github/workflows/test.yml``) proves the same
against a real build.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "localbib.spec"

#: What the spike saw missing and a vendorised numeric stack actually imports.
SPIKE_MISSING = ("fileinput", "timeit", "cProfile", "pstats", "doctest", "optparse",
                 "pdb", "graphlib", "pickletools", "wsgiref", "plistlib")


class _Recorder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        # The spec reads these off the Analysis result.
        self.pure = self.scripts = self.binaries = self.datas = []


@pytest.fixture(scope="module")
def spec_namespace():
    hooks = types.ModuleType("PyInstaller.utils.hooks")

    def collect_submodules(package, filter=lambda name: True, on_error=None):
        # Stand-in: the package plus one ordinary and one test submodule, so
        # the spec's own filter is exercised.
        return [m for m in (package, f"{package}.sub", f"{package}.tests.x") if filter(m)]

    hooks.collect_submodules = collect_submodules
    fakes = {
        "PyInstaller": types.ModuleType("PyInstaller"),
        "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
        "PyInstaller.utils.hooks": hooks,
    }
    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        namespace = {name: _Recorder for name in ("Analysis", "PYZ", "EXE", "COLLECT")}
        namespace["__file__"] = str(SPEC)
        exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return namespace


def _analysis(ns):
    return ns["a"].kwargs


def test_the_manifest_schema_is_bundled_where_plugin_api_reads_it(spec_namespace):
    datas = _analysis(spec_namespace)["datas"]
    assert ("plugin_api/manifest.schema.json", "plugin_api") in datas
    assert (REPO_ROOT / "plugin_api" / "manifest.schema.json").is_file()


def test_every_plugin_api_data_file_is_bundled(spec_namespace):
    """Any non-Python file plugin_api ships beside its modules is runtime data."""
    datas = {src for src, _dest in _analysis(spec_namespace)["datas"]}
    runtime_data = sorted(p.name for p in (REPO_ROOT / "plugin_api").iterdir()
                          if p.is_file() and p.suffix == ".json")
    assert runtime_data, "plugin_api ships no JSON data file any more?"
    for name in runtime_data:
        assert f"plugin_api/{name}" in datas


@pytest.mark.parametrize("module", SPIKE_MISSING)
def test_the_stdlib_modules_the_spike_missed_are_hiddenimports(spec_namespace, module):
    assert module in _analysis(spec_namespace)["hiddenimports"]


def test_every_importable_stdlib_module_is_bundled_unless_deliberately_skipped(spec_namespace):
    import importlib.util

    hidden = set(_analysis(spec_namespace)["hiddenimports"])
    skipped = spec_namespace["_stdlib_skipped"]
    missing = []
    for name in sorted(sys.stdlib_module_names):
        if skipped(name) or name in hidden:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is not None:
            missing.append(name)
    assert missing == []


def test_stdlib_packages_bring_their_submodules_but_not_their_tests(spec_namespace):
    hidden = set(_analysis(spec_namespace)["hiddenimports"])
    assert "email.sub" in hidden             # the stub's ordinary submodule
    assert "email.tests.x" not in hidden     # ... and its test one is filtered


def test_nothing_is_both_bundled_and_excluded(spec_namespace):
    analysis = _analysis(spec_namespace)
    hidden_tops = {m.split(".")[0] for m in analysis["hiddenimports"]}
    assert hidden_tops.isdisjoint(analysis["excludes"])
    assert "tkinter" in analysis["excludes"]
    assert "tkinter" in spec_namespace["STDLIB_SKIP"]
