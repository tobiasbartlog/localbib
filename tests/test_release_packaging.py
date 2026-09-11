"""The release pipeline's non-code contracts (issue #146, ADR-0015).

Three things in the release path are not Python and therefore have no other
seam to be pinned at, yet breaking any of them breaks a shipped release in a
way no unit test would notice:

* **localbib.spec must bundle VERSION.** It is the only route by which the
  release tag reaches the frozen app (``routers/version.py`` reads it from the
  bundle). ``tests/test_version_check.py`` covers the reading half; this covers
  the bundling half. And the build must stay *onedir* — the installer wraps a
  directory, not a onefile exe.
* **The installer asset name is an API.** Issue #148's one-click update
  resolves the GitHub release asset by the literal name
  ``LocalBib-Setup-<version>.exe``; a rename breaks in-app updating silently.
* **The retired pieces must stay retired.** The old pipeline regex-patched the
  app source, guarded against attaching an .exe to a Release (ADR-0015
  withdrew that) and carried a Lemon Squeezy placeholder job. Their return
  would be a regression, not a feature.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "localbib.spec"
ISS = REPO_ROOT / "installer" / "localbib.iss"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _read(path: Path) -> str:
    # utf-8-sig: build_exe.ps1 carries a BOM (powershell.exe 5.1 needs one to
    # read its umlauts as UTF-8), and a stray U+FEFF must not shift the very
    # first assertion of a file that exists to pin exact strings.
    return path.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# PyInstaller spec
# ---------------------------------------------------------------------------

def test_spec_bundles_the_version_file() -> None:
    """Without this datas entry a frozen build cannot report its release tag."""
    assert re.search(r'\(\s*"VERSION"\s*,\s*"\."\s*\)', _read(SPEC))


def test_spec_survives_a_missing_version_file() -> None:
    """VERSION is generated, not committed (.gitignore) — the release workflow
    writes it from the tag before building.  A build without a tag (the
    frozen-build-smoke job on every push) therefore has no VERSION at all, and
    an unconditional datas entry makes PyInstaller abort with "Unable to find
    ... VERSION".  The entry must be guarded."""
    spec = _read(SPEC)
    assert 'os.path.exists("VERSION")' in spec
    assert 'datas.append(("VERSION", "."))' in spec


def test_version_file_stays_generated_not_committed() -> None:
    """If VERSION were ever committed, a stale value would silently outrank the
    tag the release workflow writes."""
    assert "/VERSION" in _read(REPO_ROOT / ".gitignore")


def test_spec_builds_onedir() -> None:
    """COLLECT + exclude_binaries=True is what makes it onedir; the installer needs it."""
    spec = _read(SPEC)
    assert "COLLECT(" in spec
    assert "exclude_binaries=True" in spec


# ---------------------------------------------------------------------------
# Inno Setup script
# ---------------------------------------------------------------------------

def test_installer_script_exists() -> None:
    assert ISS.is_file(), "installer/localbib.iss is the CI installer build input"


def test_installer_output_name_is_the_documented_asset_name() -> None:
    """LocalBib-Setup-<version>.exe — the name #148 resolves the asset by."""
    assert "OutputBaseFilename=LocalBib-Setup-{#MyAppVersion}" in _read(ISS)


def test_installer_version_is_passed_in_not_guessed() -> None:
    """The version arrives via /DMyAppVersion; the local fallback stays numeric."""
    iss = _read(ISS)
    assert "#ifndef MyAppVersion" in iss
    assert re.search(r'#define MyAppVersion "\d+\.\d+\.\d+"', iss)
    assert "AppVersion={#MyAppVersion}" in iss


def test_installer_wraps_the_onedir_bundle_and_registers_an_uninstaller() -> None:
    iss = _read(ISS)
    assert r'Source: "..\dist\LocalBib\*"' in iss
    assert "recursesubdirs" in iss
    assert "UninstallDisplayIcon=" in iss
    assert "{autoprograms}" in iss          # Start-menu entry


def test_installer_never_deletes_the_users_library() -> None:
    """An uninstall must not touch LITERATUR_BASE_DIR — so: no [UninstallDelete]."""
    assert "[UninstallDelete]" not in _read(ISS)


