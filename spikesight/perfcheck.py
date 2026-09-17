"""Facts about the machine that explain frame-rate complaints.

None of this measures anything SpikeSight does. It collects the handful of
settings that decide whether a *background* window can cost a game frames, so
a report of "my FPS tanked" can be answered with something better than a
guess. Everything here is read-only: display settings, two registry values,
and VALORANT's own config file.

The short version of the problem it is for:

* In **Windowed Fullscreen**, a game hands its frames straight to the screen
  while nothing else is drawing. The moment another window appears, Windows
  composes the desktop instead, and the game gets slower.
* That is supposed to stop when the window goes away. It often does not: the
  game keeps the slow path until its swapchain is remade, which in practice
  means alt-tabbing out and back, or restarting the game.
* **Monitors running at different refresh rates** cause the same thing on
  their own, with no second window involved at all.

So the useful question is never "is SpikeSight heavy" (it is not - see the CPU
figures in the diagnostics panel) but "what mode is this machine in".
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: VALORANT's own video config.
GAME_SETTINGS = (
    Path.home() / "AppData" / "Local" / "VALORANT" / "Saved" / "Config"
    / "WindowsClient" / "GameUserSettings.ini"
)

FULLSCREEN_MODES = {
    "0": "Fullscreen",
    "1": "Windowed Fullscreen",
    "2": "Windowed",
}

ENUM_CURRENT_SETTINGS = -1


class _DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


DISPLAY_DEVICE_ACTIVE = 0x00000001
DISPLAY_DEVICE_PRIMARY = 0x00000004


def monitors() -> list[dict]:
    """Every active display, with the refresh rate it is actually running."""
    if not _IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    found: list[dict] = []
    index = 0
    while True:
        device = _DISPLAY_DEVICE()
        device.cb = ctypes.sizeof(_DISPLAY_DEVICE)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        index += 1
        if not device.StateFlags & DISPLAY_DEVICE_ACTIVE:
            continue
        mode = _DEVMODE()
        mode.dmSize = ctypes.sizeof(_DEVMODE)
        if not user32.EnumDisplaySettingsW(
            device.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)
        ):
            continue
        found.append(
            {
                "name": device.DeviceString,
                "width": int(mode.dmPelsWidth),
                "height": int(mode.dmPelsHeight),
                "refresh": int(mode.dmDisplayFrequency),
                "primary": bool(device.StateFlags & DISPLAY_DEVICE_PRIMARY),
            }
        )
    return found


def _registry_dword(root, path: str, name: str) -> int | None:
    try:
        import winreg

        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return int(value)
    except (OSError, ValueError, ImportError):
        return None


def graphics_settings() -> dict:
    """Two Windows settings that change how games present."""
    if not _IS_WINDOWS:
        return {}
    import winreg

    hags = _registry_dword(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        "HwSchMode",
    )
    # 5 is the documented "disable multi-plane overlays" value people are told
    # to set when they hit flicker or stutter; it also changes present paths.
    overlay_test_mode = _registry_dword(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Dwm",
        "OverlayTestMode",
    )
    return {
        "hardwareGpuScheduling": {1: "off", 2: "on"}.get(hags, "unknown"),
        "multiPlaneOverlays": "disabled" if overlay_test_mode == 5 else "default",
    }


def game_video_settings(path: Path | None = None) -> dict:
    """VALORANT's display mode and frame-rate settings, as it saved them."""
    source = path or GAME_SETTINGS
    wanted = {
        "FullscreenMode": "displayMode",
        "bUseVSync": "vsync",
        "FrameRateLimit": "frameRateLimit",
        "ResolutionSizeX": "width",
        "ResolutionSizeY": "height",
    }
    out: dict = {}
    try:
        text = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {"available": False}

    for line in text.splitlines():
        key, _, value = line.partition("=")
        field = wanted.get(key.strip())
        if field:
            out[field] = value.strip().strip('"')

    mode = out.get("displayMode")
    out["displayMode"] = FULLSCREEN_MODES.get(mode, f"unknown ({mode})")
    if "frameRateLimit" in out:
        try:
            limit = float(out["frameRateLimit"])
            out["frameRateLimit"] = "none" if limit <= 0 else str(int(limit))
        except ValueError:
            pass
    out["available"] = True
    return out


def summary() -> dict:
    """Everything above, plus the plain-language verdict."""
    screens = monitors()
    rates = {m["refresh"] for m in screens}
    game = game_video_settings()
    notes = []
    if len(rates) > 1:
        notes.append(
            "Your monitors run at different refresh rates ("
            + ", ".join(f"{r}Hz" for r in sorted(rates))
            + "). On its own that can cap a game's frame rate, whatever else "
            "is open."
        )
    if game.get("displayMode") == "Windowed Fullscreen":
        notes.append(
            "VALORANT is in Windowed Fullscreen. Any window left visible over "
            "it costs frames while it is up - and the game can stay slow after "
            "you close that window, until you alt-tab out and back."
        )
    elif game.get("displayMode") == "Fullscreen":
        notes.append(
            "VALORANT is in exclusive Fullscreen, where other windows cannot "
            "draw over it or slow it down. The overlay will not show either."
        )
    return {
        "monitors": screens,
        "mixedRefreshRates": len(rates) > 1,
        "graphics": graphics_settings(),
        "game": game,
        "notes": notes,
    }


def log_summary() -> None:
    """One line in the log, so a report comes with its context attached."""
    try:
        data = summary()
    except Exception:  # noqa: BLE001 - diagnostics must never break a match
        log.debug("Could not read performance settings", exc_info=True)
        return
    screens = ", ".join(
        f"{m['width']}x{m['height']}@{m['refresh']}Hz{'*' if m['primary'] else ''}"
        for m in data["monitors"]
    )
    game = data["game"]
    log.info(
        "Display: %s | VALORANT: %s, vsync=%s, fps limit=%s | GPU scheduling: %s, "
        "MPO: %s",
        screens or "unknown",
        game.get("displayMode", "?"),
        game.get("vsync", "?"),
        game.get("frameRateLimit", "?"),
        data["graphics"].get("hardwareGpuScheduling", "?"),
        data["graphics"].get("multiPlaneOverlays", "?"),
    )
