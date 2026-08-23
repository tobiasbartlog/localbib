#!/usr/bin/env python3
"""Anti-regrowth line-budget guard (PRD #71, issue #95).

The Backend-Modularisierung shrank webapp.py 6173 -> ~293 lines and
literature_manager.py 1865 -> ~116 lines by splitting domain logic into
``services/`` and route handlers into ``routers/``.  This check keeps the
structure from silently rotting back into god-files: every git-tracked ``.py``
file must stay at or below BUDGET lines, unless it is explicitly listed in
ALLOWLIST with a reason.

Decisions (maintainer, issue #95):
- BUDGET  = 1200 lines per .py  (PRD-Default)
- Gate    = HARD  (non-zero exit -> CI red) from day one; the refactoring that
            justified a soft ramp-up is finished.

Allowlist policy: an entry is a deliberate, documented exception, not a place to
hide new debt.  If an allowlisted file drops back under budget the check emits a
warning so the stale entry can be removed (it never fails for that reason).

Usage:
    python scripts/check_line_budget.py

Exit codes:
    0  all files within budget (allowlisted over-budget files are tolerated)
    1  at least one non-allowlisted file exceeds the budget
    2  the tool itself failed (e.g. ``git`` not available)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Maximum lines allowed in a single tracked .py file.
BUDGET = 1200

# Deliberate, documented exceptions: path (POSIX, repo-relative) -> reason.
# Keep this list short; every entry is debt the maintainer chose to accept.
ALLOWLIST: dict[str, str] = {
}


def tracked_python_files() -> list[str]:
    """Git-tracked .py paths (repo-relative, POSIX). Using the git index keeps
    worktrees, virtualenvs and build artefacts out of the scan automatically."""
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def main() -> int:
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    violations: list[tuple[str, int]] = []
    stale_allowlist: list[tuple[str, int]] = []

    for rel in tracked_python_files():
        abs_path = repo_root / rel
        if not abs_path.exists():  # tracked but deleted in the working tree
            continue
        lines = count_lines(abs_path)
        if lines <= BUDGET:
            if rel in ALLOWLIST:
                stale_allowlist.append((rel, lines))
            continue
        if rel in ALLOWLIST:
            continue  # deliberate, documented exception
        violations.append((rel, lines))

    for rel, lines in stale_allowlist:
        print(
            f"::warning::{rel} is {lines} lines, now within the {BUDGET}-line "
            f"budget — remove it from ALLOWLIST in scripts/check_line_budget.py."
        )

    if violations:
        print(
            f"\nLine-budget guard FAILED — {len(violations)} file(s) exceed "
            f"{BUDGET} lines:\n"
        )
        for rel, lines in sorted(violations, key=lambda x: -x[1]):
            print(f"  {rel}: {lines} lines (+{lines - BUDGET})")
        print(
            "\nSplit the file (see docs/adr/0006), or — if the size is a "
            "deliberate, documented exception — add it to ALLOWLIST in "
            "scripts/check_line_budget.py with a reason."
        )
        return 1

    print(f"Line-budget guard OK — no tracked .py file exceeds {BUDGET} lines.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"line-budget guard: git command failed: {exc}", file=sys.stderr)
        sys.exit(2)
