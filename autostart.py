"""Start with Windows: the HKCU Run entry is the one truth (#214, ADR-0023).

LocalBib starts at sign-in when the value ``LocalBib`` exists under the current
user's Run key and Task Manager has not switched it off. There is no copy of
that state anywhere else - not in the ``.env``, not in the app: the Settings
switch, the tray icon's check mark and the installer's task all read and write
this entry through the functions below, so they cannot disagree.

Task Manager does not delete a disabled entry; it records the decision under
``StartupApproved\\Run`` (a binary value whose first byte is odd when the entry
is off). Such an entry counts as **off** here, and switching it on removes
that record again - otherwise LocalBib would claim "on" while Windows skips it.

The rules take the registry as an argument (``Registry``), so tests hand in a
dict-backed fake and never touch the real one. ``WinRegistry`` is the thin real
adapter; it imports ``winreg`` lazily and is only ever built on Windows.
"""

from __future__ import annotations

from typing import Protocol, Union

#: The per-user Run key (HKCU). The installer writes the same key and value
#: name (installer/localbib.iss) - tests/test_release_packaging.py pins that.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: Where Task Manager records "disabled" for an entry of the Run key.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "LocalBib"
#: Marks a launch by Windows at sign-in: the starter opens no browser then.
AUTOSTART_ARG = "--autostart"

RegistryValue = Union[str, bytes, int, None]


class Registry(Protocol):
    """The three registry operations the rules need, all under HKCU."""

    def read(self, key: str, name: str) -> RegistryValue:
        """The value, or ``None`` when the key or the value does not exist."""

    def write_string(self, key: str, name: str, value: str) -> None:
        """Create the key if needed and set ``name`` to a REG_SZ ``value``."""

    def delete(self, key: str, name: str) -> None:
        """Remove ``name``; a missing key or value is not an error."""


def available(frozen: bool, platform: str) -> bool:
    """Only the Windows .exe can start with Windows. A source checkout cannot
    know how it was started (venv, IDE, reloader), so it is not offered there."""
    return bool(frozen) and platform == "win32"


def command_for(exe_path: str) -> str:
    """The Run value: the quoted .exe plus the autostart argument."""
    return f'"{exe_path}" {AUTOSTART_ARG}'


def disabled_in_task_manager(registry: Registry) -> bool:
    data = registry.read(APPROVED_KEY, VALUE_NAME)
    if not isinstance(data, (bytes, bytearray)) or not data:
        return False
    return bool(data[0] & 1)


def is_enabled(registry: Registry) -> bool:
    """On = the Run value exists and Task Manager has not disabled it."""
    value = registry.read(RUN_KEY, VALUE_NAME)
    return bool(value) and not disabled_in_task_manager(registry)


def enable(registry: Registry, exe_path: str) -> None:
    registry.write_string(RUN_KEY, VALUE_NAME, command_for(exe_path))
    # Lifts a Task Manager "disabled": a missing record means enabled.
    registry.delete(APPROVED_KEY, VALUE_NAME)


def disable(registry: Registry) -> None:
    registry.delete(RUN_KEY, VALUE_NAME)
    # A stale "disabled" record would silently veto a later installer task.
    registry.delete(APPROVED_KEY, VALUE_NAME)


def set_enabled(registry: Registry, enabled: bool, exe_path: str) -> bool:
    """Switch on or off and answer the state the registry now holds."""
    if enabled:
        enable(registry, exe_path)
    else:
        disable(registry)
    return is_enabled(registry)


class WinRegistry:
    """The real HKCU adapter. Untested on purpose: tests never touch the
    user's registry, they use a fake with the same three methods."""

    def read(self, key: str, name: str) -> RegistryValue:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                value, _kind = winreg.QueryValueEx(handle, name)
                return value
        except FileNotFoundError:
            return None

    def write_string(self, key: str, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass
