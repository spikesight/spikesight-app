"""Entry point: ``python -m spikesight``, or the packaged SpikeSight.exe.

The launcher aims to behave like a normal desktop app:

* double-clicking it twice does not start a second copy - it just brings the
  existing window back;
* it opens its own window rather than a tab in whatever browser you had open;
* closing that window quits the app, so there is nothing left running that the
  user cannot see;
* anything that goes wrong is a sentence, not a traceback.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from . import __version__, appwindow, autostart, paths
from .config import load_config
from .desktop import DesktopSession
from .server import create_app

log = logging.getLogger(__name__)

BANNER = r"""
  ___      _ _       ___ _      _   _
 / __|_ __(_) |_____/ __(_)__ _| |_| |_
 \__ \ '_ \ | / / -_)__ \ / _` | ' \  _|
 |___/ .__/_|_\_\___|___/_\__, |_||_\__|
     |_|                  |___/          v{version}

 Read-only VALORANT lobby scout. Local client APIs only.
"""


def _running_instance(url: str) -> bool:
    """True when a SpikeSight is already serving on this address."""
    try:
        response = httpx.get(f"{url}/api/meta", timeout=1.5)
        return response.status_code == 200 and "severities" in response.json()
    except Exception:  # noqa: BLE001 - anything at all means "not ours"
        return False


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.6)
        return probe.connect_ex((host, port)) != 0


def _wait_until_serving(
    server: uvicorn.Server, thread: threading.Thread, url: str
) -> bool:
    """Wait until the server actually answers an HTTP request.

    ``server.started`` only means the socket is bound. Opening the window on
    that alone leaves a gap where the browser can connect first and land on
    "can't reach this page" with no retry, so this makes a real request and
    waits for a real response.
    """
    deadline = time.monotonic() + 25
    bound = False
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        if not bound and getattr(server, "started", False):
            bound = True
            log.info("Server socket is listening; checking it answers...")
        if bound:
            try:
                response = httpx.get(f"{url}/api/meta", timeout=2.0)
                if response.status_code == 200:
                    log.info("Server is answering on %s", url)
                    return True
            except Exception as exc:  # noqa: BLE001 - not ready yet, or blocked
                log.debug("Not answering yet: %s", exc)
        time.sleep(0.15)
    return False


#: How long the UI can be absent before we decide it is gone for good. A page
#: reload drops the socket for a few hundred milliseconds, so this needs slack.
UI_GRACE_SECONDS = 8.0
#: If the browser never connects at all, do not sit there invisibly forever.
UI_FIRST_CONNECT_TIMEOUT = 90.0


def _wait_for_ui_to_close(app, thread, url: str, interactive: bool) -> None:
    """Run until the UI goes away.

    Deliberately *not* keyed on the browser process. Chromium opens a window as
    a tree of processes, and hands the window to an existing instance when one
    is already using the profile - in which case the process we spawned exits
    immediately even though the window is alive and still loading. Tearing the
    server down on that produced ERR_CONNECTION_REFUSED in the very window we
    had just opened, intermittently, depending on whether a previous browser
    process had finished exiting.

    The page's own WebSocket is the honest signal: it exists for exactly as
    long as a SpikeSight window is open, however the browser arranges itself.
    """
    hub = getattr(app.state, "hub", None)
    if hub is None:  # pragma: no cover - defensive
        while thread.is_alive():
            time.sleep(0.4)
        return

    started = time.monotonic()
    seen_ui = False
    empty_since = None

    while thread.is_alive():
        if hub.count:
            if not seen_ui:
                log.info("UI connected.")
            seen_ui = True
            empty_since = None
        elif seen_ui:
            if empty_since is None:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since >= UI_GRACE_SECONDS:
                log.info("UI closed; shutting down.")
                return
        elif time.monotonic() - started > UI_FIRST_CONNECT_TIMEOUT:
            log.warning("The window never connected to %s", url)
            _fatal(
                "SpikeSight is running, but its window never connected.\n\n"
                f"Try opening this address in your browser:\n{url}\n\n"
                f"Log:\n{paths.DATA_DIR / 'spikesight.log'}",
                interactive,
            )
            return
        time.sleep(0.4)


def _setup_logging(verbose: bool) -> Path | None:
    """Send logs somewhere they can actually be read.

    A packaged windowed build has no console at all - PyInstaller sets both
    ``sys.stdout`` and ``sys.stderr`` to None - so logging goes to a file
    beside the config instead. Running from a terminal it goes to the terminal,
    as usual.
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"

    log_path: Path | None = None
    if sys.stderr is None:
        paths.ensure_data_dirs()
        log_path = paths.DATA_DIR / "spikesight.log"
        # One session per file: a rolling log nobody reads is just clutter, and
        # "send me the log" should mean the run that went wrong.
        handler: logging.Handler = logging.FileHandler(
            log_path, mode="w", encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return log_path


def _message_box(message: str, title: str = "SpikeSight") -> None:
    """Show a native dialog, for when there is no console to print to."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - not Windows, or no user32
        pass


def _fatal(message: str, interactive: bool) -> int:
    """Report a startup failure in whichever way the user can actually see.

    Launched from a terminal there is a console to print to; double-clicked as
    a packaged .exe there is not, so the same text goes into a dialog box
    rather than disappearing with the window.
    """
    print(f"\n  {message}\n")
    if interactive:
        try:
            input("  Press Enter to close this window. ")
        except (EOFError, KeyboardInterrupt):
            pass
    else:
        _message_box(message)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spikesight",
        description="Read-only VALORANT lobby scout (Windows).",
    )
    parser.add_argument("--host", default=None, help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (default 8787)")
    parser.add_argument(
        "--no-window",
        "--no-browser",
        dest="no_window",
        action="store_true",
        help="Do not open a window; just serve. Closing it will not quit SpikeSight.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Serve a synthetic lobby. Makes no requests to Riot at all.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    parser.add_argument("--version", action="version", version=f"SpikeSight {__version__}")
    args = parser.parse_args(argv)

    log_path = _setup_logging(args.verbose)

    # A double-clicked .exe has no console to read a traceback from, so errors
    # need to hold the window open long enough to be read.
    interactive = sys.stdout is not None and sys.stdout.isatty()

    cfg = load_config()
    host = args.host or str(cfg.get("app.host", "127.0.0.1"))
    port = args.port or int(cfg.get("app.port", 8787))
    url = f"http://{host}:{port}"

    print(BANNER.format(version=__version__))
    print(f"  Config : {paths.CONFIG_FILE}")
    print(f"  Data   : {paths.DATA_DIR}")
    print(f"  UI     : {url}")
    if log_path is not None:
        print(f"  Log    : {log_path}")
    print()

    # --- already running? -------------------------------------------------
    if not _port_is_free(host, port):
        if _running_instance(url):
            print("  SpikeSight is already running - bringing its window back.\n")
            if not args.no_window:
                appwindow.open_window(url)
            return 0
        return _fatal(
            f"Port {port} is already being used by another program.\n"
            f"  Close it, or set a different port in:\n  {paths.CONFIG_FILE}",
            interactive,
        )

    if host not in ("127.0.0.1", "localhost"):
        print(
            "  WARNING: binding to a non-loopback address exposes your lobby\n"
            "           data to your network. Only do this on purpose.\n"
        )
    if args.demo:
        print("  DEMO MODE: synthetic lobby, no Riot requests will be made.\n")

    # --- serve ------------------------------------------------------------
    app = create_app(cfg, demo=args.demo)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        # We configure logging ourselves above. uvicorn's default config builds
        # a formatter that calls sys.stdout.isatty(), which is fatal in a
        # windowed build where sys.stdout is None.
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="spikesight-server", daemon=True)
    thread.start()

    if not _wait_until_serving(server, thread, url):
        return _fatal(
            "SpikeSight started but could not reach its own local server at\n"
            f"{url}\n\n"
            "This is almost always a security product, VPN or proxy blocking\n"
            "connections to 127.0.0.1. Try allowing SpikeSight.exe through it.\n\n"
            f"Details were written to:\n{paths.DATA_DIR / 'spikesight.log'}",
            interactive,
        )

    # Keep the registry entry honest: if the app has been moved or rebuilt, the
    # recorded startup command would otherwise point at the old location.
    autostart.remove_legacy()
    autostart.sync(bool(cfg.get("app.start_with_windows", False)))

    if args.no_window:
        print(f"  Open {url} in your browser. Press Ctrl+C here to quit.\n")
        try:
            while thread.is_alive():
                time.sleep(0.4)
        except KeyboardInterrupt:
            print("\n  Shutting down...")
        finally:
            server.should_exit = True
            thread.join(timeout=15)
        return 0

    session = DesktopSession(cfg, url, app)
    session.open_window()
    if cfg.get("tray.close_to_tray", False):
        print("  Closing the window leaves SpikeSight in the notification area.\n")
    else:
        print("  Close the SpikeSight window to quit.\n")

    try:
        session.run()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        session.close()
        server.should_exit = True
        thread.join(timeout=15)

    if session.never_connected:
        return _fatal(
            "SpikeSight is running, but its window never connected.\n\n"
            f"Try opening this address in your browser:\n{url}\n\n"
            f"Log:\n{paths.DATA_DIR / 'spikesight.log'}",
            interactive,
        )
    return 0


def run() -> int:
    """Wrapper so a crash never vanishes behind a closing window."""
    try:
        return main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - last line of defense
        logging.getLogger(__name__).exception("SpikeSight stopped unexpectedly")
        interactive = sys.stdout is not None and sys.stdout.isatty()
        return _fatal(
            f"SpikeSight stopped unexpectedly:\n\n{type(exc).__name__}: {exc}",
            interactive,
        )


if __name__ == "__main__":
    sys.exit(run())
