"""PyInstaller entry point.

A frozen build needs a plain script to start from rather than ``-m spikesight``,
so this is the whole of it: hand straight over to the normal launcher.
"""

from __future__ import annotations

import multiprocessing
import sys

from spikesight.__main__ import run

if __name__ == "__main__":
    # Harmless when nothing spawns a process, and essential if anything ever
    # does: without it a frozen child would re-run the launcher.
    multiprocessing.freeze_support()
    sys.exit(run())
