"""The pre-match overlay: a small panel over the game during agent select.

How it is built, and why it is safe to run next to VALORANT:

* It is an ordinary top-level window in its own process - a Chromium ``--app``
  window showing ``/overlay``. Nothing is injected into the game, nothing reads
  or writes game memory, and there is no input hook. Vanguard sees a browser
  window, the same as it sees Discord or a stream deck app.
* It is **click-through** (``WS_EX_TRANSPARENT``) and **never activates**
  (``WS_EX_NOACTIVATE``), so it cannot swallow a click meant for the agent
  grid or pull focus away from the game. The one exception is *move mode*,
  which the user turns on from Settings to drag the panel somewhere else and
  turns off with a Done button; it is never on unless asked for.
* It shows the same read-only data as the main window, for your own team only.
  The enemy team is hidden during agent select and stays hidden here.
* Being a separate window, it can only appear over the game when VALORANT runs
  in *Windowed Fullscreen* or *Windowed* mode. Exclusive fullscreen owns the
  screen outright; that is a Windows rule, not something to work around.

Always-on-top is claimed the moment the window appears, and only then.
Windows lets a process put another process's window into the topmost band
just after that window is created; later the same call reports success and
does nothing, which left the panel rendering *behind* the game. The flag is
sticky once set, so it survives being hidden between matches - and if it is
ever missing when the panel is needed, the window is thrown away and made
again rather than shown where nobody can see it.

The window is also kept off the taskbar. ``WS_EX_TOOLWINDOW`` stops a
button being created, but Windows has already made one by the time a
window we did not create exists, so the button is removed explicitly
through ``ITaskbarList`` as well - otherwise a mystery second icon sits
there for as long as the app runs.

The window uses a browser profile of its own. Sharing the main window's would
make Chromium hand the launch to the process that already owns that profile,
leaving us with no process to close - and a click-through, always-on-top
window that nobody can close is the one failure this module must not have.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from . import appwindow, paths

log = logging.getLogger(__name__)

TITLE = "SpikeSight Overlay"
PROFILE_DIR = paths.DATA_DIR / "overlay-window"

#: Panel width in CSS pixels. Height follows the content (see ``set_metrics``).
WIDTH = 340
DEFAULT_HEIGHT = 520
#: Distance from the screen edge, and from the top, as fractions of the screen.
EDGE_MARGIN = 14
TOP_FRACTION = 0.13
#: Uniform window opacity. Chromium cannot do per-pixel alpha on a top-level
#: window, so the whole panel is slightly see-through instead.
OPACITY = 236

#: Don't rebuild the window more often than this while trying for topmost.
REBUILD_COOLDOWN = 15.0

#: Where the window waits while it is not wanted, and how long it is given to
#: paint there before it slides into view. Showing a Chromium window that has
#: not painted yet means a grey box with a title bar on it for a moment, which
#: looks broken; off screen, nobody sees that happen.
#: How long to wait for a freshly launched window to appear before giving up
#: and letting the caller's loop retry.
ADOPT_WAIT = 5.0

OFFSCREEN = -32000
WARMUP_SECONDS = 0.35
#: Give up waiting for the page to report its size and show it anyway.
WARMUP_LIMIT = 2.0

HOTKEY_ID = 0x5653  # "VS"
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
VK_O = 0x4F
HOTKEY_LABEL = "Ctrl+Shift+O"

_IS_WINDOWS = sys.platform == "win32"

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x2
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 0, 4, 7
WM_CLOSE, WM_HOTKEY, WM_QUIT = 0x0010, 0x0312, 0x0012
MONITOR_DEFAULTTOPRIMARY = 1
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _LONG_PTR = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = _LONG_PTR
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, _LONG_PTR]
    _user32.SetWindowLongPtrW.restype = _LONG_PTR
    _user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD
    ]
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _user32.MonitorFromPoint.restype = wintypes.HMONITOR
    _user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
    ]
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _gdi32 = ctypes.WinDLL("gdi32")
    _gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    _gdi32.CreateRectRgn.restype = wintypes.HRGN
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _kernel32 = ctypes.WinDLL("kernel32")
else:  # pragma: no cover - the overlay is a Windows feature
    _user32 = None
    _gdi32 = None
    _kernel32 = None


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if _IS_WINDOWS:
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]


class _PhysicalPixels:
    """Talk to the window in real pixels for the duration of a block.

    Our process is not DPI aware, so on a 150% display Windows would scale
    every coordinate we pass. Chromium's window is per-monitor aware, so
    doing the maths in physical pixels on both sides is the only way the
    panel lands where it was asked to.
    """

    def __enter__(self):
        self._previous = _user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        )
        return self

    def __exit__(self, *exc):
        if self._previous:
            _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self._previous))


def _primary_monitor() -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the primary display, where games run."""
    monitor = _user32.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    rect = info.rcMonitor
    return rect.left, rect.top, rect.right, rect.bottom


