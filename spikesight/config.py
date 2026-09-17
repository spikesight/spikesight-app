"""Configuration loading.

A TOML file is written to %LOCALAPPDATA%\SpikeSight\config.toml on first run so
the user can tune polling, rate limits and detection thresholds without editing
code. Unknown keys are ignored; missing keys fall back to DEFAULTS.
"""

from __future__ import annotations

import copy
import json
import logging
import tomllib
from typing import Any

from . import paths

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8787,
        "open_browser": True,
        # Launch SpikeSight when you sign in to Windows. Off unless asked for.
        "start_with_windows": False,
        # Minimize the window once agent select ends. Off by default: the
        # scoreboard is at its most useful in a live match, where the enemy
        # team is finally visible. It is here for people who would rather
        # have the frames - a visible window stops a Windowed Fullscreen game
        # from drawing straight to the screen for as long as it is up.
        "minimize_during_match": False,
    },
    "ui": {
        # "dark", "light", or "system" to follow Windows.
        "theme": "dark",
    },
    "overlay": {
        # A small always-on-top panel over the game during agent select.
        # Off unless asked for.
        "enabled": False,
        # Which edge of the screen it sits on: "left" or "right". Right by
        # default: agent select puts your team down the left, and the agent
        # grid and Lock In button through the middle, so the right is the one
        # side with nothing on it.
        "side": "right",
        # A spot it was dragged to, as fractions of the screen. Unset means
        # "dock to the side above".
        "x": None,
        "y": None,
    },
    "tray": {
        # Minimizing the window hides it to the notification area instead of
        # leaving a taskbar button.
        "minimize_to_tray": False,
        # Closing the window leaves SpikeSight running in the notification area
        # rather than quitting.
        "close_to_tray": False,
    },
    "riot": {
        # Blank = auto-detect from the running client and the game log.
        "region": "",
        "shard": "",
        "client_version": "",
        # Optional key from developer.riotgames.com. Only used for the official
        # VAL-CONTENT (act names) and VAL-STATUS (server status) endpoints.
        "official_api_key": "",
    },
    "polling": {
        "presence_interval": 2.0,
        "pregame_interval": 5.0,
        "idle_interval": 5.0,
    },
    "network": {
        "requests_per_second": 4.0,
        "burst": 8,
        "max_concurrency": 3,
        "timeout": 12.0,
    },
    "stats": {
        "enable_deep_stats": True,
        "match_history_depth": 5,
        "max_match_details_per_scan": 40,
        "queue": "auto",
    },
    "history": {
        "default_count": 15,
        "cache_seconds": 120.0,
    },
    "smurf": {
        "enabled": True,
        "level_threshold": 60,
        "kd_threshold": 1.45,
        "winrate_threshold": 0.65,
        "min_games_for_winrate": 8,
        "score_threshold": 45,
    },
    "winprob": {
        "elo_scale": 120.0,
    },
}

CONFIG_TEMPLATE = """\
# SpikeSight configuration. Delete this file to regenerate it with defaults.

[app]
host = "127.0.0.1"
port = 8787
open_browser = true

[riot]
# Leave blank to auto-detect from the running Riot Client and ShooterGame.log.
region = ""
shard = ""
client_version = ""
# Optional. From developer.riotgames.com. Used only for official VAL-CONTENT
# (act names) and VAL-STATUS (server status). SpikeSight works fine without it.
official_api_key = ""

[polling]
# Local presence polling is free (127.0.0.1) - this is the heartbeat.
presence_interval = 2.0
# How often to refresh the agent-select roster while it is filling in.
pregame_interval = 5.0
idle_interval = 5.0

[network]
# Deliberately conservative. These are Riot's own servers; be a good citizen.
requests_per_second = 4.0
burst = 8
max_concurrency = 3
timeout = 12.0

[stats]
# Deep stats fetch each player's recent match details to derive K/D and win
# rate. Set to false for a much lighter footprint (ranks and peaks only).
enable_deep_stats = true
match_history_depth = 5
max_match_details_per_scan = 40
# "auto" follows whatever queue the lobby is in, so Swiftplay premades and
# Swiftplay form come from Swiftplay history. Set a queue name to pin it.
queue = "auto"

[history]
# Matches shown in the History tab, and how long that view is cached.
default_count = 15
cache_seconds = 120.0

[smurf]
enabled = true
level_threshold = 60
kd_threshold = 1.45
winrate_threshold = 0.65
min_games_for_winrate = 8
score_threshold = 45

[winprob]
# Rating points per logistic unit. Larger = flatter (less confident) curve.
elo_scale = 120.0
"""


