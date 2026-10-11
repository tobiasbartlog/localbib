"""The .exe's starter (#214, ADR-0023), driven through fakes only.

Everything the starter touches on a real machine - the port, the server, the
tray icon, the browser, the registry, the message box - is handed in, so these
tests never open a socket, never show an icon and never touch the user's
registry or ``.env``. The pystray wiring itself stays untested on purpose (see
the module docstring of ``starter.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autostart
import settings_store
import starter
import ui_catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES = REPO_ROOT / "static" / "locales"
PORT = 8123
URL = f"http://localhost:{PORT}"

MENU_ORDER = ["status", "-", "open", "log", "library", "-", "autostart", "restart", "-", "quit"]


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict = {}

    def read(self, key, name):
        return self.values.get((key, name))

    def write_string(self, key, name, value):
        self.values[(key, name)] = value

    def delete(self, key, name):
        self.values.pop((key, name), None)


class FakeServer:
    """``starts``: comes up at all; ``slow_rounds``: how many waits time out
    first while the thread is still alive (a slow first start)."""

    def __init__(self, starts: bool = True, slow_rounds: int = 0) -> None:
        self.starts = starts
        self.slow_rounds = slow_rounds
        self.waits = 0
        self.stopped = False

    def wait_started(self, timeout):
        self.waits += 1
        if self.waits <= self.slow_rounds:
            return False
        return self.starts

    def is_alive(self):
        # Alive through the slow rounds; afterwards only if it came up.
        return self.starts or self.waits <= self.slow_rounds

    def stop(self):
        self.stopped = True


class FakeIcon:
    def __init__(self, tooltip, entries) -> None:
        self.tooltip = tooltip
        self.entries = entries
        self.notices: list = []
        self.rebuilt = 0
        self.hidden = False
        self.stopped = False
        self.on_tick = None

    def run(self, on_ready, on_tick):
        self.on_tick = on_tick
        on_ready()

    def set_menu(self, tooltip, entries):
        self.tooltip = tooltip
        self.entries = entries
        self.rebuilt += 1

    def notify(self, title, body):
        self.notices.append((title, body))

    def hide(self):
        self.hidden = True

    def stop(self):
        self.stopped = True

    def entry(self, entry_id):
        return next(e for e in self.entries if e.id == entry_id)


class World:
    """One machine as the starter sees it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.in_use = False
        self.localbib = False
        self.server_starts = True
        self.server_slow_rounds = 0
        self.lang = "en"
        self.now = 100.0
        self.locales = LOCALES
        self.registry = FakeRegistry()
        self.env = tmp_path / ".env"
        self.library = str(tmp_path / "Literatur")
        self.log_file = str(tmp_path / "logs" / "localbib.log")
        self.exe = r"C:\Program Files\LocalBib\LocalBib.exe"
        self.browser: list = []
        self.opened: list = []
        self.errors: list = []
        self.waited: list = []
        self.servers: list = []
        self.icons: list = []
        self.relaunches = 0
        self.relaunch_fails = False

    def _server(self, port):
        server = FakeServer(self.server_starts, self.server_slow_rounds)
        self.servers.append(server)
        return server

    def _icon(self, tooltip, entries):
        icon = FakeIcon(tooltip, entries)
        self.icons.append(icon)
        return icon

    def _relaunch(self):
        if self.relaunch_fails:
            raise OSError("no exe")
        self.relaunches += 1

    def deps(self) -> starter.StarterDeps:
        return starter.StarterDeps(
            port_in_use=lambda port: self.in_use,
            is_localbib=lambda port: self.localbib,
            wait_for_free_port=lambda port: self.waited.append(port) or True,
            start_server=self._server,
            icon_factory=self._icon,
            open_browser=self.browser.append,
            open_path=self.opened.append,
            show_error=lambda title, text: self.errors.append((title, text)),
            registry=self.registry,
            relaunch=self._relaunch,
            catalog=ui_catalog.Catalog(str(self.locales)),
            language=lambda: self.lang,
            library_dir=lambda: self.library,
            log_file=self.log_file,
            env_path=str(self.env),
            exe_path=self.exe,
            clock=lambda: self.now,
        )

    def start(self, argv=(), restarted=False) -> tuple[starter.Starter, int]:
        s = starter.Starter(self.deps(), port=PORT, argv=argv, restarted=restarted)
        return s, s.run()

    @property
    def icon(self) -> FakeIcon:
        assert len(self.icons) == 1
        return self.icons[0]


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