@dataclass(frozen=True)
class Frame:
    """What Chromium draws around the page, in CSS pixels.

    An ``--app`` window paints its own title strip and borders *inside* the
    window, so removing the native frame does not remove them. The page
    reports its outer and inner sizes, which is enough to work them out, and
    the window is then clipped to the page alone.
    """

    side: float = 7.0
    top: float = 30.0

    @classmethod
    def from_metrics(cls, metrics: dict | None) -> "Frame":
        if not metrics:
            return cls()
        side = max(0.0, (metrics["outerWidth"] - metrics["innerWidth"]) / 2)
        top = max(0.0, metrics["outerHeight"] - metrics["innerHeight"] - side)
        # Nonsense (a page mid-resize) falls back to the measured defaults.
        if side > 40 or top > 120:
            return cls()
        return cls(side=side, top=top)


@dataclass(frozen=True)
class Placement:
    """Window rectangle and the visible region inside it, physical pixels."""

    x: int
    y: int
    width: int
    height: int
    region: tuple[int, int, int, int]


def placement(
    side: str,
    content_height: float,
    frame: Frame,
    scale: float,
    screen: tuple[int, int, int, int],
    position: tuple[float, float] | None = None,
) -> Placement:
    """Where the panel goes.

    ``position`` is a spot the user dragged it to: the panel's top-left
    corner as a fraction of the screen, so it survives a resolution change.
    Without one, the panel docks to ``side``.
    """
    left, top, right, bottom = screen
    margin = round(EDGE_MARGIN * scale)
    content_w = round(WIDTH * scale)
    if position is None:
        content_y = top + round((bottom - top) * TOP_FRACTION)
        content_x = right - content_w - margin if side == "right" else left + margin
    else:
        content_x = left + round(position[0] * (right - left))
        content_y = top + round(position[1] * (bottom - top))
        # Keep the whole panel reachable, whatever the saved spot says.
        content_x = max(left, min(content_x, right - content_w))
        content_y = max(top, min(content_y, bottom - margin - round(80 * scale)))
    # Never run off the bottom of the screen.
    content_h = max(1, min(round(content_height * scale), bottom - content_y - margin))

    frame_side = round(frame.side * scale)
    frame_top = round(frame.top * scale)
    return Placement(
        x=content_x - frame_side,
        y=content_y - frame_top,
        width=content_w + 2 * frame_side,
        height=content_h + frame_top + frame_side,
        region=(frame_side, frame_top, frame_side + content_w, frame_top + content_h),
    )


def position_from(
    content_x: int, content_y: int, screen: tuple[int, int, int, int]
) -> tuple[float, float]:
    """The inverse of ``placement``: a screen spot as saved fractions."""
    left, top, right, bottom = screen
    return (
        round((content_x - left) / max(1, right - left), 4),
        round((content_y - top) / max(1, bottom - top), 4),
    )


CLSID_TASKBARLIST = "{56FDF344-FD6D-11D0-958A-006097C9A090}"
IID_ITASKBARLIST = "{56FDF342-FD6D-11D0-958A-006097C9A090}"
CLSCTX_INPROC_SERVER = 1
_HRINIT, _DELETETAB, _RELEASE = 3, 5, 2


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def _guid(text: str) -> _GUID:
    value = _GUID()
    ctypes.windll.ole32.CLSIDFromString(text, ctypes.byref(value))
    return value


def _com_call(interface, slot: int, *args, argtypes=()) -> int:
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return prototype(vtable[slot])(interface, *args)


def remove_from_taskbar(hwnd) -> None:
    """Take an existing taskbar button away.

    The tool-window style prevents a button; this removes the one Windows
    made before we could set it. Failure is not worth reporting: the worst
    case is a spare icon, and the app still works.
    """
    if _user32 is None:
        return
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    taskbar = ctypes.c_void_p()
    try:
        created = ole32.CoCreateInstance(
            ctypes.byref(_guid(CLSID_TASKBARLIST)), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(_guid(IID_ITASKBARLIST)), ctypes.byref(taskbar),
        )
        if created != 0 or not taskbar:
            return
        _com_call(taskbar, _HRINIT)
        _com_call(taskbar, _DELETETAB, wintypes.HWND(hwnd), argtypes=(wintypes.HWND,))
    except OSError:
        log.debug("Could not remove the overlay's taskbar button", exc_info=True)
    finally:
        if taskbar:
            vtable = ctypes.cast(
                taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
            )[0]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                vtable[_RELEASE]
            )(taskbar)
        ole32.CoUninitialize()


