"""``localbib-addon`` — check and build an Add-on Bundle (Add-on Contract 2).

Shipped as the console script of the ``dev`` extra of ``localbib-plugin-api``
(``pip install localbib-plugin-api[dev]``). Stdlib + the package only, so an
Add-on author never needs anything beyond the contract itself to validate and
package a Bundle; the ``schema`` extra's ``jsonschema`` cross-check
(``plugin_api.manifest.validate_manifest`` already interprets the schema
itself) is never required here.

Two subcommands:

``check <folder>``
    Validates a Bundle folder: the Manifest against ``manifest.schema.json``,
    the Bundle structure (a Python package under the Add-on id, ``frontend/``
    with exactly one script), the namespace rules the core enforces at load
    time (view/slot keys, hash-routes, locale keys all prefixed with
    ``<id>.``), locale parity per shipped language, a CSS class prefix check
    (warning only) and a Python build-tag plausibility check. Errors are
    listed and exit with status 1; warnings are printed but never block.

``build <folder> --out <dest>``
    Packs the folder into a deterministic Zip (fixed entry order, fixed
    1980-01-01 timestamps, fixed file attributes — so two builds of the same
    source are byte-identical), writes its SHA-256 checksum file and an index
    snippet (the fields the Marketplace-Index CI and the Release Console read:
    id, version, ``api_version``, ``min_core``, ``python``, artifact name,
    size, sha256).

    When the Manifest names a CPython build tag (``"python": "cp313-win_amd64"``)
    the Zip also carries precompiled bytecode for every ``.py`` in it (the own
    package and ``vendor/``): LocalBib's frozen exe never writes ``.pyc``, so
    without it an installed Bundle recompiles its whole stack on every start.
    The bytecode sits in the ordinary ``__pycache__/<name>.cpython-3XX.pyc``
    layout next to the sources — that is what the standard path finder reads
    for a ``vendor/`` on ``sys.path``, and the sources stay for tracebacks and
    review. It is *unchecked-hash* bytecode (PEP 552): an unpacked Zip gets
    fresh file times, which would invalidate timestamp bytecode, and a Bundle
    never changes after install. Bytecode is tied to the interpreter version,
    so the build refuses to run on another CPython minor than the tag names
    (``--no-bytecode`` ships sources only). ``"python": "any"`` never gets
    bytecode: such a Bundle runs on any interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from plugin_api.manifest import ManifestError, load_manifest

# =============================================================================
# check
# =============================================================================

#: Directory names excluded from both the structure scan and the Zip (build
#: output, caches, the Add-on's own test suite — never part of a shipped
#: Bundle).
_EXCLUDED_DIR_NAMES = {"__pycache__", "tests", ".git", ".pytest_cache", "build", "dist"}

_CSS_CLASS_RE = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
_ROUTE_LITERAL_RE = re.compile(r"""(['"])(/[a-z][a-z0-9_\-/]*)\1""")
_DOTTED_KEY_RE = re.compile(r"""(['"])([a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*)\1""")
_PY_TAG_RE = re.compile(r"^cp(\d{2,3})-(.+)$")
_PLAUSIBLE_PLATFORM_SUBSTRINGS = ("win", "manylinux", "macosx", "musllinux")
_MAX_LISTED = 5  # cap on how many offending names a single message names


def _truncate(names: list[str]) -> str:
    shown = ", ".join(names[:_MAX_LISTED])
    if len(names) > _MAX_LISTED:
        shown += f" (+{len(names) - _MAX_LISTED} more)"
    return shown


@dataclass
class CheckResult:
    """The verdict of ``check_bundle``: errors block, warnings never do."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _css_classes_outside_prefix(css_text: str, addon_id: str) -> list[str]:
    classes = sorted(set(_CSS_CLASS_RE.findall(css_text)))
    prefix = f"{addon_id}-"
    return [c for c in classes if not c.startswith(prefix)]


def _scan_script_for_namespace_issues(script_text: str, addon_id: str) -> list[str]:
    """Best-effort regex scan of the shipped script for route/key literals that
    look like they escape the Add-on's namespace. A warning, never an error:
    static text matching cannot tell a registration string from an unrelated
    one, so a hit here is a prompt to look, not a proven violation."""
    warnings: list[str] = []

    routes = sorted({m.group(2) for m in _ROUTE_LITERAL_RE.finditer(script_text)})
    bad_routes = [r for r in routes if r != f"/{addon_id}" and not r.startswith(f"/{addon_id}/")]
    if bad_routes:
        warnings.append(
            f"script: route-like string(s) outside '/{addon_id}/' found by a best-effort scan, "
            f"verify manually: {_truncate(bad_routes)}"
        )

    keys = sorted({m.group(2) for m in _DOTTED_KEY_RE.finditer(script_text)})
    bad_keys = [k for k in keys if not k.startswith(f"{addon_id}.")]
    if bad_keys:
        warnings.append(
            f"script: dotted key(s) outside the '{addon_id}.' namespace found by a best-effort scan, "
            f"verify manually: {_truncate(bad_keys)}"
        )
    return warnings


def _python_tag_warnings(python_tag: str) -> list[str]:
    if python_tag == "any":
        return []
    match = _PY_TAG_RE.match(python_tag)
    if not match:
        return [f"python: tag {python_tag!r} does not look like a CPython build tag"]
    minor, platform_part = int(match.group(1)), match.group(2)
    warnings: list[str] = []
    if not (38 <= minor <= 320):
        warnings.append(f"python: tag {python_tag!r} names an implausible CPython version ({minor})")
    if not any(s in platform_part for s in _PLAUSIBLE_PLATFORM_SUBSTRINGS):
        warnings.append(f"python: tag {python_tag!r} names an unrecognised platform {platform_part!r}")
    return warnings


def check_bundle(bundle_dir: Path | str) -> CheckResult:
    """Validate a Bundle folder. Never raises for an invalid Bundle — problems
    land in :class:`CheckResult`; only a filesystem error propagates."""
    bundle_dir = Path(bundle_dir)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        manifest = load_manifest(bundle_dir)
    except ManifestError as exc:
        return CheckResult(errors=[f"manifest: {e}" for e in exc.errors])

    addon_id = manifest.id

    # --- Bundle structure: the package under the Add-on id --------------
    if not (bundle_dir / addon_id / "__init__.py").is_file():
        errors.append(f"structure: missing Python package '{addon_id}/__init__.py'")

    # --- Bundle structure: frontend/ with exactly one script ------------
    frontend_dir = bundle_dir / "frontend"
    script_path = bundle_dir / manifest.frontend.script
    if not frontend_dir.is_dir():
        errors.append("structure: missing 'frontend/' directory")
    else:
        scripts = sorted(p.name for p in frontend_dir.glob("*.js"))
        if len(scripts) != 1:
            errors.append(f"structure: 'frontend/' must hold exactly one script, found {scripts or 'none'}")
        elif Path(manifest.frontend.script).name != scripts[0]:
            errors.append(
                f"structure: manifest frontend.script '{manifest.frontend.script}' "
                f"does not match the shipped script '{scripts[0]}'"
            )
    if not script_path.is_file():
        errors.append(f"structure: frontend script not found: {manifest.frontend.script}")

    if manifest.frontend.stylesheet:
        style_path = bundle_dir / manifest.frontend.stylesheet
        if not style_path.is_file():
            errors.append(f"structure: stylesheet not found: {manifest.frontend.stylesheet}")
        else:
            bad_classes = _css_classes_outside_prefix(style_path.read_text(encoding="utf-8"), addon_id)
            if bad_classes:
                warnings.append(f"css: class(es) without the '{addon_id}-' prefix: {_truncate(bad_classes)}")

    # --- Locale files: one per declared language, namespace, parity -----
    missing_locale_langs = sorted(lang for lang in manifest.languages if lang not in manifest.frontend.locales)
    if missing_locale_langs:
        errors.append(f"locales: no locale file declared for language(s): {', '.join(missing_locale_langs)}")

    catalogs: dict[str, dict] = {}
    for lang, rel_path in sorted(manifest.frontend.locales.items()):
        path = bundle_dir / rel_path
        if not path.is_file():
            errors.append(f"locales: file not found for '{lang}': {rel_path}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"locales: {rel_path} is not valid JSON ({exc})")
            continue
        if not isinstance(data, dict):
            errors.append(f"locales: {rel_path} must hold a JSON object of key -> text")
            continue
        catalogs[lang] = data
        bad_keys = sorted(k for k in data if not k.startswith(f"{addon_id}."))
        if bad_keys:
            errors.append(f"namespace: locale '{lang}' has key(s) without the '{addon_id}.' prefix: {_truncate(bad_keys)}")

    if len(catalogs) > 1:
        union = set().union(*(set(d) for d in catalogs.values()))
        for lang, data in sorted(catalogs.items()):
            missing = sorted(union - set(data))
            if missing:
                errors.append(f"locales: '{lang}' is missing key(s) present in another language: {_truncate(missing)}")

    # --- Namespace: nav.view / nav.route ---------------------------------
    if manifest.nav is not None:
        if not manifest.nav.view.startswith(f"{addon_id}."):
            errors.append(f"namespace: nav.view '{manifest.nav.view}' does not start with '{addon_id}.'")
        if manifest.nav.route != f"/{addon_id}" and not manifest.nav.route.startswith(f"/{addon_id}/"):
            errors.append(f"namespace: nav.route '{manifest.nav.route}' is not under '/{addon_id}/'")

    # --- Best-effort script scan (warnings only) -------------------------
    if script_path.is_file():
        warnings.extend(_scan_script_for_namespace_issues(script_path.read_text(encoding="utf-8"), addon_id))

    # --- Python build-tag plausibility -----------------------------------
    warnings.extend(_python_tag_warnings(manifest.python))

    return CheckResult(errors=errors, warnings=warnings)


# =============================================================================
# build
# =============================================================================

#: The fixed Zip timestamp (DOS epoch minimum) so a rebuild is byte-identical.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
#: Regular file, rw-r--r--, in the Unix half of the external attributes.
_ZIP_FILE_ATTR = 0o100644 << 16


class BuildError(Exception):
    """A build that cannot produce a correct Bundle (not a Manifest problem)."""


@dataclass
class BuildResult:
    zip_path: Path
    sha256_path: Path
    snippet_path: Path
    sha256: str
    size: int
    snippet: dict
    #: ``.pyc`` files added to the Zip (0 for ``"python": "any"``).
    bytecode_files: int = 0
    warnings: list[str] = field(default_factory=list)


def bytecode_cache_tag(python_tag: str) -> Optional[str]:
    """The ``__pycache__`` tag (``cpython-313``) a build tag's interpreter
    reads, or ``None`` when the Bundle ships no bytecode (``any``, or a tag
    that names no CPython version)."""
    match = _PY_TAG_RE.match(python_tag)
    if not match:
        return None
    return f"cpython-{match.group(1)}"


def _host_cache_tag() -> Optional[str]:
    if sys.implementation.name != "cpython":
        return None
    return f"cpython-{sys.version_info[0]}{sys.version_info[1]}"


def _compile_bytecode(bundle_dir: Path, files: list[Path], cache_tag: str, scratch: Path,
                      warnings: list[str]) -> dict[str, Path]:
    """``{arcname: pyc file}`` for every ``.py`` among ``files``, written
    below ``scratch`` (never into the Bundle folder itself).

    ``dfile`` is the path inside the Bundle, so the bytecode carries no trace
    of the build machine (the import system fixes ``co_filename`` up to the
    real location at load time). A file under ``vendor/`` that does not
    compile (third-party packages ship the odd Python-2 or template file) is
    skipped with a warning; one in the Add-on's own code fails the build."""
    compiled: dict[str, Path] = {}
    skipped: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(bundle_dir)
        arcname = rel.as_posix()
        pyc_arcname = (rel.parent / "__pycache__" / f"{rel.stem}.{cache_tag}.pyc").as_posix()
        cfile = scratch / pyc_arcname
        try:
            py_compile.compile(
                str(path), cfile=str(cfile), dfile=arcname, doraise=True, optimize=0,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )
        except py_compile.PyCompileError as exc:
            if rel.parts[0] == "vendor":
                skipped.append(arcname)
                continue
            raise BuildError(f"{arcname} does not compile: {exc.msg.strip()}") from exc
        compiled[pyc_arcname] = cfile
    if skipped:
        warnings.append(f"bytecode: {len(skipped)} vendor file(s) do not compile and ship as source only: "
                        f"{_truncate(skipped)}")
    return compiled


def _excluded(parts: tuple[str, ...]) -> bool:
    """Whether a Bundle-relative path stays out of the Zip.

    Below ``vendor/`` the files are third-party packages exactly as ``pip``
    installed them, so only caches and their test suites are dropped: a
    wheel's dot-named folder is load-bearing (scikit-learn keeps
    ``vcomp140.dll`` in ``sklearn/.libs``), and ``build``/``dist`` may be
    package names there. Everywhere else the author's own tree also loses
    VCS/build folders, dotfiles and ``*.egg-info``."""
    if parts[0] == "vendor":
        return any(part in ("__pycache__", "tests") for part in parts[:-1])
    if any(part in _EXCLUDED_DIR_NAMES or part.startswith(".") or part.endswith(".egg-info") for part in parts[:-1]):
        return True
    return parts[-1].startswith(".")


def _iter_bundle_files(bundle_dir: Path, out_dir: Optional[Path]) -> list[Path]:
    """Every file that belongs in the Zip, in a fixed, sorted order.

    Excludes ``__pycache__``, ``*.pyc``/``*.pyo``, ``tests/``, VCS/build
    directories, dotfiles outside ``vendor/`` (see :func:`_excluded`), and
    (if it happens to sit inside the Bundle folder) the build's own output
    directory.
    """
    out_resolved = out_dir.resolve() if out_dir is not None else None
    files: list[Path] = []
    for path in bundle_dir.rglob("*"):
        if path.is_dir():
            continue
        parts = path.relative_to(bundle_dir).parts
        if _excluded(parts) or path.suffix in (".pyc", ".pyo"):
            continue
        if out_resolved is not None:
            try:
                path.resolve().relative_to(out_resolved)
                continue
            except ValueError:
                pass
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(bundle_dir).as_posix())


def build_bundle(bundle_dir: Path | str, out_dir: Path | str, *, bytecode: bool = True) -> BuildResult:
    """Pack a Bundle folder into a deterministic Zip, write its checksum file
    and an index snippet. Raises :class:`ManifestError` for an invalid
    Manifest — run :func:`check_bundle` first for the full diagnostic — and
    :class:`BuildError` when the bytecode for the Manifest's build tag cannot
    be produced here (see the module docstring; ``bytecode=False`` opts out)."""
    bundle_dir = Path(bundle_dir)
    out_dir = Path(out_dir)
    manifest = load_manifest(bundle_dir)
    warnings: list[str] = []

    cache_tag = bytecode_cache_tag(manifest.python) if bytecode else None
    if cache_tag is not None and cache_tag != _host_cache_tag():
        host = f"{sys.implementation.name} {sys.version_info[0]}.{sys.version_info[1]}"
        raise BuildError(
            f"the Manifest's build tag {manifest.python!r} needs bytecode for {cache_tag}, "
            f"but this build runs on {host}. Build on the matching CPython, or pass "
            f"--no-bytecode to ship sources only (every start of the app then recompiles them)."
        )

    files = _iter_bundle_files(bundle_dir, out_dir)
    entries = {path.relative_to(bundle_dir).as_posix(): path for path in files}

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"{manifest.id}-{manifest.version}.zip"
    zip_path = out_dir / zip_name
    tmp_path = out_dir / f"{zip_name}.tmp"

    with tempfile.TemporaryDirectory(prefix="localbib-addon-pyc-") as scratch:
        compiled = _compile_bytecode(bundle_dir, files, cache_tag, Path(scratch), warnings) if cache_tag else {}
        entries.update(compiled)
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arcname in sorted(entries):
                info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = _ZIP_FILE_ATTR
                info.create_system = 0  # FAT — platform-independent, unlike the OS default
                zf.writestr(info, entries[arcname].read_bytes(), compresslevel=6)
    tmp_path.replace(zip_path)

    zip_bytes = zip_path.read_bytes()
    sha256_hex = hashlib.sha256(zip_bytes).hexdigest()
    size = len(zip_bytes)

    sha256_path = out_dir / f"{zip_name}.sha256"
    sha256_path.write_text(f"{sha256_hex}  {zip_name}\n", encoding="utf-8", newline="\n")

    # Field names mirror the Marketplace-Index version entry (the marketplace spec).
    snippet = {
        "id": manifest.id,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "min_core": manifest.min_core,
        "python": manifest.python,
        "artifact": zip_name,
        "size": size,
        "sha256": sha256_hex,
    }
    snippet_path = out_dir / f"{manifest.id}-{manifest.version}.index.json"
    snippet_path.write_text(json.dumps(snippet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    return BuildResult(
        zip_path=zip_path,
        sha256_path=sha256_path,
        snippet_path=snippet_path,
        sha256=sha256_hex,
        size=size,
        snippet=snippet,
        bytecode_files=len(compiled),
        warnings=warnings,
    )


# =============================================================================
# CLI
# =============================================================================

def _print_check_result(result: CheckResult, bundle_dir: Path) -> int:
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")
    if result.errors:
        print(f"{bundle_dir}: {len(result.errors)} error(s), {len(result.warnings)} warning(s)")
        return 1
    print(f"{bundle_dir}: OK ({len(result.warnings)} warning(s))")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.folder)
    return _print_check_result(check_bundle(bundle_dir), bundle_dir)


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        result = build_bundle(args.folder, args.out, bytecode=not args.no_bytecode)
    except ManifestError as exc:
        for error in exc.errors:
            print(f"ERROR: manifest: {error}")
        return 1
    except BuildError as exc:
        print(f"ERROR: build: {exc}")
        return 1
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    print(f"built {result.zip_path.name}  ({result.size} bytes, sha256 {result.sha256})")
    if result.bytecode_files:
        print(f"bytecode: {result.bytecode_files} .pyc for {result.snippet['python']}")
    print(f"checksum: {result.sha256_path.name}")
    print(f"index snippet: {result.snippet_path.name}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localbib-addon",
        description="Check and build a LocalBib Add-on Bundle (Add-on Contract 2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Validate a Bundle folder: manifest, structure, namespaces, locale parity.")
    p_check.add_argument("folder", help="Path to the Bundle folder (holding plugin.json).")
    p_check.set_defaults(func=_cmd_check)

    p_build = sub.add_parser("build", help="Pack a Bundle folder into a Zip, its checksum and an index snippet.")
    p_build.add_argument("folder", help="Path to the Bundle folder (holding plugin.json).")
    p_build.add_argument("--out", required=True, help="Destination directory for the Zip, checksum and index snippet.")
    p_build.add_argument(
        "--no-bytecode", action="store_true",
        help="Ship sources only, even for a CPython build tag (default: precompiled .pyc for the tag's "
             "interpreter, which the build must then run on).",
    )
    p_build.set_defaults(func=_cmd_build)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