# ---------------------------------------------------------------------------
# A second start: LocalBib already runs, or something else holds the port
# ---------------------------------------------------------------------------

def test_a_second_start_only_opens_the_browser_on_the_running_app(world):
    world.in_use, world.localbib = True, True
    _s, code = world.start()
    assert code == 0
    assert world.browser == [URL]
    assert world.icons == [] and world.servers == []
    assert world.errors == []


def test_a_second_start_by_windows_at_sign_in_opens_nothing(world):
    world.in_use, world.localbib = True, True
    _s, code = world.start(argv=["--autostart"])
    assert code == 0
    assert world.browser == []
    assert world.icons == [] and world.servers == []


def test_a_foreign_program_on_the_port_gets_a_windows_message(world):
    world.in_use, world.localbib = True, False
    _s, code = world.start()
    assert code == 1
    assert world.icons == [] and world.servers == [] and world.browser == []
    [(title, text)] = world.errors
    assert title == "LocalBib"
    assert str(PORT) in text and "another program" in text


# ---------------------------------------------------------------------------
# The first start: server, icon, notification, browser
# ---------------------------------------------------------------------------

def test_a_normal_start_runs_the_server_shows_the_icon_and_opens_the_browser(world):
    _s, code = world.start()
    assert code == 0
    assert len(world.servers) == 1
    assert world.icon.tooltip == f"LocalBib is running · localhost:{PORT}"
    assert world.browser == [URL]


def test_the_autostart_argument_opens_no_browser(world):
    world.start(argv=["--autostart"])
    assert len(world.icons) == 1 and len(world.servers) == 1
    assert world.browser == []


def test_a_restart_waits_for_the_port_and_opens_no_browser(world):
    world.start(restarted=True)
    assert world.waited == [PORT]
    assert len(world.icons) == 1
    assert world.browser == []


def test_the_notification_shows_once_and_is_recorded_through_the_settings_store(world):
    world.start()
    [(title, body)] = world.icon.notices
    assert title == "LocalBib is running in the system tray"
    assert "clock" in body
    assert settings_store.read_env(world.env, {starter.NOTICE_KEY}) == {starter.NOTICE_KEY: "true"}

    second = World(world.tmp_path)
    second.start()
    assert second.icon.notices == []


def test_a_locked_env_does_not_stop_the_start_and_the_notice_stays_unrecorded(world, monkeypatch):
    def locked(path, updates):
        raise settings_store.EnvLocked("held")

    monkeypatch.setattr(settings_store, "write_env", locked)
    world.start()
    assert len(world.icon.notices) == 1
    assert settings_store.read_env(world.env, {starter.NOTICE_KEY}) == {}


def test_a_server_that_does_not_come_up_is_reported_and_ends_the_app(world):
    world.server_starts = False
    _s, code = world.start()
    assert code == 1
    assert world.browser == []
    assert world.errors and str(PORT) in world.errors[0][1]
    assert world.icon.hidden and world.icon.stopped
    assert world.servers[0].stopped


def test_losing_the_port_to_another_localbib_just_opens_that_one(world):
    """Two starts from one double click: the second to bind gives way."""
    world.server_starts = False
    world.localbib = True  # answers by the time our own server gave up
    _s, code = world.start()
    assert code == 0
    assert world.errors == []
    assert world.browser == [URL]
    assert world.icon.hidden and world.icon.stopped


