"""Router: version check + one-click update.

Serves

* ``GET /api/version-check`` — polls the public repo's GitHub Releases, compares
  to the bundled APP_VERSION, returns update availability (cached for 1 hour);
* ``POST /api/update/install`` — the one-click update (#148): download the
  release's installer, verify it, launch it silently, and exit so the installer
  can replace the installation and relaunch the app.

The *decision* (is there an update, does the release ship an installer, may this
installation update itself?) is pure and lives in ``services.update_offer``.
This module owns everything the decision is not allowed to do: the network call,
the download, the subprocess, the exit.

APP_VERSION is computed once at import time (side-effect-free after the first
call; reads a VERSION file or runs git, same as the original webapp.py code).
webapp.py re-exports APP_VERSION from here so that ``webapp.APP_VERSION``
continues to work in existing tests.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests as http_requests

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from services import update_offer

router = APIRouter()

# ---------------------------------------------------------------------------
# APP_VERSION — replicated from webapp.py's _read_version() / APP_VERSION.
# Uses Path(__file__).parent.parent to reach the project root (or sys._MEIPASS
# in a frozen build), equivalent to the original _SCRIPT_DIR reference.
#
# The VERSION file is how a release tag reaches the running app (#146): the
# release workflow writes the tag (minus its leading "v") into VERSION,
# localbib.spec bundles that file into the frozen build, and the read below
# picks it up at import. Nothing patches this module's source any more.
# ---------------------------------------------------------------------------

# Releases (and the installer asset) live in the public repo, which is the only
# tree an installer is ever built from (ADR-0015).
RELEASES_API_URL = "https://api.github.com/repos/tobiasbartlog/localbib/releases/latest"


def _read_version() -> str:
    """Read the app version from the bundled VERSION file, else from a git tag.

    Order matters: the VERSION file wins because it is the one thing a release
    build controls (a frozen app has no git repo to describe). "0.1.0" is the
    last-resort answer for a source checkout with neither.
    """
    _FROZEN = getattr(sys, "frozen", False)
    if _FROZEN:
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        bundle_dir = Path(__file__).parent.parent.resolve()

    version_file = bundle_dir / "VERSION"
    if version_file.exists():
        # utf-8-sig, not utf-8: whoever writes VERSION decides whether it gets a
        # BOM, and that answer differs per shell (Windows PowerShell 5.1's
        # "Set-Content -Encoding utf8" writes one, pwsh 7's does not). A BOM is
        # not whitespace, so .strip() leaves it in and the version silently
        # becomes "﻿8.0.0" — unparseable for the update comparison.
        v = version_file.read_text(encoding="utf-8-sig").strip().lstrip("v")
        if v:
            return v
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            stderr=subprocess.DEVNULL,
            cwd=str(bundle_dir),
        )
        return out.decode().strip().lstrip("v")
    except Exception:
        return "0.1.0"


APP_VERSION: str = _read_version()

_version_cache: dict = {"ts": 0.0, "data": None}


def _running_frozen() -> bool:
    """True in the PyInstaller build. One switch, so tests can flip exactly one."""
    return bool(getattr(sys, "frozen", False))


def _fetch_release() -> dict | None:
    """GET the latest release from the public repo, or None on any failure."""
    try:
        resp = http_requests.get(
            RELEASES_API_URL,
            timeout=5,
            headers={"Accept": "application/vnd.github+json"},
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _current_offer(use_cache: bool = True) -> update_offer.UpdateOffer:
    """The evaluated release offer, cached for an hour (the poll, not the verdict)."""
    global _version_cache
    now = time.time()
    if use_cache and _version_cache["data"] is not None and now - _version_cache["ts"] < 3600:
        return update_offer.decide(
            APP_VERSION, _version_cache["data"], _running_frozen()
        )
    release = _fetch_release()
    _version_cache = {"ts": now, "data": release}
    return update_offer.decide(APP_VERSION, release, _running_frozen())


@router.get("/api/version-check")
async def check_version() -> dict:
    """Poll GitHub Releases, compare to APP_VERSION, return update availability.

    Cached for 1 hour. Never raises — network failures degrade silently to
    "no update known", with the manual link empty rather than broken.
    """
    return _current_offer().as_dict()


# ---------------------------------------------------------------------------
# One-click update (#148)
#
# The order below is the whole safety argument, so it is spelled out once:
#   1. refuse unless this is a frozen build with a real installer asset,
#   2. download the installer *completely* into a temp dir,
#   3. verify it is a non-empty Windows executable,
#   4. launch it detached and silent,
#   5. only then exit — the installer needs our files released.
# Any failure before step 4 leaves the app running and answers in German with
# the release page, so the manual route is never lost.
# ---------------------------------------------------------------------------

#: Sanity bound for the download; a LocalBib installer is ~60-120 MB.
MAX_INSTALLER_BYTES = 500 * 1024 * 1024

#: Inno Setup switches (#146's installer/localbib.iss). /SILENT keeps a progress
#: window — the user just watched the app close, silence would read as a crash.
#: Closing a lingering instance and relaunching afterwards are decided in the
#: .iss (CloseApplications=force, the skipifnotsilent [Run] entry), not here:
#: one source of truth per behaviour.
INSTALLER_SILENT_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]

#: Grace period between answering the request and killing the process, so the
#: SPA actually receives the "installation started" reply.
EXIT_DELAY_SECONDS = 1.0


def _download_installer(url: str) -> Path:
    """Download ``url`` into a temp file and return its path. Raises on failure."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="localbib-update-"))
    target = tmp_dir / (url.rsplit("/", 1)[-1] or "LocalBib-Setup.exe")
    written = 0
    with http_requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        declared = int(resp.headers.get("Content-Length") or 0)
        with open(target, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_INSTALLER_BYTES:
                    raise ValueError("Installer ist unerwartet gross")
                fh.write(chunk)
    if declared and written != declared:
        raise ValueError("Download unvollstaendig")
    return target


def _verify_installer(path: Path) -> bool:
    """A downloaded file we are about to execute must at least be a Windows .exe."""
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with open(path, "rb") as fh:
            return fh.read(2) == b"MZ"
    except Exception:
        return False


def _launch_installer(path: Path) -> None:
    """Start the installer detached, so it survives this process exiting."""
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(  # noqa: S603 - path is our own verified download
        [str(path), *INSTALLER_SILENT_ARGS],
        cwd=str(path.parent),
        close_fds=True,
        creationflags=creationflags,
    )


def _exit_app() -> None:
    """Leave immediately so the installer can overwrite the installation.

    ``os._exit`` on purpose: uvicorn's graceful shutdown would keep the process
    (and its file handles) alive while connections drain, which is exactly what
    must not happen here. Nothing in this path holds an open DB transaction —
    the update endpoint writes nothing.
    """
    os._exit(0)


async def _shutdown_after_grace() -> None:
    await asyncio.sleep(EXIT_DELAY_SECONDS)
    _exit_app()


@router.post("/api/update/install")
async def install_update() -> JSONResponse:
    """Download the new installer, launch it silently, and exit.

    Failure paths never exit the app and always name the release page, so the
    banner can degrade to the manual download.
    """
    offer = _current_offer(use_cache=False)

    if not offer.is_frozen:
        raise HTTPException(
            status_code=400,
            detail=(
                "Das Ein-Klick-Update gibt es nur in der installierten Version. "
                "Aus dem Quellcode gestartet aktualisierst du mit 'git pull'."
            ),
        )
    if not offer.update_available:
        raise HTTPException(status_code=400, detail="Es liegt keine neuere Version vor.")
    if not offer.installer_url:
        raise HTTPException(
            status_code=400,
            detail=(
                "Dieses Release enthält keinen Installer. Bitte lade die neue "
                "Version von der Release-Seite herunter."
            ),
        )

    try:
        installer = _download_installer(offer.installer_url)
    except Exception as exc:  # noqa: BLE001 - every failure reads the same to the user
        raise HTTPException(
            status_code=502,
            detail=(
                f"Der Installer konnte nicht geladen werden ({exc}). Bitte lade "
                "ihn manuell von der Release-Seite herunter."
            ),
        )

    if not _verify_installer(installer):
        raise HTTPException(
            status_code=502,
            detail=(
                "Die heruntergeladene Datei ist unvollständig oder kein gültiger "
                "Installer. Bitte lade ihn manuell von der Release-Seite herunter."
            ),
        )

    try:
        _launch_installer(installer)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=(
                f"Der Installer ließ sich nicht starten ({exc}). Bitte lade ihn "
                "manuell von der Release-Seite herunter."
            ),
        )

    return JSONResponse(
        {
            "status": "installing",
            "version": offer.latest,
            "release_url": offer.release_url,
            "message": (
                "Update wird installiert — LocalBib beendet sich jetzt und "
                "startet danach automatisch neu."
            ),
        },
        background=BackgroundTask(_shutdown_after_grace),
    )
