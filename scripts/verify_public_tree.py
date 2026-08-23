#!/usr/bin/env python3
"""Public-tree verification: fail loudly if anything private survived the export.

The public LocalBib repo is a *derived* artifact (issue #143, ADR-0015): the
private repo stays the development repo, and a per-release export strips the
plugin packages, the plugin registrations and every private/working file. This
module is the machine that checks the result — used twice:

* right after ``scripts/export_public.py`` produced a tree, and
* as a CI gate **inside** the public repo, which is why this file is one of the
  few ``scripts/`` modules that ships with the export and depends on nothing
  beyond the standard library.

Six checks, all reported together so one run shows every problem:

1. **Forbidden identifiers** — no plugin package name, plugin-only vendor name
   or private string may appear in a file's *path*, in its text, or in the
   bytes of a binary asset. Case-insensitive substring match, because a leak is
   a leak whether it sits in code, a comment, a doc, a filename or an icon.
2. **Required files** — the licensing groundwork (#139) must be present: the
   AGPL root ``LICENSE`` and the MIT ``plugin_api/LICENSE``, plus the entry
   points a source install needs.
3. **Requirements split** — no exported ``requirements*.txt`` may pull in a
   plugin-only dependency; the exported tree has no plugin to use it.
4. **Oversized files** — build residue or a stray dataset that rode along is a
   publishing accident, not a release artifact (#151).
5. **Unreviewable binaries** — binary content nobody can read by eye is only
   allowed in the shapes a UI legitimately needs (images, icons, fonts);
   anything else has to be justified by widening the allowlist (#151).
6. **Credential shapes** — well-known key/token formats, so a key pasted into a
   tracked file during debugging is caught before it is published (#151).

Checks 4–6 are deliberately *generic*: they carry no private knowledge, so they
ship with the export and the public repo's own CI re-runs them. The
maintainer's private patterns live in a separate, unexported audit module.

This file scans itself out of checks 1 and 6 (it necessarily *contains* the
forbidden words and the credential shapes) — see ``SCAN_EXEMPT``.

Usage:
    python scripts/verify_public_tree.py [path/to/tree]     # default: repo root

Exit codes:
    0  clean
    1  at least one violation
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# Anything matching one of these (case-insensitive, substring) is a leak: the
# plugin packages, their route/settings namespaces, a plugin-only vendor, the
# export markers themselves (a surviving marker means the stripper missed a
# block) and the author's institutional address. Substring, not word match — a
# leak is a leak in ``atlasProjectId`` too. A core word that legitimately
# contains one of these would hard-fail the gate; the escape is to pass a
# narrowed ``identifiers`` or an extra ``exempt`` path, deliberately loud.
FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "atlas",
    "hipster",
    "elsevier",
    "plugin-export-",
    "@rwth-aachen.de",
)

# Present-and-non-empty in every published tree.
REQUIRED_FILES: tuple[str, ...] = (
    "LICENSE",
    "plugin_api/LICENSE",
    "README.md",
    "requirements.txt",
    "webapp.py",
    "literature_manager.py",
)

# Distributions that exist only for the plugins (issue #143 requirements split).
# Hand-listed rather than read from ``requirements-plugins.txt``: that file is
# private and never reaches a tree this module has to check. Same reason
# FORBIDDEN_IDENTIFIERS does not derive the plugin names from
# ``context.PLUGIN_MODULES`` — in a public tree that table is empty by design.
PLUGIN_ONLY_PACKAGES: tuple[str, ...] = (
    "PyYAML",
    "tenacity",
    "umap-learn",
    "scikit-learn",
)

# Files exempt from the identifier and credential scans. Only this module: it is
# the list of forbidden words and of credential shapes, so scanning it would
# always fail.
SCAN_EXEMPT: tuple[str, ...] = ("scripts/verify_public_tree.py",)

# Nothing in a source release is this big. The largest legitimate file today is
# the exported ``static/app.js`` at roughly half a megabyte, so the budget sits
# a comfortable factor above it: big enough never to fire on real sources,
# small enough that a stray dataset, a database or a build artifact trips it.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Binary shapes a web app legitimately publishes. Everything else that is not
# decodable as UTF-8 is unreviewable content: nobody can read it, and it may
# carry identifiers in its bytes. Widening this set is a deliberate decision,
# which is exactly the point of listing it.
REVIEWABLE_BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".ico", ".icns", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})

# Well-known credential shapes. Deliberately format-anchored rather than
# entropy-based: a false positive costs one line of investigation, a false
# negative publishes a key. The last entry is the generic "someone assigned a
# long opaque literal to something called a secret" case.
CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai-style key", r"\bsk-[A-Za-z0-9_\-]{24,}"),
    ("github token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("aws access key id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("google api key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    (
        "secret assigned to a literal",
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*"
        r"[\"'][A-Za-z0-9_\-+/.=]{24,}[\"']",
    ),
)

# Never walked — machine-generated or vendored, and none of it is published.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".pytest_cache",
    ".import_linter_cache",
    ".grimp_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".venv",
    "venv",
})


@dataclass(frozen=True)
class Violation:
    """One reason the tree is not publishable."""

    kind: str      # "forbidden-identifier" | "missing-file" | "plugin-dependency"
                   # | "oversized-file" | "unreviewable-binary" | "credential"
    path: str      # tree-relative POSIX path
    line: int      # 1-based; 0 when the finding is about the file as a whole
    detail: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: [{self.kind}] {self.detail}"


def iter_paths(root: Path) -> Iterator[Path]:
    """Every publishable file under ``root``, in a stable order. Skips SKIP_DIRS."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def iter_files(root: Path) -> Iterator[tuple[Path, str | None]]:
    """Every file under ``root`` with its UTF-8 text, or ``None`` when binary.

    Skips SKIP_DIRS. Binaries are yielded rather than dropped: an icon whose
    bytes carry a plugin name leaks it just as surely as source would, and the
    export copies binaries verbatim.
    """
    for path in iter_paths(root):
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            yield path, None  # binary asset — scanned as bytes, not as lines
        except OSError:
            continue  # unreadable — nothing to scan