def test_a_slow_start_keeps_waiting_and_never_takes_its_own_server_for_another(world):
    """SET-B7: past the timeout the starter's own half-started server answers
    on the port. While the server thread lives that is "still starting", not
    "another LocalBib" or "port busy"."""
    world.server_slow_rounds = 2
    world.localbib = True  # our own server answers is_localbib by now
    s, code = world.start()
    assert world.servers[0].waits == 3
    assert code == 0
    assert world.errors == []
    assert world.browser == [URL]
    assert not world.icon.stopped  # the app keeps running with its icon
    assert not world.servers[0].stopped


def test_a_slow_start_then_a_dead_thread_is_a_failed_start(world):
    world.server_slow_rounds = 1
    world.server_starts = False
    _s, code = world.start()
    assert world.servers[0].waits == 2
    assert code == 1
    assert world.errors and str(PORT) in world.errors[0][1]


# ---------------------------------------------------------------------------
# The menu
# ---------------------------------------------------------------------------

def test_the_menu_order_with_the_greyed_out_status_line(world):
    world.start()
    entries = world.icon.entries
    assert [e.id for e in entries] == MENU_ORDER
    status = entries[0]
    assert status.text == f"LocalBib is running · localhost:{PORT}"
    assert status.enabled is False and status.action is None
    assert [e.text for e in entries if not e.is_separator][1:] == [
        "Open LocalBib", "Open log", "Open library folder",
        "Start with Windows", "Restart", "Quit",
    ]
    # "Open LocalBib" is the bold default - what a double click does.
    assert [e.id for e in entries if e.default] == ["open"]
    assert world.icon.entry("autostart").checked is False
    assert all(e.checked is None for e in entries if e.id != "autostart")


def test_open_debounces_the_double_click(world):
    s, _ = world.start(argv=["--autostart"])
    open_app = world.icon.entry("open").action
    open_app()
    open_app()  # the second click of a double click
    assert world.browser == [URL]
    world.now += starter.OPEN_DEBOUNCE_SECONDS + 0.1
    open_app()
    assert world.browser == [URL, URL]


def test_open_log_and_open_library_open_their_paths(world):
    world.start()
    world.icon.entry("log").action()
    world.icon.entry("library").action()
    assert world.opened == [world.log_file, world.library]


def test_start_with_windows_writes_and_removes_the_run_entry(world):
    world.start()
    world.icon.entry("autostart").action()
    run_value = world.registry.read(autostart.RUN_KEY, autostart.VALUE_NAME)
    assert run_value == f'"{world.exe}" --autostart'
    assert world.icon.entry("autostart").checked is True

    world.icon.entry("autostart").action()
    assert world.registry.read(autostart.RUN_KEY, autostart.VALUE_NAME) is None
    assert world.icon.entry("autostart").checked is False


def test_an_entry_disabled_in_task_manager_shows_off_and_switching_on_lifts_it(world):
    world.registry.write_string(autostart.RUN_KEY, autostart.VALUE_NAME, f'"{world.exe}" --autostart')
    world.registry.values[(autostart.APPROVED_KEY, autostart.VALUE_NAME)] = b"\x03" + bytes(11)
    world.start()
    assert world.icon.entry("autostart").checked is False
    world.icon.entry("autostart").action()
    assert world.registry.read(autostart.APPROVED_KEY, autostart.VALUE_NAME) is None
    assert world.icon.entry("autostart").checked is True


def test_restart_relaunches_then_leaves_in_order(world):
    world.start(argv=["--autostart"])
    world.icon.entry("restart").action()
    assert world.relaunches == 1
    assert world.servers[0].stopped
    assert world.icon.hidden and world.icon.stopped


def test_a_failed_restart_keeps_the_app_running_and_says_why(world):
    world.relaunch_fails = True
    world.start(argv=["--autostart"])
    world.icon.entry("restart").action()
    assert world.errors and "no exe" in world.errors[0][1]
    assert not world.servers[0].stopped and not world.icon.stopped


def test_quit_shuts_the_server_down_and_removes_the_icon(world):
    s, _ = world.start()
    world.icon.entry("quit").action()
    assert world.servers[0].stopped
    assert world.icon.hidden and world.icon.stopped
    s.quit()  # a second quit is a no-op
    assert world.relaunches == 0


