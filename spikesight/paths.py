"""Windows filesystem locations SpikeSight reads from and writes to.

Everything under ``RIOT_*`` is *read only*. SpikeSight never writes to, renames,
or deletes anything inside a Riot Games directory.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _env_dir(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        # Fall back to something sane so imports never explode on non-Windows.
        return Path.home() / f".{name.lower()}"
    return Path(value)


LOCALAPPDATA = _env_dir("LOCALAPPDATA")

#: Riot Client lockfile: "name:pid:port:password:protocol".
RIOT_LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"

#: VALORANT game log - used to read the shipping version and the shard hosts.
RIOT_SHOOTER_LOG = LOCALAPPDATA / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"

#: Where SpikeSight keeps its own state (config, notes database, caches).
DATA_DIR = LOCALAPPDATA / "SpikeSight"

#: This app used to be called ValScout. Anyone upgrading has their flags,
#: encounters and settings in the old folder.
LEGACY_DATA_DIR = LOCALAPPDATA / "ValScout"

#: What is worth carrying over. The browser profile directories are left
#: behind on purpose: Chromium rebuilds them, and they are almost the whole
#: folder - close to a gigabyte on a well-used install.
LEGACY_ITEMS = (
    "notes.sqlite3",
    "notes.sqlite3-wal",
    "notes.sqlite3-shm",
    "notes.sqlite3.backup",
    "config.toml",
    "settings.json",
    "content.json",
    "matchcache",
)

CONFIG_FILE = DATA_DIR / "config.toml"
#: Toggles flipped from the UI. Kept apart from config.toml so writing them
#: back can never mangle the hand-edited file or lose its comments.
USER_SETTINGS_FILE = DATA_DIR / "settings.json"
NOTES_DB = DATA_DIR / "notes.sqlite3"
MATCH_CACHE_DIR = DATA_DIR / "matchcache"
CONTENT_CACHE = DATA_DIR / "content.json"

def _install_root() -> Path:
    """Where the bundled files live.

    Running from source that is the repo root. Packaged with PyInstaller the
    data files are unpacked into a temporary directory it points ``sys._MEIPASS``
    at, so both layouts resolve through the same two names below.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _install_root()
PACKAGE_DIR = PROJECT_ROOT / "spikesight"
WEB_DIR = PROJECT_ROOT / "web"
#: Release notes, rendered inside the app rather than left as a loose file.
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"
#: The app icon, reused for the tray. Bundled with the packaged build.
ICON_FILE = PROJECT_ROOT / "packaging" / "spikesight.ico"


def migrate_legacy_data() -> bool:
    """Copy a ValScout install's data over on first run. Returns True if it did.

    Copied rather than moved: the old folder is left exactly as it was, so
    nothing is destroyed if this goes wrong or somebody wants to go back.
    """
    if DATA_DIR.exists() or not LEGACY_DATA_DIR.is_dir():
        return False
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for name in LEGACY_ITEMS:
            source = LEGACY_DATA_DIR / name
            if not source.exists():
                continue
            if source.is_dir():
                shutil.copytree(source, DATA_DIR / name, dirs_exist_ok=True)
            else:
                shutil.copy2(source, DATA_DIR / name)
    except OSError:
        log.exception("Could not bring the old ValScout data across")
        return False
    log.info("Brought your notes and settings over from ValScout (%s).",
             LEGACY_DATA_DIR)
    return True


def ensure_data_dirs() -> None:
    migrate_legacy_data()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