def test_installer_offers_no_machine_wide_install() -> None:
    """A machine-wide install turns the one-click update into a UAC prompt.

    Measured on a real build: with ``PrivilegesRequiredOverridesAllowed=dialog``
    a user could install for all users, and UsePreviousPrivileges (default yes)
    then made the *silent* update re-launch Setup elevated with /ALLUSERS —
    "Administrative install mode: Yes" in Setup's own log, ~40 s spent waiting on
    a rights prompt that appears after the app has already exited. Without the
    override the escape hatch is closed: even an explicit /ALLUSERS on the
    command line stays at "Administrative install mode: No".
    """
    # Directives only — the comment above them names the override on purpose,
    # to record why it is absent.
    directives = [
        line.strip() for line in _read(ISS).splitlines()
        if not line.lstrip().startswith(";")
    ]
    assert "PrivilegesRequired=lowest" in directives
    assert not [d for d in directives if d.startswith("PrivilegesRequiredOverridesAllowed")]


def test_silent_relaunch_runs_as_the_logged_in_user() -> None:
    """The updated app must belong to the user, not to an elevated token.

    Without ``runasoriginaluser`` the [Run] entry inherits Setup's token. On a
    machine where elevation switches accounts that hands the user a LocalBib with
    a different %USERPROFILE% — another ~\\Literatur, another database, an empty
    library where theirs used to be.
    """
    relaunch = [
        line for line in _read(ISS).splitlines()
        if line.startswith("Filename:") and "skipifnotsilent" in line
    ]
    assert len(relaunch) == 1, "exactly one silent relaunch entry"
    assert "runasoriginaluser" in relaunch[0]


def test_installer_installs_the_64bit_bundle_in_64bit_mode() -> None:
    """PyInstaller builds x64; without this it landed in Program Files (x86)."""
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in _read(ISS)


# ---------------------------------------------------------------------------
# Release workflow — regression guards for the pieces #146 removed
# ---------------------------------------------------------------------------

def test_release_workflow_builds_from_the_spec() -> None:
    workflow = _read(RELEASE_WORKFLOW)
    assert "pyinstaller --noconfirm --clean localbib.spec" in workflow
    assert "installer\\localbib.iss" in workflow


@pytest.mark.parametrize("retired", [
    "--onefile",                       # ad-hoc build, replaced by the spec
    "APP_VERSION = _read_version",     # the regex patch of the app source
    "LEMON_SQUEEZY",                   # placeholder upload job (ADR-0015)
    "no .exe files in workspace",      # the withdrawn "no exe in releases" guard
])
def test_retired_release_machinery_is_gone(retired: str) -> None:
    assert retired not in _read(RELEASE_WORKFLOW)


def test_release_workflow_only_runs_in_the_public_repo() -> None:
    """ADR-0015: an installer is never built from a tree that exists only privately."""
    assert "if: github.repository == 'tobiasbartlog/localbib'" in _read(RELEASE_WORKFLOW)


def test_release_workflow_attaches_the_installer_to_a_draft_release() -> None:
    """Issue #154, permanent policy: the installer reaches a draft, not a
    published release, so a candidate is never offered to an installed app —
    the maintainer verifies the CI artefact and publishes it by hand."""
    assert "draft: true" in _read(RELEASE_WORKFLOW)


@pytest.mark.parametrize("script", ["release_workflow", "build_exe"])
def test_version_is_written_bom_free_on_both_paths(script: str) -> None:
    """build_exe.ps1 calls itself the local twin of release.yml, so both must
    produce the same VERSION bytes.  ``Set-Content -Encoding utf8`` does not:
    Windows PowerShell 5.1 writes a BOM, pwsh 7 does not.  U+FEFF is not
    whitespace, so it survives the reader's ``.strip()`` and ends up inside the
    version number.  Writing through .NET makes the result shell-independent
    instead of relying on which shell happens to run the step."""
    path = RELEASE_WORKFLOW if script == "release_workflow" else REPO_ROOT / "build_exe.ps1"
    text = _read(path)

    assert "[System.Text.UTF8Encoding]::new($false)" in text
    assert not re.search(r"Set-Content[^\n]*VERSION", text)
