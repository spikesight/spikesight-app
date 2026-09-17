"""Opening SpikeSight as a desktop window rather than a browser tab.

Windows always has Edge, and Chromium's ``--app`` mode gives a chromeless
window that looks and behaves like a native app: no address bar, no tabs, its
own taskbar entry and its own icon.

The important detail is ``--user-data-dir``. Without it, launching Edge hands
the URL to whatever Edge process is already running and our subprocess exits
immediately - so we would have no way to tell when the user closed the window.
With a private profile directory we own the process, which lets the launcher
shut the server down when the window closes. That is what makes "close the
window" mean "quit the app", which is what anyone would expect.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: Private browser profile, so the window never touches the user's own browser.
PROFILE_DIR = paths.DATA_DIR / "window"

#: Render the UI on the CPU instead of the graphics card.
#:
#: Chromium normally keeps a Direct3D device open and composites through the
#: GPU. That is invisible on the desktop and a real problem next to a game: a
#: second process holding a device can push a game out of exclusive
#: fullscreen, and it does not necessarily get it back when the window closes,
#: which is how "my FPS dropped and stayed down" happens with nothing visible
#: on screen. A scoreboard is text and small images, so the GPU buys us
#: nothing here and costs somebody a game.
#: Also: cap the profile. Chromium will happily cache hundreds of megabytes
#: for a page served from localhost that never changes.
BROWSER_FLAGS = [
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disk-cache-size=8388608",
    "--media-cache-size=1048576",
]


# --- controlling the window once it exists --------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True) if _IS_WINDOWS else None

if _user32 is not None:
    _ENUM_PROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t
    )
    _user32.EnumWindows.argtypes = [_ENUM_PROC, ctypes.c_ssize_t]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]

SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOW, SW_MINIMIZE, SW_RESTORE = 0, 4, 5, 6, 9

#: Chromium's top-level window class. Every Edge/Chrome/Brave window uses it.
_CHROMIUM_CLASS = "Chrome_WidgetWin_1"


def find_app_window(title: str = "SpikeSight", include_hidden: bool = False):
    """HWND of our app window, or None.

    Matched on the Chromium window class plus the page title. The window is
    running in a browser process we do not own the handle table of, so there is
    no cleaner handle to ask for.
    """
    if _user32 is None:
        return None

    found: list[int] = []

    def visit(hwnd, _lparam):
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, buffer, 64)
        if buffer.value != _CHROMIUM_CLASS:
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, text, length + 1)
        if text.value.strip() == title:
            found.append(hwnd)
            return False
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(visit), 0)
    except OSError as exc:  # pragma: no cover - defensive
        log.debug("EnumWindows failed: %s", exc)
    return found[0] if found else None


def window_exists(hwnd) -> bool:
    return bool(hwnd and _user32 is not None and _user32.IsWindow(hwnd))


def is_minimized(hwnd) -> bool:
    return bool(hwnd and _user32 is not None and _user32.IsIconic(hwnd))


def hide_window(hwnd) -> None:
    if hwnd and _user32 is not None:
        _user32.ShowWindow(hwnd, SW_HIDE)


def minimize_window(hwnd) -> None:
    if hwnd and _user32 is not None:
        _user32.ShowWindow(hwnd, SW_MINIMIZE)


def unminimize_without_focus(hwnd) -> None:
    """Put the window back without pulling the user out of anything.

    ``SW_SHOWNOACTIVATE`` restores size and position but leaves the active
    window alone - important when the game may still be in front.
    """
    if hwnd and _user32 is not None:
        _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def restore_window(hwnd) -> None:
    """Un-hide, un-minimize and bring to the front."""
    if not hwnd or _user32 is None:
        return
    _user32.ShowWindow(hwnd, SW_SHOW)
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)


@dataclass
class AppWindow:
    """A window we launched. ``process`` is None when we do not own it."""

    process: subprocess.Popen | None
    kind: str

    @property
    def owned(self) -> bool:
        return self.process is not None

    def wait(self) -> None:
        if self.process is not None:
            self.process.wait()

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
            except OSError:
                pass


def candidate_browsers() -> list[tuple[str, Path]]:
    """Chromium builds that support --app, most preferred first."""
    program_files = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    relative = [
        ("Edge", r"Microsoft\Edge\Application\msedge.exe"),
        ("Chrome", r"Google\Chrome\Application\chrome.exe"),
        ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]

    found: list[tuple[str, Path]] = []
    for name, tail in relative:
        for root in program_files:
            if not root:
                continue
            candidate = Path(root) / tail
            if candidate.is_file():
                found.append((name, candidate))
                break
    # Last resort: whatever is on PATH.
    for name, exe in (("Edge", "msedge"), ("Chrome", "chrome")):
        if not any(n == name for n, _ in found):
            located = shutil.which(exe)
            if located:
                found.append((name, Path(located)))
    return found


def open_window(url: str, width: int = 1500, height: int = 950) -> AppWindow:
    """Open ``url`` as an app window, falling back to the default browser."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    for name, exe in candidate_browsers():
        command = [
            str(exe),
            f"--app={url}",
            f"--user-data-dir={PROFILE_DIR}",
            f"--window-size={width},{height}",
            "--no-first-run",
            "--no-default-browser-check",
            # This profile exists only to host our window; none of the usual
            # browser services need to run in it.
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            *BROWSER_FLAGS,
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.debug("Could not launch %s: %s", name, exc)
            continue

        log.info("Opened the SpikeSight window using %s.", name)
        return AppWindow(process=process, kind=name)

    log.info("No Chromium browser found; opening your default browser instead.")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a failed launch is cosmetic
        log.warning("Could not open a browser. Go to %s yourself.", url)
    return AppWindow(process=None, kind="default browser")
