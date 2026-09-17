"""Starting SpikeSight when Windows starts.

Implemented as a value under ``HKCU\\...\\CurrentVersion\\Run``, which is the
per-user, no-admin-required way to do this. Nothing is written to HKLM, no
scheduled task is created, and no service is installed - so removing it is a
single registry value, and Windows' own Startup Apps screen can disable it too.

Off unless the user asks for it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SpikeSight"
#: The name this app used to run under. Left alone it would start a second
#: copy - the old one - at every sign-in.
LEGACY_VALUE_NAME = "ValScout"


def _winreg():
    try:
        import winreg
    except ImportError:  # pragma: no cover - not Windows
        return None
    return winreg


def launch_command() -> str | None:
    """The command Windows should run at sign-in, or None if we cannot tell.

    Packaged, that is the .exe itself. From source it is the virtual
    environment's ``pythonw.exe`` running the package, so no console flashes up.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'

    executable = Path(sys.executable).resolve()
    windowless = executable.with_name("pythonw.exe")
    interpreter = windowless if windowless.exists() else executable
    return f'"{interpreter}" -m spikesight'


def is_enabled() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("Could not read the Run key: %s", exc)
        return False


def current_command() -> str | None:
    winreg = _winreg()
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except (FileNotFoundError, OSError):
        return None


def set_enabled(enabled: bool) -> bool:
    """Add or remove the entry. Returns the state actually achieved."""
    winreg = _winreg()
    if winreg is None:
        return False

    if not enabled:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
            log.info("Removed SpikeSight from Windows startup.")
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Could not remove the startup entry: %s", exc)
        return False

    command = launch_command()
    if not command:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        log.info("SpikeSight will start with Windows: %s", command)
        return True
    except OSError as exc:
        log.warning("Could not add the startup entry: %s", exc)
        return False


def remove_legacy() -> None:
    """Delete the startup entry an old ValScout install left behind."""
    winreg = _winreg()
    if winreg is None:
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
        log.info("Removed the old ValScout startup entry.")
    except FileNotFoundError:
        pass
    except OSError:
        log.debug("Could not check for the old startup entry", exc_info=True)


def sync(desired: bool) -> bool:
    """Make the registry match the setting, and report what is now true.

    Also repairs a stale entry: if the app has been moved or rebuilt, the
    recorded command will point somewhere that no longer exists.
    """
    if desired and is_enabled():
        wanted = launch_command()
        if wanted and current_command() != wanted:
            log.info("Startup entry pointed elsewhere; updating it.")
            return set_enabled(True)
        return True
    if desired == is_enabled():
        return desired
    return set_enabled(desired)
