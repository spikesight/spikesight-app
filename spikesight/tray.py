"""A notification-area (system tray) icon.

Written directly against user32/shell32 with ctypes rather than pulling in
pystray, which would drag Pillow into the runtime bundle for about ten
megabytes. We already ship a multi-resolution ``.ico``, and ``LoadImageW`` turns
that into an HICON in one call.

Win32 requires the message loop to run on the thread that created the window,
so the whole icon lives on its own thread and callbacks are invoked from there.
Anything those callbacks touch must be safe to call off the main thread.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# --- Win32 plumbing -------------------------------------------------------

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE, LR_SHARED = 0x0010, 0x0040, 0x8000

MF_STRING, MF_CHECKED, MF_SEPARATOR, MF_DISABLED, MF_GRAYED = 0x0, 0x8, 0x800, 0x2, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

IDI_APPLICATION = 32512


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Every prototype is declared. Without argtypes ctypes guesses, and a 64-bit
# HINSTANCE then overflows the int it guessed at.
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

user32.LoadImageW.restype = wintypes.HICON
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]

user32.CreatePopupMenu.restype = wintypes.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, WPARAM, wintypes.LPCWSTR]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


@dataclass
class MenuItem:
    label: str
    action: Callable[[], None] | None = None
    checked: bool | None = None
    enabled: bool = True
    default: bool = False

    @staticmethod
    def separator() -> "MenuItem":
        return MenuItem(label="-")


class TrayIcon:
    """A tray icon whose menu is rebuilt on every right-click.

    Rebuilding means checkmarks always reflect live state, with no need to
    push updates into the icon when a setting changes elsewhere.
    """

    _class_registered = False
    _class_name = "SpikeSightTrayWindow"

    def __init__(
        self,
        icon_path,
        tooltip: str,
        menu: Callable[[], list[MenuItem]],
        on_activate: Callable[[], None] | None = None,
    ) -> None:
        self._icon_path = str(icon_path)
        self._tooltip = tooltip[:127]
        self._menu = menu
        self._on_activate = on_activate

        self._hwnd = None
        self._hicon = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._commands: dict[int, Callable[[], None]] = {}
        # ctypes callbacks must outlive the window, or Python will collect the
        # trampoline while Windows still holds the pointer.
        self._wndproc = WNDPROC(self._handle_message)
        self.failed = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(
            target=self._run, name="spikesight-tray", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout)
        return not self.failed and self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3)

    # -- thread body -------------------------------------------------------

    def _run(self) -> None:
        try:
            self._create_window()
            self._add_icon()
        except Exception as exc:  # noqa: BLE001 - the tray is never essential
            log.warning("Could not create the tray icon: %s", exc)
            self.failed = True
            self._ready.set()
            return

        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _create_window(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        if not TrayIcon._class_registered:
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = instance
            window_class.lpszClassName = TrayIcon._class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError(f"RegisterClassW failed: {ctypes.get_last_error()}")
            TrayIcon._class_registered = True
            # Keep the class alive for the process lifetime.
            TrayIcon._class_ref = window_class

        self._hwnd = user32.CreateWindowExW(
            0, TrayIcon._class_name, "SpikeSight", 0,
            0, 0, 0, 0, None, None, instance, None,
        )
        if not self._hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")

    def _load_icon(self):
        handle = user32.LoadImageW(
            None, self._icon_path, IMAGE_ICON, 0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if not handle:
            log.debug("Falling back to the stock application icon.")
            # IDI_APPLICATION is a MAKEINTRESOURCE ordinal, not a string.
            handle = user32.LoadIconW(
                None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR)
            )
        return handle

    def _icon_data(self, flags: int) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._hicon
        data.szTip = self._tooltip
        return data

    def _add_icon(self) -> None:
        self._hicon = self._load_icon()
        data = self._icon_data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError(f"Shell_NotifyIconW failed: {ctypes.get_last_error()}")

    def _remove_icon(self) -> None:
        if not self._hwnd:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))

    # -- messages ----------------------------------------------------------

    def _handle_message(self, hwnd, message, wparam, lparam) -> int:
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP and self._on_activate:
                self._safely(self._on_activate)
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_COMMAND:
            action = self._commands.get(wparam & 0xFFFF)
            if action:
                self._safely(action)
            return 0
        if message == WM_CLOSE:
            self._remove_icon()
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _safely(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:  # noqa: BLE001 - a bad menu handler must not kill the loop
            log.exception("Tray menu action failed")

    def _show_menu(self) -> None:
        try:
            items = self._menu()
        except Exception:  # noqa: BLE001
            log.exception("Could not build the tray menu")
            return

        menu = user32.CreatePopupMenu()
        if not menu:
            return
        self._commands.clear()

        for index, item in enumerate(items, start=1):
            if item.label == "-":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING
            if item.checked:
                flags |= MF_CHECKED
            if not item.enabled:
                flags |= MF_DISABLED | MF_GRAYED
            user32.AppendMenuW(menu, flags, index, item.label)
            if item.default:
                user32.SetMenuDefaultItem(menu, index, 0)
            if item.action:
                self._commands[index] = item.action

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Required, or the menu refuses to dismiss when clicked away from.
        user32.SetForegroundWindow(self._hwnd)
        chosen = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y,
            0, self._hwnd, None,
        )
        user32.DestroyMenu(menu)
        if chosen:
            action = self._commands.get(chosen)
            if action:
                self._safely(action)
