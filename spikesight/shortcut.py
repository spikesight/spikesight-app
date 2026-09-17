"""Creating a desktop shortcut, from inside the app.

This used to be a loose ``Create Desktop Shortcut.bat`` in the zip. That was a
mistake: a stray batch file that shells out to PowerShell to write a ``.lnk`` is
precisely the shape of a malware dropper, so antivirus takes an interest in it -
holding it open, occasionally quarantining it, and making the folder awkward to
delete. Doing the same work in-process through the shell's own COM interface
avoids the whole problem and is one fewer file to explain.

``IShellLinkW`` is driven through ctypes rather than pywin32, which would be a
sizeable dependency for one function.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import POINTER, byref, c_void_p, wintypes
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

CLSCTX_INPROC_SERVER = 1
CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"

# Vtable slots, counting the three IUnknown entries that come first.
_SET_DESCRIPTION = 7
_SET_WORKING_DIRECTORY = 9
_SET_ICON_LOCATION = 17
_SET_PATH = 20
_PERSIST_SAVE = 6
_RELEASE = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def _guid(text: str) -> GUID:
    value = GUID()
    if ctypes.windll.ole32.CLSIDFromString(text, byref(value)) != 0:
        raise OSError(f"Bad GUID: {text}")
    return value


def _call(interface, slot: int, *args, argtypes=()):
    """Invoke a COM method by vtable slot."""
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    result = prototype(vtable[slot])(interface, *args)
    if result != 0:
        raise OSError(f"COM call {slot} failed: 0x{result & 0xFFFFFFFF:08x}")


def _release(interface) -> None:
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p)))[0]
    ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[_RELEASE])(interface)


def target_command() -> tuple[str, str]:
    """``(executable, arguments)`` for whatever form SpikeSight is running in."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), ""

    executable = Path(sys.executable).resolve()
    windowless = executable.with_name("pythonw.exe")
    interpreter = windowless if windowless.exists() else executable
    return str(interpreter), "-m spikesight"


def desktop_directory() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        # OneDrive redirects the Desktop on a lot of machines.
        redirected = Path(profile) / "OneDrive" / "Desktop"
        if redirected.is_dir():
            return redirected
        return Path(profile) / "Desktop"
    return Path.home() / "Desktop"


def create_desktop_shortcut(name: str = "SpikeSight") -> Path:
    """Write ``<Desktop>/<name>.lnk`` pointing at this SpikeSight. Returns it."""
    if sys.platform != "win32":
        raise OSError("Shortcuts are a Windows thing.")

    target, arguments = target_command()
    # Frozen, the exe's own folder is right. From source it has to be the
    # project root, or "-m spikesight" has nothing to import.
    working_directory = str(
        Path(target).parent if getattr(sys, "frozen", False) else paths.PROJECT_ROOT
    )
    destination = desktop_directory() / f"{name}.lnk"
    destination.parent.mkdir(parents=True, exist_ok=True)

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    link = c_void_p()
    try:
        result = ole32.CoCreateInstance(
            byref(_guid(CLSID_SHELLLINK)), None, CLSCTX_INPROC_SERVER,
            byref(_guid(IID_ISHELLLINKW)), byref(link),
        )
        if result != 0:
            raise OSError(f"CoCreateInstance failed: 0x{result & 0xFFFFFFFF:08x}")

        _call(link, _SET_PATH, target, argtypes=(wintypes.LPCWSTR,))
        _call(link, _SET_WORKING_DIRECTORY, working_directory,
              argtypes=(wintypes.LPCWSTR,))
        _call(link, _SET_DESCRIPTION, "Read-only VALORANT lobby scout",
              argtypes=(wintypes.LPCWSTR,))
        if arguments:
            _call(link, 11, arguments, argtypes=(wintypes.LPCWSTR,))  # SetArguments
        # A packaged build already has the icon in its resources, so point at
        # the exe rather than a file buried in _internal.
        icon = target if getattr(sys, "frozen", False) else str(paths.ICON_FILE)
        if Path(icon).exists():
            _call(link, _SET_ICON_LOCATION, icon, 0,
                  argtypes=(wintypes.LPCWSTR, ctypes.c_int))

        persist = c_void_p()
        _call(link, 0, byref(_guid(IID_IPERSISTFILE)), byref(persist),
              argtypes=(POINTER(GUID), POINTER(c_void_p)))  # QueryInterface
        try:
            _call(persist, _PERSIST_SAVE, str(destination), True,
                  argtypes=(wintypes.LPCWSTR, wintypes.BOOL))
        finally:
            _release(persist)
    finally:
        if link:
            _release(link)
        ole32.CoUninitialize()

    log.info("Created desktop shortcut at %s", destination)
    return destination