def scan_forbidden(
    root: Path,
    *,
    identifiers: Sequence[str] = FORBIDDEN_IDENTIFIERS,
    exempt: Iterable[str] = SCAN_EXEMPT,
) -> list[Violation]:
    """Every forbidden identifier in the tree — in a path, a line or a blob.

    All three surfaces are scanned, because all three get published: a file
    *named* ``routers/atlas_bridge.py`` leaks even when its content is
    innocuous, and so do the bytes of a binary asset.
    """
    exempt_set = set(exempt)
    needles = [(identifier, identifier.lower()) for identifier in identifiers]
    violations: list[Violation] = []
    for path, text in iter_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in exempt_set:
            continue
        lowered_rel = rel.lower()
        for identifier, needle in needles:
            if needle in lowered_rel:
                violations.append(Violation(
                    kind="forbidden-identifier",
                    path=rel,
                    line=0,
                    detail=f"{identifier!r} in the file path itself",
                ))
        if text is None:
            blob = path.read_bytes().lower()
            for identifier, needle in needles:
                if needle.encode("utf-8") in blob:
                    violations.append(Violation(
                        kind="forbidden-identifier",
                        path=rel,
                        line=0,
                        detail=f"{identifier!r} in the bytes of this binary file",
                    ))
            continue
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            for identifier, needle in needles:
                if needle in lowered:
                    violations.append(Violation(
                        kind="forbidden-identifier",
                        path=rel,
                        line=number,
                        detail=f"{identifier!r} in: {line.strip()[:120]}",
                    ))
    return violations


def check_required(
    root: Path, *, required: Sequence[str] = REQUIRED_FILES
) -> list[Violation]:
    """Required files must exist and carry content (an empty LICENSE is a bug)."""
    violations: list[Violation] = []
    for rel in required:
        path = root / rel
        if not path.is_file():
            violations.append(Violation(
                kind="missing-file", path=rel, line=0, detail="required file is missing",
            ))
        elif not path.read_bytes().strip():
            violations.append(Violation(
                kind="missing-file", path=rel, line=0, detail="required file is empty",
            ))
    return violations