class Config:
    """Dot-path accessor over the merged config dictionary."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self._data.get(name, {})
        return value if isinstance(value, dict) else {}

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def set(self, path: str, value: Any) -> None:
        """In-memory override (used by the UI's runtime toggles)."""
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


#: Settings the UI is allowed to change. Anything not listed here has to be
#: edited in config.toml by hand, which keeps the API from writing arbitrary
#: keys into the app's configuration.
def _choice(*options: str):
    """A validator for a setting that takes one of a few fixed strings."""

    def validate(value) -> str:
        text = str(value).strip().lower()
        if text not in options:
            raise ValueError(f"expected one of {', '.join(options)}")
        return text

    validate.options = options
    return validate


def _fraction(value):
    """0-1 screen fraction, or None to clear it."""
    if value is None:
        return None
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("expected a fraction between 0 and 1")
    return number


WRITABLE_SETTINGS = {
    "app.start_with_windows": bool,
    "app.minimize_during_match": bool,
    "tray.minimize_to_tray": bool,
    "tray.close_to_tray": bool,
    "stats.enable_deep_stats": bool,
    "smurf.enabled": bool,
    "ui.theme": _choice("dark", "light", "system"),
    "overlay.enabled": bool,
    "overlay.side": _choice("left", "right"),
    "overlay.x": _fraction,
    "overlay.y": _fraction,
}


def _load_user_settings() -> dict:
    """Toggles saved from the UI, as a nested dict."""
    if not paths.USER_SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(paths.USER_SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable settings file: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}

    nested: dict = {}
    for path, value in data.items():
        kind = WRITABLE_SETTINGS.get(path)
        if kind is None:
            continue
        try:
            value = kind(value)
        except (TypeError, ValueError):
            log.warning("Ignoring invalid value for %s in settings.json", path)
            continue
        section, _, key = str(path).partition(".")
        if key:
            nested.setdefault(section, {})[key] = value
    return nested


def save_user_settings(updates: dict) -> dict:
    """Persist UI toggles, merged over whatever was already saved.

    Written to settings.json rather than back into config.toml: rewriting TOML
    would mean either losing the file's comments or shipping a TOML writer, and
    neither is worth it for a handful of toggles.
    """
    paths.ensure_data_dirs()
    current: dict = {}
    if paths.USER_SETTINGS_FILE.exists():
        try:
            loaded = json.loads(paths.USER_SETTINGS_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, json.JSONDecodeError):
            current = {}

    for path, value in updates.items():
        kind = WRITABLE_SETTINGS.get(path)
        if kind is None:
            raise KeyError(f"{path} is not a UI-writable setting")
        current[path] = kind(value)

    paths.USER_SETTINGS_FILE.write_text(
        json.dumps(current, indent=2, sort_keys=True), encoding="utf-8"
    )
    return current


#: One-line rewrites for settings whose *generated default* changed. Only an
#: exact match of the old generated line is touched, so a value someone chose
#: on purpose that happens to differ is left alone, and comments survive.
_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        'queue = "competitive"',
        'queue = "auto"',
        "recent-match history now follows the queue you are in",
    ),
]


def _migrate_config_file() -> None:
    """Update stale generated defaults in an existing config.toml.

    Rewriting the whole file would cost the user their comments, so this is a
    line-for-line swap and nothing else.
    """
    try:
        original = paths.CONFIG_FILE.read_text(encoding="utf-8-sig")
    except OSError:
        return

    lines = original.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        for old, new, reason in _MIGRATIONS:
            if stripped == old:
                lines[index] = line.replace(old, new)
                log.info("config.toml: %s -> %s (%s)", old, new, reason)
                changed = True

    if changed:
        try:
            paths.CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not update config.toml: %s", exc)


def load_config() -> Config:
    paths.ensure_data_dirs()
    if not paths.CONFIG_FILE.exists():
        paths.CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    else:
        _migrate_config_file()
    try:
        user = tomllib.loads(paths.CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError):
        user = {}
    merged = _deep_merge(DEFAULTS, user)
    # UI toggles win over the file: they are the most recent thing the user did.
    return Config(_deep_merge(merged, _load_user_settings()))
