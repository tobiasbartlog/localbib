"""The .exe's starter: a tray icon instead of a console (#214, ADR-0023).

On Windows the frozen build has no console window. ``webapp.py`` hands its
``__main__`` over to ``run_frozen`` here, which

1. waits for the port when it is a restart (the old process still holds it),
2. finds out whether LocalBib already runs on the port - then it only opens
   the browser (not for a launch by Windows at sign-in) and leaves, without a
   second icon; a foreign program on the port gets a Windows message instead,
3. starts the server in a background thread,
4. shows the tray icon with its menu, the one-time notification, and opens the
   browser - except for the ``--autostart`` launch and a restart.

``python webapp.py`` in the source tree never comes here: console, no icon.

Everything that touches the system is handed in (``StarterDeps``): the port
check, the server start, the icon factory, the browser, the registry, the
message box. The ``Starter`` itself is plain logic and tested with fakes
(``tests/test_starter.py``). The real wiring below it - ``pystray``,
``Pillow``, ``uvicorn`` in a thread, ``ctypes`` - stays untested on purpose,
like the Release Console's HTTP layer; ``pystray``/``PIL`` are imported only
inside it, so nothing else needs them installed.

The menu texts come from the SPA's own catalog (``ui_catalog``, ADR-0018
addendum), in the language of ``UI_LANGUAGE``. The menu is rebuilt when that
language or the autostart state changes - the Settings page or Task Manager
can change either while the icon is up.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence

import autostart
import settings_store
import ui_catalog

log = logging.getLogger("localbib.starter")

APP_NAME = "LocalBib"
#: The ``.env`` key recording that the one-time notification was shown.
NOTICE_KEY = "TRAY_NOTICE_SHOWN"
#: A double click on the icon is two clicks for pystray; one tab is enough.
OPEN_DEBOUNCE_SECONDS = 1.0
#: How often the icon re-reads language and autostart state.
REFRESH_SECONDS = 2.0
#: How long one wait for the server lasts. A start that takes longer is not a
#: failure while the server thread is alive: the starter logs and waits again
#: (SET-B7) - a slow first start used to find its *own* server on the port.
SERVER_START_TIMEOUT = 60.0
#: uvicorn's grace for open connections (an SSE stream) on "Quit".
STOP_GRACE_SECONDS = 3.0

SEPARATOR = "-"

#: Every catalog key the starter reads. tests/test_starter.py checks that each
#: exists in both catalogs - the parity test covers them like any other key.
TEXT_KEYS = (
    "tray.running", "tray.open", "tray.openLog", "tray.openLibrary",
    "tray.autostart", "tray.restart", "tray.quit",
    "tray.noticeTitle", "tray.noticeBody", "tray.portBusy",
    "error.restart_failed", "error.autostart_failed",
)


@dataclass(frozen=True)
class MenuEntry:
    """One line of the tray menu, independent of pystray."""

    id: str
    text: str = ""
    action: Optional[Callable[[], None]] = None
    enabled: bool = True
    default: bool = False
    checked: Optional[bool] = None

    @property
    def is_separator(self) -> bool:
        return self.id == SEPARATOR


_SEP = MenuEntry(SEPARATOR)


class ServerHandle(Protocol):
    def wait_started(self, timeout: float) -> bool:
        """``True`` once the server listens; ``False`` when it died or the
        timeout passed - ``is_alive`` tells the two apart."""

    def is_alive(self) -> bool:
        """Whether the server thread still runs (a slow start, not a failure)."""

    def stop(self) -> None: ...


class TrayIcon(Protocol):
    def run(self, on_ready: Callable[[], None], on_tick: Callable[[], None]) -> None:
        """Show the icon, call ``on_ready`` once, then ``on_tick`` every
        ``REFRESH_SECONDS``; block until ``stop``."""

    def set_menu(self, tooltip: str, entries: List[MenuEntry]) -> None: ...
    def notify(self, title: str, body: str) -> None: ...
    def hide(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class StarterDeps:
    """The system boundary, handed in so tests can fake every part of it."""

    port_in_use: Callable[[int], bool]
    is_localbib: Callable[[int], bool]
    wait_for_free_port: Callable[[int], bool]
    start_server: Callable[[int], ServerHandle]
    icon_factory: Callable[[str, List[MenuEntry]], TrayIcon]
    open_browser: Callable[[str], None]
    open_path: Callable[[str], None]
    show_error: Callable[[str, str], None]
    registry: autostart.Registry
    relaunch: Callable[[], None]
    catalog: ui_catalog.Catalog
    language: Callable[[], str]
    library_dir: Callable[[], str]
    log_file: str
    env_path: str
    exe_path: str
    clock: Callable[[], float] = field(default=time.monotonic)


class Starter:
    def __init__(self, deps: StarterDeps, *, port: int, argv: Sequence[str] = (),
                 restarted: bool = False) -> None:
        self.deps = deps
        self.port = port
        self.url = f"http://localhost:{port}"
        # A launch by Windows at sign-in, or the second half of a restart: the
        # user did not ask for a browser tab (a restarted app's open tab
        # reloads itself).
        self.autostart_launch = autostart.AUTOSTART_ARG in argv
        self.restarted = restarted
        self.opens_browser = not (self.autostart_launch or restarted)
        self._server: Optional[ServerHandle] = None
        self._icon: Optional[TrayIcon] = None
        self._state: Optional[tuple] = None
        self._last_open: Optional[float] = None
        self._quitting = False
        self._exit_code = 0

    # --- texts ------------------------------------------------------------
    def t(self, key: str, **params: object) -> str:
        return self.deps.catalog.text(self.deps.language(), key, **params)

    def tooltip(self) -> str:
        return self.t("tray.running", port=self.port)

    # --- start ------------------------------------------------------------
    def run(self) -> int:
        """The whole start. Returns the process exit code."""
        if self.restarted:
            self.deps.wait_for_free_port(self.port)
        if self.deps.port_in_use(self.port):
            if self.deps.is_localbib(self.port):
                log.info("LocalBib already runs on port %s", self.port)
                if not self.autostart_launch:
                    self.deps.open_browser(self.url)
                return 0
            self.deps.show_error(APP_NAME, self.t("tray.portBusy", port=self.port))
            return 1
        self._server = self.deps.start_server(self.port)
        self._state = self._current_state()
        self._icon = self.deps.icon_factory(self.tooltip(), self.menu())
        self._icon.run(self.on_ready, self.refresh)
        return self._exit_code

    def on_ready(self) -> None:
        """The icon is visible: say where the app lives (once), then open it."""
        self._notify_once()
        started = self._wait_for_server()
        if not started:
            # Lost the port between the check and the bind - usually to a
            # second LocalBib started by the same double click, then it is
            # simply the running instance.
            log.error("The server did not start on port %s", self.port)
            if self.deps.is_localbib(self.port):
                if not self.autostart_launch:
                    self.deps.open_browser(self.url)
            else:
                self._exit_code = 1
                self.deps.show_error(APP_NAME, self.t("tray.portBusy", port=self.port))
            self.quit()
            return
        if self.opens_browser:
            self.open_app()

    def _wait_for_server(self) -> bool:
        """Wait until the server listens. Only a server thread that died is a
        failed start; a timeout while it is alive means "still starting" (a
        large library's first start), so keep waiting instead of taking our
        own half-started server for another LocalBib or a foreign program."""
        if self._server is None:
            return False
        while not self._quitting:
            if self._server.wait_started(SERVER_START_TIMEOUT):
                return True
            if not self._server.is_alive():
                return False
            log.warning("The server on port %s is still starting after %.0f s; waiting on",
                        self.port, SERVER_START_TIMEOUT)
        return False

    def _notify_once(self) -> None:
        """The first start with an icon says where LocalBib lives now. That it
        was shown is a ``.env`` key, written through the settings store."""
        env = Path(self.deps.env_path)
        if self._icon is None or settings_store.read_env(env, {NOTICE_KEY}).get(NOTICE_KEY) == "true":
            return
        self._icon.notify(self.t("tray.noticeTitle"), self.t("tray.noticeBody"))
        try:
            settings_store.write_env(env, {NOTICE_KEY: "true"})
        except settings_store.EnvLocked:
            log.warning("The .env is locked; the tray notice stays unrecorded")

    # --- menu -------------------------------------------------------------
    def autostart_enabled(self) -> bool:
        try:
            return autostart.is_enabled(self.deps.registry)
        except OSError:
            return False

    def menu(self) -> List[MenuEntry]:
        """The menu, top to bottom, exactly as the approved design orders it."""
        t = self.t
        return [
            MenuEntry("status", self.tooltip(), enabled=False),
            _SEP,
            MenuEntry("open", t("tray.open"), self.open_app, default=True),
            MenuEntry("log", t("tray.openLog"), self.open_log),
            MenuEntry("library", t("tray.openLibrary"), self.open_library),
            _SEP,
            MenuEntry("autostart", t("tray.autostart"), self.toggle_autostart,
                      checked=self.autostart_enabled()),
            MenuEntry("restart", t("tray.restart"), self.restart),
            _SEP,
            MenuEntry("quit", t("tray.quit"), self.quit),
        ]

    def _current_state(self) -> tuple:
        return (self.deps.language(), self.autostart_enabled())

    def refresh(self, force: bool = False) -> bool:
        """Rebuild menu and tooltip when language or autostart changed."""
        state = self._current_state()
        if self._icon is None or (state == self._state and not force):
            return False
        self._state = state
        self._icon.set_menu(self.tooltip(), self.menu())
        return True

    # --- actions ----------------------------------------------------------
    def open_app(self) -> None:
        now = self.deps.clock()
        if self._last_open is not None and now - self._last_open < OPEN_DEBOUNCE_SECONDS:
            return
        self._last_open = now
        self.deps.open_browser(self.url)

    def open_log(self) -> None:
        self._open(self.deps.log_file)

    def open_library(self) -> None:
        self._open(self.deps.library_dir())

    def _open(self, path: str) -> None:
        try:
            self.deps.open_path(path)
        except OSError as exc:
            log.warning("Could not open %s: %s", path, exc)

    def toggle_autostart(self) -> None:
        try:
            autostart.set_enabled(self.deps.registry, not self.autostart_enabled(),
                                  self.deps.exe_path)
        except OSError as exc:
            log.warning("Autostart could not be switched: %s", exc)
            self.deps.show_error(APP_NAME, self.t("error.autostart_failed", message=exc))
        self.refresh(force=True)

    def restart(self) -> None:
        """The mechanism of ``POST /api/app/restart``: start the .exe again
        (it waits for the port), then leave in order."""
        try:
            self.deps.relaunch()
        except Exception as exc:  # noqa: BLE001 - keep running, say why
            log.warning("Restart failed: %s", exc)
            self.deps.show_error(APP_NAME, self.t("error.restart_failed", message=exc))
            return
        self.quit()

    def quit(self) -> None:
        """Remove the icon, shut the server down in order, end the loop."""
        if self._quitting:
            return
        self._quitting = True
        self.hide_icon()
        if self._server is not None:
            self._server.stop()
        if self._icon is not None:
            self._icon.stop()

    def hide_icon(self) -> None:
        if self._icon is not None:
            try:
                self._icon.hide()
            except Exception:  # noqa: BLE001 - leaving anyway
                pass


# =============================================================================
# The real wiring (Windows .exe only, untested on purpose - see module doc)
# =============================================================================

def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def answers_as_localbib(port: int, attempts: int = 6, delay: float = 0.5) -> bool:
    """Does LocalBib answer on ``port``? A few tries cover an instance that
    is just starting. No proxy: this is the loopback."""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{port}/api/system/info"
    for attempt in range(attempts):
        try:
            with opener.open(url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8")).get("app") == APP_NAME
        except (OSError, ValueError):
            if attempt + 1 < attempts:
                time.sleep(delay)
    return False


def _message_box(title: str, text: str) -> None:
    import ctypes

    MB_ICONERROR, MB_SETFOREGROUND = 0x10, 0x10000
    ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONERROR | MB_SETFOREGROUND)


class _UvicornServer:
    """uvicorn in a background thread; the icon's loop owns the main thread."""

    def __init__(self, app, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(
            app, host="127.0.0.1", port=port,
            # No uvicorn logging config: its records reach the root logger and
            # with it the log file. The access log would only bury the rest.
            log_config=None, access_log=False,
            timeout_graceful_shutdown=STOP_GRACE_SECONDS,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="localbib-server", daemon=True)
        self._thread.start()

    def wait_started(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return True
            if not self._thread.is_alive():
                return False
            time.sleep(0.1)
        return bool(self._server.started)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=STOP_GRACE_SECONDS + 10)


class _PystrayIcon:
    """The MenuEntry list as a native Windows menu (pystray)."""

    def __init__(self, image_path: str, tooltip: str, entries: List[MenuEntry]) -> None:
        import pystray
        from PIL import Image

        self._pystray = pystray
        self._stopped = threading.Event()
        self._icon = pystray.Icon(APP_NAME, Image.open(image_path), title=tooltip,
                                  menu=self._menu(entries))

    def _menu(self, entries: List[MenuEntry]):
        p = self._pystray

        def act(fn):
            return (lambda icon, item: fn()) if fn else None

        def fixed(value):
            return lambda item: value

        items = []
        for entry in entries:
            if entry.is_separator:
                items.append(p.Menu.SEPARATOR)
                continue
            items.append(p.MenuItem(
                entry.text, act(entry.action), default=entry.default, enabled=entry.enabled,
                checked=None if entry.checked is None else fixed(entry.checked),
            ))
        return p.Menu(*items)

    def run(self, on_ready: Callable[[], None], on_tick: Callable[[], None]) -> None:
        def setup(icon) -> None:
            icon.visible = True
            on_ready()
            while not self._stopped.wait(REFRESH_SECONDS):
                try:
                    on_tick()
                except Exception:  # noqa: BLE001 - the icon must keep running
                    log.exception("Tray refresh failed")

        self._icon.run(setup)

    def set_menu(self, tooltip: str, entries: List[MenuEntry]) -> None:
        self._icon.title = tooltip
        self._icon.menu = self._menu(entries)

    def notify(self, title: str, body: str) -> None:
        self._icon.notify(body, title)

    def hide(self) -> None:
        self._icon.visible = False

    def stop(self) -> None:
        self._stopped.set()
        self._icon.stop()


def run_frozen(app, *, port: int, argv: Sequence[str], bundle_dir: str,
               wait_for_free_port: Callable[[int], bool]) -> int:
    """The Windows .exe's start (``webapp.py`` ``__main__`` hands over here)."""
    import webbrowser

    from config import Config
    from logging_setup import LOG_FILE_NAME, log_dir_for
    from routers import version as version_router

    restarted = os.environ.pop(version_router.RESTART_ENV, None) == "1"
    settings_dir = os.path.dirname(Config.ENV_PATH)
    icon_path = os.path.join(bundle_dir, "static", "icons", "localbib.ico")
    deps = StarterDeps(
        port_in_use=port_in_use,
        is_localbib=answers_as_localbib,
        wait_for_free_port=wait_for_free_port,
        start_server=lambda p: _UvicornServer(app, p),
        icon_factory=lambda tooltip, entries: _PystrayIcon(icon_path, tooltip, entries),
        open_browser=webbrowser.open,
        open_path=os.startfile,
        show_error=_message_box,
        registry=autostart.WinRegistry(),
        relaunch=version_router._relaunch_self,
        catalog=ui_catalog.Catalog(os.path.join(bundle_dir, "static", "locales")),
        language=lambda: Config.UI_LANGUAGE,
        library_dir=lambda: Config.BASE_DIR,
        log_file=os.path.join(log_dir_for(settings_dir), LOG_FILE_NAME),
        env_path=Config.ENV_PATH,
        exe_path=os.path.abspath(sys.executable),
    )
    starter = Starter(deps, port=port, argv=argv, restarted=restarted)
    # A restart or update from the SPA leaves through os._exit: take the icon
    # along, or a dead one stays in the notification area.
    version_router.add_exit_hook(starter.hide_icon)
    return starter.run()
