"""Launcher smoke test.

Everything else in the suite imports modules directly, which means a name that
is used but never imported in ``__main__`` slips through - exactly the bug that
shipped a "NameError: name 'autostart' is not defined" dialog. This starts the
app the way a user does and checks it actually serves.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TestLauncherStartsAndServes(unittest.TestCase):
    def test_app_starts_serves_and_stops(self):
        port = _free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "spikesight", "--no-window",
             "--demo", "--port", str(port)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            url = f"http://127.0.0.1:{port}/api/meta"
            deadline = time.monotonic() + 40
            last_error = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    self.fail(f"launcher exited early:\n{output}")
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        return
                except (urllib.error.URLError, OSError) as exc:
                    last_error = exc
                    time.sleep(0.3)
            self.fail(f"server never answered: {last_error}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    unittest.main()