def check_requirements_split(
    root: Path, *, plugin_packages: Sequence[str] = PLUGIN_ONLY_PACKAGES
) -> list[Violation]:
    """No exported requirements file may list a plugin-only distribution.

    Every ``requirements*.txt`` in the tree root is checked, not just the core
    one: anyone following the public README installs the dev and build files
    too, and the public tree has no plugin that could use the dependency.
    """
    violations: list[Violation] = []
    for path in sorted(root.glob("requirements*.txt")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            requirement = line.split("#", 1)[0].strip()
            if not requirement:
                continue
            name = requirement.split("[", 1)[0]
            for char in ("=", "<", ">", "!", "~", ";", " "):
                name = name.split(char, 1)[0]
            for package in plugin_packages:
                if name.lower() == package.lower():
                    violations.append(Violation(
                        kind="plugin-dependency",
                        path=path.name,
                        line=number,
                        detail=f"{package} is plugin-only and belongs in requirements-plugins.txt",
                    ))
    return violations


def check_file_sizes(
    root: Path, *, max_bytes: int = MAX_FILE_BYTES
) -> list[Violation]:
    """Flag files too big to be plausible source — build residue, datasets, dumps."""
    violations: list[Violation] = []
    for path in iter_paths(root):
        size = path.stat().st_size
        if size > max_bytes:
            violations.append(Violation(
                kind="oversized-file",
                path=path.relative_to(root).as_posix(),
                line=0,
                detail=(
                    f"{size / 1024 / 1024:.1f} MiB exceeds the "
                    f"{max_bytes / 1024 / 1024:.0f} MiB budget for a published file"
                ),
            ))
    return violations


def check_binary_content(
    root: Path, *, allowed_suffixes: Iterable[str] = REVIEWABLE_BINARY_SUFFIXES
) -> list[Violation]:
    """Flag binary content outside the shapes a UI legitimately publishes.

    Not "no binaries": icons and fonts are normal. The finding is *unreviewable*
    content — a database, an archive, an executable — which nobody can read by
    eye before it goes out.
    """
    allowed = {suffix.lower() for suffix in allowed_suffixes}
    violations: list[Violation] = []
    for path, text in iter_files(root):
        if text is not None:
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() in allowed:
            continue
        violations.append(Violation(
            kind="unreviewable-binary",
            path=rel,
            line=0,
            detail=(
                f"binary content with suffix {path.suffix or '(none)'!r} — not a "
                "reviewable asset type; exclude it or widen REVIEWABLE_BINARY_SUFFIXES"
            ),
        ))
    return violations


def scan_credentials(
    root: Path,
    *,
    patterns: Sequence[tuple[str, str]] = CREDENTIAL_PATTERNS,
    exempt: Iterable[str] = SCAN_EXEMPT,
) -> list[Violation]:
    """Flag lines whose shape matches a well-known credential format."""
    exempt_set = set(exempt)
    compiled = [(label, re.compile(pattern)) for label, pattern in patterns]
    violations: list[Violation] = []
    for path, text in iter_files(root):
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exempt_set:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label, regex in compiled:
                match = regex.search(line)
                if match:
                    violations.append(Violation(
                        kind="credential",
                        path=rel,
                        line=number,
                        detail=f"looks like a {label}: {_redact(match.group(0))}",
                    ))
                    break  # one finding per line: the shapes overlap, the leak is one
    return violations


def _redact(secret: str) -> str:
    """Show enough of a match to recognise it, never enough to use it."""
    return secret[:8] + "…" if len(secret) > 8 else secret


def verify_tree(root: Path) -> list[Violation]:
    """All six checks, missing files first (they explain follow-on findings)."""
    root = Path(root)
    return (
        check_required(root)
        + check_requirements_split(root)
        + scan_forbidden(root)
        + check_file_sizes(root)
        + check_binary_content(root)
        + scan_credentials(root)
    )


def format_report(violations: Sequence[Violation], root: Path) -> str:
    """A report loud enough that nobody publishes past it."""
    if not violations:
        return f"Public-tree verification OK — {root} is clean."
    lines = [
        "",
        f"PUBLIC-TREE VERIFICATION FAILED — {len(violations)} violation(s) in {root}:",
        "",
    ]
    lines += [f"  {violation}" for violation in violations]
    lines += [
        "",
        "Nothing may be published from this tree. Either strip the offending "
        "block (PLUGIN-EXPORT markers), exclude the file in "
        "scripts/export_public.py, or add the missing file.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    violations = verify_tree(root)
    print(format_report(violations, root))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