def test_the_menu_follows_language_and_autostart_changes(world):
    world.start()
    icon = world.icon
    assert icon.on_tick() is False and icon.rebuilt == 0

    world.lang = "de"  # changed in Settings while the icon is up
    assert icon.on_tick() is True
    assert icon.tooltip == f"LocalBib läuft · localhost:{PORT}"
    assert [e.text for e in icon.entries if not e.is_separator] == [
        f"LocalBib läuft · localhost:{PORT}", "LocalBib öffnen", "Log öffnen",
        "Bibliotheksordner öffnen", "Mit Windows starten", "Neu starten", "Beenden",
    ]

    autostart.enable(world.registry, world.exe)  # switched on in Settings
    assert icon.on_tick() is True
    assert icon.entry("autostart").checked is True
    assert icon.on_tick() is False


# ---------------------------------------------------------------------------
# Texts: the one catalog, the visible missing key
# ---------------------------------------------------------------------------

def test_german_texts_come_from_the_german_catalog(world):
    world.lang = "de"
    world.start()
    assert world.icon.entry("open").text == "LocalBib öffnen"
    assert world.icon.notices[0][0] == "LocalBib läuft im Infobereich"


def test_a_missing_key_shows_as_its_path(world, tmp_path):
    locales = tmp_path / "locales"
    locales.mkdir()
    (locales / "core.en.js").write_text(
        '(function (root) {\n    root.LB_I18N.en = {\n'
        '        "tray.open": "Open LocalBib",\n    };\n})(globalThis);\n',
        encoding="utf-8",
    )
    world.locales = locales
    world.start()
    assert world.icon.entry("open").text == "Open LocalBib"
    assert world.icon.entry("quit").text == "\u27e6tray.quit\u27e7"
    assert world.icon.tooltip == "\u27e6tray.running\u27e7"


def test_an_unknown_language_shows_every_key_as_its_path():
    catalog = ui_catalog.Catalog(str(LOCALES))
    assert catalog.text("xx", "tray.open") == "\u27e6tray.open\u27e7"


@pytest.mark.parametrize("name", ["core.en.js", "core.de.js"])
def test_every_text_the_starter_reads_is_in_both_catalogs(name):
    entries = ui_catalog.parse((LOCALES / name).read_text(encoding="utf-8"))
    missing = [key for key in starter.TEXT_KEYS if key not in entries]
    assert missing == []
    assert all(entries[key].strip() for key in starter.TEXT_KEYS)


def test_parse_reads_json_escapes_and_skips_javascript():
    text = (
        "// comment\n(function (root) {\n"
        '    "tray.a": "say \\"hi\\"",\n'
        '    "tray.b": "two\\nlines \\\\ done",\n'
        '    "other.c": "not wanted"\n'
        "    root.x = { \"tray.d\": 1 };\n"
    )
    assert ui_catalog.parse(text, prefix="tray.") == {
        "tray.a": 'say "hi"', "tray.b": "two\nlines \\ done",
    }


def test_the_catalog_parse_sees_every_entry_of_the_real_files():
    """Every quoted-key line of both files must be readable - an entry the
    starter cannot see would render as its path in the tray."""
    for name in ("core.en.js", "core.de.js"):
        lines = [line for line in (LOCALES / name).read_text(encoding="utf-8").splitlines()
                 if line.strip().startswith('"')]
        assert len(ui_catalog.parse("\n".join(lines))) == len(lines)


# ---------------------------------------------------------------------------
# The entry point: only the Windows .exe takes this path
# ---------------------------------------------------------------------------

def test_only_the_frozen_windows_build_hands_over_to_the_starter():
    """``python webapp.py`` keeps its console and gets no icon (#214)."""
    source = (REPO_ROOT / "webapp.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    guard = main.index('if _FROZEN and sys.platform == "win32":')
    assert guard < main.index("import starter") < main.index("uvicorn.run(app")
