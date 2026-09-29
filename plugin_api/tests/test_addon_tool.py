"""``localbib-addon check``/``build`` — the tool of issue #185.

Runs with the ``localbib-plugin-api`` package alone (no core import), against
the good fixture from A1 (``hello``) and three tiny broken fixtures.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from plugin_api.addon_tool import (
    BuildError,
    build_bundle,
    check_bundle,
    main,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HELLO = FIXTURES / "hello"
BROKEN_MISSING_FIELD = FIXTURES / "broken_missing_field"
BROKEN_PREFIX = FIXTURES / "broken_prefix"
BROKEN_LOCALE_GAP = FIXTURES / "broken_locale_gap"


# =============================================================================
# check
# =============================================================================

def test_check_accepts_the_good_fixture():
    result = check_bundle(HELLO)
    assert result.errors == []
    assert result.warnings == []
    assert result.ok


def test_check_rejects_missing_required_field():
    result = check_bundle(BROKEN_MISSING_FIELD)
    assert not result.ok
    assert any("license" in e for e in result.errors)


def test_check_rejects_locale_key_without_namespace_prefix():
    result = check_bundle(BROKEN_PREFIX)
    assert not result.ok
    assert any("badprefix." in e and "prefix" in e for e in result.errors)


def test_check_rejects_locale_gap():
    result = check_bundle(BROKEN_LOCALE_GAP)
    assert not result.ok
    assert any("localegap.body" in e for e in result.errors)


@pytest.mark.parametrize("fixture", [BROKEN_MISSING_FIELD, BROKEN_PREFIX, BROKEN_LOCALE_GAP])
def test_cli_check_exits_1_on_a_broken_fixture(fixture, capsys):
    assert main(["check", str(fixture)]) == 1
    out = capsys.readouterr().out
    assert "ERROR:" in out


def test_cli_check_exits_0_on_the_good_fixture(capsys):
    assert main(["check", str(HELLO)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_css_prefix_violation_is_a_warning_not_an_error(tmp_path):
    """The one namespace rule PRD-marketplace.md calls a warning: CSS classes
    without the '<id>-' prefix must not fail 'check' or block a build."""
    _write_minimal_bundle(tmp_path, "cssid", stylesheet_css=".cssid-ok {}\n.stray {}\n")
    result = check_bundle(tmp_path)
    assert result.ok
    assert any("stray" in w for w in result.warnings)


def test_implausible_python_tag_is_a_warning(tmp_path):
    _write_minimal_bundle(tmp_path, "pytagid", python="cp999-weirdplatform")
    result = check_bundle(tmp_path)
    assert result.ok
    assert any("implausible" in w for w in result.warnings)
    assert any("unrecognised" in w for w in result.warnings)


# =============================================================================
# build
# =============================================================================

def test_build_writes_zip_checksum_and_snippet_that_agree(tmp_path):
    out = tmp_path / "out"
    result = build_bundle(HELLO, out)

    assert result.zip_path.is_file()
    assert result.sha256_path.is_file()
    assert result.snippet_path.is_file()

    zip_bytes = result.zip_path.read_bytes()
    assert hashlib.sha256(zip_bytes).hexdigest() == result.sha256
    assert result.size == len(zip_bytes)

    checksum_line = result.sha256_path.read_text(encoding="utf-8").strip()
    assert checksum_line == f"{result.sha256}  {result.zip_path.name}"

    snippet = json.loads(result.snippet_path.read_text(encoding="utf-8"))
    assert snippet == {
        "id": "hello",
        "version": "0.1.0",
        "api_version": 2,
        "min_core": "0.8.0",
        "python": "any",
        "artifact": "hello-0.1.0.zip",
        "size": result.size,
        "sha256": result.sha256,
    }


def test_build_excludes_tests_pycache_and_is_a_valid_zip(tmp_path):
    out = tmp_path / "out"
    result = build_bundle(HELLO, out)
    with zipfile.ZipFile(result.zip_path) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
    assert "plugin.json" in names
    assert "hello/__init__.py" in names
    assert not any("__pycache__" in n for n in names)
    assert not any(n.startswith("tests/") or "/tests/" in n for n in names)


def test_two_builds_of_the_same_source_are_byte_identical(tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    result_a = build_bundle(HELLO, out_a)
    result_b = build_bundle(HELLO, out_b)

    assert result_a.zip_path.read_bytes() == result_b.zip_path.read_bytes()
    assert result_a.sha256 == result_b.sha256
    assert result_a.snippet == result_b.snippet


def test_cli_build_reports_the_artifact(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["build", str(HELLO), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "hello-0.1.0.zip" in text
    assert (out / "hello-0.1.0.zip.sha256").is_file()


def test_cli_build_fails_clearly_on_an_invalid_manifest(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["build", str(BROKEN_MISSING_FIELD), "--out", str(out)]) == 1
    assert "ERROR:" in capsys.readouterr().out
    assert not out.exists() or not any(out.iterdir())


# =============================================================================
# build — precompiled bytecode for a CPython build tag
# =============================================================================

HOST_TAG = f"cp{sys.version_info[0]}{sys.version_info[1]}-win_amd64"
HOST_CACHE = sys.implementation.cache_tag
OTHER_TAG = f"cp{sys.version_info[0]}{sys.version_info[1] + 1}-win_amd64"


def _bundle_with_vendor(root: Path, addon_id: str, python: str) -> None:
    _write_minimal_bundle(root, addon_id, python=python)
    (root / addon_id / "__init__.py").write_text("ANSWER = 42\n", encoding="utf-8")
    (root / "vendor" / "libpkg").mkdir(parents=True)
    (root / "vendor" / "libpkg" / "__init__.py").write_text("NAME = 'lib'\n", encoding="utf-8")
    (root / "vendor" / "libmod.py").write_text("VALUE = 7\n", encoding="utf-8")
    # A stale cache from the author's machine must never ship.
    (root / addon_id / "__pycache__").mkdir()
    (root / addon_id / "__pycache__" / "__init__.cpython-27.pyc").write_bytes(b"stale")


def test_a_build_tag_ships_unchecked_hash_bytecode_in_pycache(tmp_path):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pyctag", HOST_TAG)
    result = build_bundle(src, tmp_path / "out")

    with zipfile.ZipFile(result.zip_path) as zf:
        names = set(zf.namelist())
        header = zf.read(f"pyctag/__pycache__/__init__.{HOST_CACHE}.pyc")[:16]
    assert {
        f"pyctag/__pycache__/__init__.{HOST_CACHE}.pyc",
        f"vendor/libpkg/__pycache__/__init__.{HOST_CACHE}.pyc",
        f"vendor/__pycache__/libmod.{HOST_CACHE}.pyc",
    } <= names
    assert "pyctag/__pycache__/__init__.cpython-27.pyc" not in names
    assert "vendor/libmod.py" in names                       # sources stay
    assert result.bytecode_files == 3
    assert header[:4] == importlib.util.MAGIC_NUMBER
    assert int.from_bytes(header[4:8], "little") == 0b01    # hash-based, unchecked (PEP 552)


def test_vendor_ships_as_pip_installed_it_minus_caches_and_test_suites(tmp_path):
    """A wheel's dot-named folder is load-bearing (scikit-learn loads
    ``sklearn/.libs/vcomp140.dll`` at import), and ``build``/``dist`` can be
    package names; the author's own tree still loses dotfiles and build dirs."""
    src = tmp_path / "src"
    _bundle_with_vendor(src, "vendkeep", "any")
    for rel in ("vendor/lib/.libs/vcomp140.dll", "vendor/lib/.hidden_cfg", "vendor/dist/__init__.py",
                "vendor/lib/tests/test_x.py", "vendor/lib/__pycache__/x.cpython-313.pyc",
                "vendkeep/.secret", ".ci/notes.txt", "build/junk.txt"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(b"x")
    with zipfile.ZipFile(build_bundle(src, tmp_path / "out").zip_path) as zf:
        names = set(zf.namelist())
    assert {"vendor/lib/.libs/vcomp140.dll", "vendor/lib/.hidden_cfg", "vendor/dist/__init__.py"} <= names
    assert not names & {"vendor/lib/tests/test_x.py", "vendor/lib/__pycache__/x.cpython-313.pyc",
                        "vendkeep/.secret", ".ci/notes.txt", "build/junk.txt"}


def test_the_shipped_bytecode_is_what_the_import_system_loads(tmp_path, monkeypatch):
    """Unpacked like an install (fresh file times), with the source changed
    afterwards: the import still runs the shipped bytecode, so no compile."""
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycload", HOST_TAG)
    result = build_bundle(src, tmp_path / "out")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(result.zip_path) as zf:
        zf.extractall(installed)
    (installed / "vendor" / "libmod.py").write_text("raise RuntimeError('compiled from source')\n",
                                                    encoding="utf-8")

    monkeypatch.setattr(sys, "dont_write_bytecode", True)   # like the frozen exe
    monkeypatch.syspath_prepend(str(installed / "vendor"))
    monkeypatch.delitem(sys.modules, "libmod", raising=False)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module("libmod")
        assert module.VALUE == 7
        assert module.__cached__.endswith(f"libmod.{HOST_CACHE}.pyc")
    finally:
        sys.modules.pop("libmod", None)


def test_any_ships_no_bytecode(tmp_path):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycany", "any")
    result = build_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(result.zip_path) as zf:
        assert not any(n.endswith(".pyc") for n in zf.namelist())
    assert result.bytecode_files == 0


def test_a_tag_for_another_interpreter_is_refused_with_a_clear_message(tmp_path, capsys):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycother", OTHER_TAG)
    with pytest.raises(BuildError, match="--no-bytecode"):
        build_bundle(src, tmp_path / "out")

    assert main(["build", str(src), "--out", str(tmp_path / "cli")]) == 1
    out = capsys.readouterr().out
    assert "ERROR: build:" in out and OTHER_TAG in out


def test_no_bytecode_ships_sources_only_for_a_foreign_tag(tmp_path):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycopt", OTHER_TAG)
    assert main(["build", str(src), "--out", str(tmp_path / "out"), "--no-bytecode"]) == 0
    with zipfile.ZipFile(tmp_path / "out" / "pycopt-0.1.0.zip") as zf:
        assert not any(n.endswith(".pyc") for n in zf.namelist())


def test_a_vendor_file_that_does_not_compile_is_a_warning_own_code_an_error(tmp_path):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycbad", HOST_TAG)
    (src / "vendor" / "py2only.py").write_text("print 'hello'\n", encoding="utf-8")
    result = build_bundle(src, tmp_path / "out")
    assert any("py2only.py" in w for w in result.warnings)
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "vendor/py2only.py" in zf.namelist()

    (src / "pycbad" / "broken.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(BuildError, match="pycbad/broken.py"):
        build_bundle(src, tmp_path / "out2")


def test_bytecode_builds_are_byte_identical(tmp_path):
    src = tmp_path / "src"
    _bundle_with_vendor(src, "pycsame", HOST_TAG)
    a = build_bundle(src, tmp_path / "a")
    b = build_bundle(src, tmp_path / "b")
    assert a.zip_path.read_bytes() == b.zip_path.read_bytes()


# =============================================================================
# helpers
# =============================================================================

def _write_minimal_bundle(root: Path, addon_id: str, *, stylesheet_css: str = "", python: str = "any") -> None:
    manifest = {
        "id": addon_id,
        "name": addon_id,
        "version": "0.1.0",
        "author": "Test",
        "license": "MIT",
        "tagline": "minimal fixture",
        "languages": ["en"],
        "default_language": "en",
        "api_version": 2,
        "min_core": "0.1.0",
        "python": python,
        "permissions": [],
        "frontend": {
            "script": f"frontend/{addon_id}.js",
            "locales": {"en": "frontend/locales/en.json"},
        },
    }
    if stylesheet_css:
        manifest["frontend"]["stylesheet"] = f"frontend/{addon_id}.css"
        (root / "frontend" / f"{addon_id}.css").parent.mkdir(parents=True, exist_ok=True)
        (root / "frontend" / f"{addon_id}.css").write_text(stylesheet_css, encoding="utf-8")

    (root / addon_id).mkdir(parents=True, exist_ok=True)
    (root / addon_id / "__init__.py").write_text("", encoding="utf-8")
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / f"{addon_id}.js").write_text("// fixture\n", encoding="utf-8")
    (root / "frontend" / "locales").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "locales" / "en.json").write_text(
        json.dumps({f"{addon_id}.title": "Title"}), encoding="utf-8"
    )
    (root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
