"""Desktop integration: the window, the tray icon, and when to quit.

Three optional behaviors live here, all off by default so the app behaves like
a plain window unless asked otherwise:

* **minimize to tray** - minimizing hides the window instead of leaving a
  taskbar button;
* **close to tray** - closing the window leaves SpikeSight running so it keeps
  following your matches, and the tray icon brings it back;
* **start with Windows** - handled in :mod:`spikesight.autostart`, surfaced in
  the tray menu here;
* **pre-match overlay** - the panel over the game during agent select, driven
  from this loop; see :mod:`spikesight.overlay`;
* **minimize after agent select** - off, like the rest. The enemy team only
  becomes visible once the match starts, so this is exactly when a lot of
  people want the board. Worth turning on for anyone who would rather have
  the frames: a visible window keeps Windows compositing the desktop, which
  stops a Windowed Fullscreen game putting its frames on screen directly.

The tray icon only appears when one of the first two is enabled. With both off
there is nothing extra on screen and closing the window quits, exactly as
before.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from . import appwindow, autostart, overlay, paths, perfcheck
from .config import save_user_settings
from .tray import MenuItem, TrayIcon

log = logging.getLogger(__name__)

#: How long the UI can be absent before we treat the window as closed. A page
#: reload drops the socket briefly, so this needs slack.
UI_GRACE_SECONDS = 8.0
#: If the window never connects at all, stop waiting and say so.
UI_FIRST_CONNECT_TIMEOUT = 90.0
POLL_SECONDS = 0.4
#: After the overlay browser fails to start, wait this long before retrying.
OVERLAY_RETRY_SECONDS = 60.0
#: Move mode switches itself off after this long, in case it is forgotten.
OVERLAY_EDIT_TIMEOUT = 600.0


def match_window_action(state: str, enabled: bool, already: bool) -> str | None:
    """``"minimize"``, ``"restore"`` or nothing, for the window during a match.

    Split out from the session so the rule is testable without a window: it
    is the one piece of this file that decides something rather than calling
    Windows.
    """
    if not enabled:
        # Switched off mid-match: undo it, then leave the window alone.
        return "restore" if already else None
    if state == "INGAME":
        return None if already else "minimize"
    return "restore" if already else None


class DesktopSession:
    """Owns the window and decides when the app should stop running."""

    def __init__(self, cfg, url: str, app, on_settings_changed=None) -> None:
        self._cfg = cfg
        self._url = url
        self._app = app
        self._on_settings_changed = on_settings_changed

        self._window = appwindow.AppWindow(process=None, kind="none")
        self._hwnd = None
        self._hidden = False
        #: Window closed but the app deliberately kept running in the tray.
        self._in_tray = False
        self._tray: TrayIcon | None = None
        self._quit = threading.Event()
        self._first_connect_deadline = time.monotonic() + UI_FIRST_CONNECT_TIMEOUT
        self.never_connected = False

        self._overlay = overlay.OverlayWindow(url.rstrip("/") + "/overlay")
        self._hotkey: overlay.Hotkey | None = None
        self._overlay_retry_at = 0.0
        #: Match the overlay was hidden for with the hotkey; it comes back
        #: on its own for the next one.
        self._overlay_dismissed: str | None = None
        self._overlay_showing = False
        self._overlay_edit_since: float | None = None
        #: Set once the window has been dealt with for the running match -
        #: either we minimized it, or the user deliberately brought it back
        #: and it should stay where they put it.
        self._match_window_handled = False
        app.state.overlay_window = self._overlay

    # -- settings ----------------------------------------------------------

    def _minimize_to_tray(self) -> bool:
        return bool(self._cfg.get("tray.minimize_to_tray", False))

    def _close_to_tray(self) -> bool:
        return bool(self._cfg.get("tray.close_to_tray", False))

    def _minimize_during_match(self) -> bool:
        return bool(self._cfg.get("app.minimize_during_match", True))

    def _overlay_enabled(self) -> bool:
        return bool(self._cfg.get("overlay.enabled", False))

    def _wants_tray(self) -> bool:
        return self._minimize_to_tray() or self._close_to_tray()

    def _toggle(self, path: str) -> None:
        new_value = not bool(self._cfg.get(path, False))
        self._cfg.set(path, new_value)
        try:
            save_user_settings({path: new_value})
        except (OSError, KeyError) as exc:
            log.warning("Could not save %s: %s", path, exc)
        log.info("%s -> %s", path, new_value)

        if path == "app.start_with_windows":
            actual = autostart.sync(new_value)
            if actual != new_value:
                self._cfg.set(path, actual)
        if self._on_settings_changed:
            self._on_settings_changed()
        self._sync_tray()

    # -- tray --------------------------------------------------------------

    def _menu(self) -> list[MenuItem]:
        return [
            MenuItem("Open SpikeSight", self.show_window, default=True),
            MenuItem.separator(),
            MenuItem("Minimize to tray", lambda: self._toggle("tray.minimize_to_tray"),
                     checked=self._minimize_to_tray()),
            MenuItem("Close to tray", lambda: self._toggle("tray.close_to_tray"),
                     checked=self._close_to_tray()),
            MenuItem("Pre-match overlay", lambda: self._toggle("overlay.enabled"),
                     checked=self._overlay_enabled()),
            MenuItem("Start with Windows",
                     lambda: self._toggle("app.start_with_windows"),
                     checked=bool(self._cfg.get("app.start_with_windows", False))),
            MenuItem.separator(),
            MenuItem("Quit SpikeSight", self.request_quit),
        ]

    def _sync_tray(self) -> None:
        """Create or remove the tray icon to match the current settings."""
        if self._wants_tray() and self._tray is None:
            tray = TrayIcon(
                icon_path=paths.ICON_FILE,
                tooltip="SpikeSight",
                menu=self._menu,
                on_activate=self.show_window,
            )
            if tray.start():
                self._tray = tray
                log.info("Tray icon added.")
            else:
                log.warning("Tray icon unavailable; close will quit as usual.")
        elif not self._wants_tray() and self._tray is not None and not self._hidden:
            self._tray.stop()
            self._tray = None
            log.info("Tray icon removed.")

    # -- window ------------------------------------------------------------

    def open_window(self) -> None:
        self._window = appwindow.open_window(self._url)
        self._hwnd = None
        self._hidden = False
        self._in_tray = False
        self._first_connect_deadline = time.monotonic() + UI_FIRST_CONNECT_TIMEOUT

    def show_window(self) -> None:
        """Bring SpikeSight back, whether it is hidden or gone entirely.

        Called from the tray thread, so it only touches thread-safe things.
        """
        hwnd = self._hwnd or appwindow.find_app_window()
        if appwindow.window_exists(hwnd):
            self._hwnd = hwnd
            appwindow.restore_window(hwnd)
            self._hidden = False
            # Asked for by hand: leave it alone for the rest of this match.
            self._match_window_handled = True
            return
        log.info("Window is gone; opening a new one.")
        self.open_window()

    def request_quit(self) -> None:
        self._quit.set()

    # -- main loop ---------------------------------------------------------

    def run(self) -> None:
        """Block until the app should shut down."""
        # Written once per run so any "my FPS dropped" report arrives with
        # the display setup that decides whether that can even happen.
        perfcheck.log_summary()
        self._sync_tray()

        seen_ui = False
        empty_since: float | None = None
        hub = getattr(self._app.state, "hub", None)

        while not self._quit.is_set():
            if hub is None:  # pragma: no cover - defensive
                time.sleep(POLL_SECONDS)
                continue

            # Switching the tray off while the window is closed would otherwise
            # leave the app running with nothing on screen and no way to reach
            # or quit it.
            if self._in_tray and not self._wants_tray():
                log.info("Tray turned off with no window open; shutting down.")
                return

            # Cheap, and it means a toggle flipped from the UI takes effect
            # without any cross-thread plumbing.
            self._sync_tray()
            self._track_minimize()
            self._sync_overlay()
            self._sync_match_window()

            if hub.count:
                if not seen_ui:
                    log.info("UI connected.")
                seen_ui = True
                empty_since = None
                self._in_tray = False
            elif seen_ui:
                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= UI_GRACE_SECONDS:
                    if self._close_to_tray() and self._tray is not None:
                        log.info("Window closed; staying in the tray.")
                        self._hidden = False
                        self._in_tray = True
                        self._hwnd = None
                        seen_ui = False
                        empty_since = None
                        self._first_connect_deadline = float("inf")
                    else:
                        log.info("Window closed; shutting down.")
                        return
            elif time.monotonic() > self._first_connect_deadline:
                self.never_connected = True
                return

            time.sleep(POLL_SECONDS)

    def _track_minimize(self) -> None:
        """Hide the window when it is minimized, if that is switched on."""
        if not self._minimize_to_tray() or self._tray is None or self._hidden:
            return
        if not appwindow.window_exists(self._hwnd):
            self._hwnd = appwindow.find_app_window()
        if self._hwnd and appwindow.is_minimized(self._hwnd):
            log.info("Minimized; hiding to the tray.")
            appwindow.hide_window(self._hwnd)
            self._hidden = True

    # -- staying out of the way --------------------------------------------

    def _sync_match_window(self) -> None:
        """Minimize while a match is on, and put it back afterwards."""
        state, _ = self._current_match()
        action = match_window_action(
            state, self._minimize_during_match(), self._match_window_handled
        )
        if action is None:
            return

        hwnd = self._hwnd if appwindow.window_exists(self._hwnd) else None
        if hwnd is None:
            hwnd = appwindow.find_app_window(include_hidden=True)
            self._hwnd = hwnd
        if not appwindow.window_exists(hwnd):
            # Nothing on screen to move.
            self._match_window_handled = action == "minimize"
            return

        if action == "minimize":
            if not appwindow.is_minimized(hwnd):
                log.info("Match started; minimizing the window to leave the game alone.")
                perfcheck.log_summary()
                appwindow.minimize_window(hwnd)
            self._match_window_handled = True
        else:
            log.info("Match over; bringing the window back.")
            # SW_SHOWNOACTIVATE also un-hides it if minimize-to-tray put it
            # away on the way down.
            appwindow.unminimize_without_focus(hwnd)
            self._hidden = False
            self._match_window_handled = False

    # -- overlay -----------------------------------------------------------

    def _current_match(self) -> tuple[str, str]:
        poller = getattr(self._app.state, "poller", None)
        snapshot = getattr(poller, "snapshot", None) or {}
        match = snapshot.get("match") or {}
        return str(snapshot.get("state") or ""), str(match.get("id") or "")

    def _end_overlay_edit(self, reason: str) -> None:
        """Leave move mode from this thread, and tell the pages."""
        if not getattr(self._app.state, "overlay_edit", False):
            return
        log.info("Leaving overlay move mode (%s).", reason)
        self._app.state.overlay_edit = False
        loop = getattr(self._app.state, "loop", None)
        hub = getattr(self._app.state, "hub", None)
        if loop is not None and hub is not None:
            asyncio.run_coroutine_threadsafe(
                hub.broadcast({"type": "overlay-edit", "on": False}), loop
            )

    def _toggle_overlay_for_this_match(self) -> None:
        """The hotkey. Hides the panel until the next match, or brings it back."""
        if getattr(self._app.state, "overlay_edit", False):
            self._end_overlay_edit("hotkey")
            return
        state, match_id = self._current_match()
        if state != "PREGAME":
            return
        if self._overlay_dismissed == match_id:
            self._overlay_dismissed = None
            log.info("Overlay shown again for this match.")
        else:
            self._overlay_dismissed = match_id
            log.info("Overlay hidden for this match.")

    def _sync_overlay(self) -> None:
        if not self._overlay_enabled():
            self._end_overlay_edit("overlay switched off")
            if self._overlay.running:
                log.info("Overlay switched off.")
                self._overlay.stop()
            if self._hotkey is not None:
                self._hotkey.stop()
                self._hotkey = None
            self._overlay_showing = False
            return

        if self._hotkey is None:
            self._hotkey = overlay.Hotkey(self._toggle_overlay_for_this_match)
            self._hotkey.start()

        if not self._overlay.running:
            now = time.monotonic()
            if now < self._overlay_retry_at:
                return
            self._overlay_retry_at = now + OVERLAY_RETRY_SECONDS
            self._overlay.ensure_started()
            if not self._overlay.running:
                return

        self._overlay.set_metrics(getattr(self._app.state, "overlay_metrics", None))
        state, match_id = self._current_match()
        if state != "PREGAME":
            self._overlay_dismissed = None

        editing = bool(getattr(self._app.state, "overlay_edit", False))
        if editing:
            now = time.monotonic()
            if self._overlay_edit_since is None:
                self._overlay_edit_since = now
            # A panel that takes the mouse has no business over a live match.
            if state == "INGAME":
                self._end_overlay_edit("match started")
                editing = False
            elif now - self._overlay_edit_since > OVERLAY_EDIT_TIMEOUT:
                self._end_overlay_edit("timed out")
                editing = False
        if not editing:
            self._overlay_edit_since = None

        show = editing or (state == "PREGAME" and match_id != self._overlay_dismissed)
        if show != self._overlay_showing:
            log.info("Overlay %s.", "shown" if show else "hidden")
            self._overlay_showing = show
        x, y = self._cfg.get("overlay.x"), self._cfg.get("overlay.y")
        position = (float(x), float(y)) if x is not None and y is not None else None
        self._overlay.update(
            show, str(self._cfg.get("overlay.side", "left")), position, editing
        )

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        # First, so a click-through always-on-top window can never outlive us.
        self._overlay.stop()
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        # A hidden window would otherwise be left with no way to get it back.
        if self._hidden and appwindow.window_exists(self._hwnd):
            appwindow.restore_window(self._hwnd)
        self._window.close()