def is_topmost(hwnd) -> bool:
    return bool(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def _claim_topmost(hwnd) -> bool:
    """Put the window in the topmost band. Only works just after creation."""
    _user32.SetWindowPos(
        hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )
    return is_topmost(hwnd)


def _style_as_overlay(hwnd, interactive: bool = False) -> None:
    """Strip the frame; make it click-through, unfocusable and topmost.

    ``interactive`` is move mode: the panel takes the mouse so it can be
    dragged, and is fully opaque so it is obvious it is not the usual state.
    """
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style &= ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    style |= WS_POPUP
    _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, ctypes.c_ssize_t(style).value)

    ex = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex &= ~WS_EX_APPWINDOW
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    if interactive:
        ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
    else:
        ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
    _user32.SetLayeredWindowAttributes(hwnd, 0, 255 if interactive else OPACITY, LWA_ALPHA)


def find_overlay_window():
    """Our overlay's HWND, visible or not."""
    if _user32 is None:
        return None
    return appwindow.find_app_window(TITLE, include_hidden=True)


class OverlayWindow:
    """Owns the overlay browser process and decides where the panel sits.

    Every public method is safe to call from the desktop session's loop, and
    each one is a no-op when there is nothing to act on.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._process: subprocess.Popen | None = None
        self._hwnd = None
        self._launched_at = 0.0
        self._visible = False
        self._content_height = float(DEFAULT_HEIGHT)
        self._frame = Frame()
        self._side = "right"
        self._position: tuple[float, float] | None = None
        self._interactive = False
        #: Physical-pixel content origin captured when a drag starts.
        self._drag_origin: tuple[int, int] | None = None
        self._placed_at: Placement | None = None
        self._scale = 1.0
        #: Set when the window came up without the topmost flag and being
        #: rebuilt did not help; the panel is then shown as best we can.
        self._topmost_failures = 0
        self._rebuilt_at = 0.0
        #: Set while the window is up off screen, painting before it is shown.
        self._warming_since: float | None = None
        #: Set once the page has reported a size for what it is showing now.
        self._sized = False
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def ensure_started(self) -> None:
        """Launch the overlay browser if it is not running yet.

        The window opens far off screen and is hidden the moment it is found,
        so launching never flashes a panel or a frame at anyone.
        """
        if _user32 is None:
            return
        with self._lock:
            if self.running:
                self._adopt()
                return
            # A previous run that crashed can leave one behind. Close it
            # rather than stacking a second.
            # A crash (or a force-kill) can leave one behind. Close it and
            # wait for it to actually go: adopting a dying window would mean
            # styling something that is about to disappear, and the new one
            # would come up looking broken.
            stale = find_overlay_window()
            if stale:
                _user32.PostMessageW(stale, WM_CLOSE, 0, 0)
                for _ in range(30):
                    if not find_overlay_window():
                        break
                    time.sleep(0.1)

            exe = next(iter(appwindow.candidate_browsers()), None)
            if exe is None:
                log.warning("No Chromium browser found; the overlay needs Edge or Chrome.")
                return
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            command = [
                str(exe[1]),
                f"--app={self._url}",
                f"--user-data-dir={PROFILE_DIR}",
                f"--window-size={WIDTH + 14},{DEFAULT_HEIGHT + 37}",
                "--window-position=-32000,-32000",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                # Keep the page's clock running while it is hidden so it is
                # up to date the moment it appears. Occluded-window
                # backgrounding is deliberately left alone: that one keeps a
                # hidden window compositing, which is exactly the sort of
                # thing that costs a game frames for no benefit.
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                # Chromium treats a window parked off screen as occluded and
                # stops painting it, which would defeat the whole point of
                # warming it up out of sight.
                "--disable-features=CalculateNativeWinOcclusion",
                # The panel must not cost the game anything to draw.
                *appwindow.BROWSER_FLAGS,
            ]
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # Minimized-and-inactive is the one launch state Chromium honors
            # without taking the foreground - which matters if this ever
            # happens while a game has focus.
            startup.wShowWindow = SW_SHOWMINNOACTIVE
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startup,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                log.warning("Could not start the overlay window: %s", exc)
                self._process = None
                return
            self._hwnd = None
            self._visible = False
            self._launched_at = time.monotonic()
            log.info("Overlay window starting (%s).", exe[0])

            # Take it over the moment it appears rather than waiting for the
            # next pass of the caller's loop: until it is adopted it is a
            # visible, unstyled browser window sitting in the taskbar.
            deadline = time.monotonic() + ADOPT_WAIT
            while time.monotonic() < deadline and not self._hwnd:
                self._adopt()
                if self._hwnd:
                    break
                time.sleep(0.05)

    def _adopt(self) -> None:
        """Find the window once it has a title, and make it an overlay."""
        if self._hwnd and _user32.IsWindow(self._hwnd):
            return
        self._hwnd = None
        hwnd = find_overlay_window()
        if not hwnd:
            if time.monotonic() - self._launched_at > 30:
                log.warning("Overlay window never appeared; giving up on it.")
                self.stop()
            return
        with _PhysicalPixels():
            _style_as_overlay(hwnd)
            # Now or never: this only works while the window is new.
            topmost = _claim_topmost(hwnd)
            _user32.ShowWindow(hwnd, SW_HIDE)
        # The style stops a *new* button; this removes the one that already
        # exists, because the window was briefly visible while starting.
        remove_from_taskbar(hwnd)
        self._hwnd = hwnd
        self._visible = False
        if topmost:
            self._topmost_failures = 0
            log.info("Overlay window ready.")
        else:
            self._topmost_failures += 1
            log.warning(
                "Overlay window came up without always-on-top (attempt %s); "
                "it would draw behind the game.", self._topmost_failures,
            )

    def stop(self) -> None:
        with self._lock:
            hwnd = self._hwnd or find_overlay_window()
            if hwnd and _user32 is not None:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        self._process.kill()
                    except OSError:
                        pass
            self._process = None
            self._hwnd = None
            self._visible = False
            self._warming_since = None

    # -- showing -----------------------------------------------------------

    def set_metrics(self, metrics: dict | None) -> None:
        """The page reports its size; the window is refitted if it changed."""
        if not metrics:
            return
        height = max(60.0, min(float(metrics["contentHeight"]), 2000.0))
        frame = Frame.from_metrics(metrics)
        with self._lock:
            # Any word from the page counts as "it has drawn something".
            self._sized = True
            if height == self._content_height and frame == self._frame:
                return
            self._content_height = height
            self._frame = frame
            if self._warming_since is not None:
                # Resize out of sight and give it another moment: a window
                # that grows after it is shown paints the new area a frame
                # late, which is the empty strip people notice.
                self._place(onscreen=False)
                self._warming_since = time.monotonic()
            elif self._visible and self._drag_origin is None:
                self._place(onscreen=True)

    def update(
        self,
        show: bool,
        side: str,
        position: tuple[float, float] | None = None,
        interactive: bool = False,
    ) -> None:
        with self._lock:
            if not self.running:
                return
            self._adopt()
            if not self._hwnd:
                return
            side = "right" if side == "right" else "left"

            if not show:
                if self._visible or self._warming_since is not None:
                    _user32.ShowWindow(self._hwnd, SW_HIDE)
                    self._visible = False
                    self._warming_since = None
                    self._drag_origin = None
                if self._interactive:
                    # Leaving move mode: back to click-through straight away,
                    # not just the next time the panel is shown.
                    self._interactive = False
                    with _PhysicalPixels():
                        _style_as_overlay(self._hwnd, False)
                return

            if not is_topmost(self._hwnd) and self._rebuild_for_topmost():
                return

            wanted = (side, position, interactive)
            changed = wanted != (self._side, self._position, self._interactive)
            if changed:
                self._side, self._position, self._interactive = wanted

            if self._warming_since is not None:
                # Painting off screen. It is ready when it has had a moment
                # *and* the page has said how big it is for what it is showing
                # now - otherwise it would slide in at the previous size and
                # resize in front of the user.
                waited = time.monotonic() - self._warming_since
                if waited < WARMUP_SECONDS or (not self._sized and waited < WARMUP_LIMIT):
                    return
                self._warming_since = None
                self._place(onscreen=True)
                self._visible = True
                return

            if not self._visible:
                self._sized = False
                self._place(onscreen=False)
                self._warming_since = time.monotonic()
                return

            # Mid-drag the page is steering; do not snap it back.
            if changed and self._drag_origin is None:
                self._place(onscreen=True)

    def _rebuild_for_topmost(self) -> bool:
        """Throw the window away and make a new one that can sit on top.

        Returns True when a rebuild was started, so the caller skips this
        round; the new window is adopted a moment later. After a couple of
        failed attempts it gives up and the panel is shown regardless -
        visible in the wrong place beats never visible at all.
        """
        if self._topmost_failures >= 2:
            return False
        now = time.monotonic()
        if now - self._rebuilt_at < REBUILD_COOLDOWN:
            return False
        self._rebuilt_at = now
        log.info("Overlay lost always-on-top; rebuilding its window.")
        self.stop()
        self.ensure_started()
        return True

    # -- dragging (move mode only) ------------------------------------------

    def begin_drag(self) -> bool:
        with self._lock:
            if not (self._interactive and self._visible and self._placed_at):
                return False
            spot = self._placed_at
            self._drag_origin = (spot.x + spot.region[0], spot.y + spot.region[1])
            return True

    def drag(self, dx_css: float, dy_css: float) -> None:
        """Move by the pointer's travel since ``begin_drag``, in CSS pixels."""
        with self._lock:
            if self._drag_origin is None or not self._hwnd:
                return
            ox, oy = self._drag_origin
            spot = self._placed_at
            x = ox + round(dx_css * self._scale) - spot.region[0]
            y = oy + round(dy_css * self._scale) - spot.region[1]
            with _PhysicalPixels():
                _user32.SetWindowPos(
                    self._hwnd, wintypes.HWND(HWND_TOPMOST), x, y, 0, 0,
                    SWP_NOACTIVATE | SWP_NOSIZE,
                )
            self._placed_at = Placement(x, y, spot.width, spot.height, spot.region)

    def end_drag(self) -> tuple[float, float] | None:
        """Finish a drag; returns the new spot as fractions, to be saved."""
        with self._lock:
            if self._drag_origin is None or not self._placed_at:
                return None
            self._drag_origin = None
            spot = self._placed_at
            with _PhysicalPixels():
                screen = _primary_monitor()
            self._position = position_from(
                spot.x + spot.region[0], spot.y + spot.region[1], screen
            )
            # Snap through placement so a drop past the edge is pulled back.
            self._place(onscreen=True)
            return self._position

    def _place(self, onscreen: bool) -> None:
        """Size, clip and position the window.

        The region is what hides Chromium's own title bar and borders, so it
        has to be in place *before* the window is ever on screen - otherwise
        the frame shows for a frame or two and the panel looks like a broken
        browser window.
        """
        with _PhysicalPixels():
            # Chromium occasionally restores its own frame styles; putting
            # them back on every move is cheap and keeps it click-through.
            _style_as_overlay(self._hwnd, self._interactive)
            scale = (_user32.GetDpiForWindow(self._hwnd) or 96) / 96
            self._scale = scale
            spot = placement(
                self._side, self._content_height, self._frame, scale,
                _primary_monitor(), self._position,
            )
            self._placed_at = spot
            x, y = (spot.x, spot.y) if onscreen else (OFFSCREEN, OFFSCREEN)
            _user32.SetWindowPos(
                self._hwnd, wintypes.HWND(HWND_TOPMOST), x, y,
                spot.width, spot.height,
                SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
            region = _gdi32.CreateRectRgn(*spot.region)
            # On success the window owns the region; only free it on failure.
            if not _user32.SetWindowRgn(self._hwnd, region, True):
                _gdi32.DeleteObject(region)
            # Only now is it safe to look at.
            _user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)


class Hotkey:
    """One global shortcut, delivered on a thread of its own.

    ``RegisterHotKey`` asks Windows to tell us when a key combination is
    pressed. It is not a keyboard hook: nothing else is seen, nothing is
    intercepted, and the game still receives the keys.
    """

    def __init__(self, callback) -> None:
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self) -> bool:
        if _user32 is None:
            return False
        self._thread = threading.Thread(target=self._run, name="overlay-hotkey",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)
        return self.registered

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self.registered = bool(_user32.RegisterHotKey(
            None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_O
        ))
        if not self.registered:
            log.info("%s is taken by another app; the overlay hotkey is off.",
                     HOTKEY_LABEL)
        self._ready.set()
        if not self.registered:
            return
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    try:
                        self._callback()
                    except Exception:  # noqa: BLE001 - never kill the loop
                        log.exception("Overlay hotkey handler failed")
        finally:
            _user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)
        self._thread = None
