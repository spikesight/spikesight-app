"""Static game content: agents, maps, and the episode/act timeline.

Split by source, because Riot's own endpoints no longer cover all of it:

* **Acts** come from the client's ``content-service`` on the shared shard. It
  returns every season with start/end times, which is exactly what "peak rank,
  and the act it was hit in" needs.
* **Agent and map names** are *not* in ``content-service`` any more - it now
  returns only ``Seasons``, ``Events`` and ``DisabledIDs``. They ship with
  SpikeSight as a static table under ``spikesight/data`` so the app makes zero
  third-party requests at runtime. Refresh it with
  ``tools/refresh_static_content.py``.
* If an official Riot API key is configured, VAL-CONTENT overrides both, which
  keeps agent names current the moment a new agent ships.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from . import paths

log = logging.getLogger(__name__)

_EPISODE_RE = re.compile(r"EPISODE\s*(\d+)", re.IGNORECASE)
_ACT_RE = re.compile(r"ACT\s*([IVX]+|\d+)", re.IGNORECASE)
_VERSIONED_RE = re.compile(r"\bV(\d+)\b", re.IGNORECASE)

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}

_STATIC_DIR = paths.PACKAGE_DIR / "data"
#: Agent portraits, served from our own origin so the page loads nothing remote.
_ICON_DIR = paths.WEB_DIR / "assets" / "agents"
_ICON_URL = "/static/assets/agents"


def _available_icons() -> set[str]:
    try:
        return {path.stem.lower() for path in _ICON_DIR.glob("*.png")}
    except OSError:
        return set()

# Routing values accepted by the official VAL-CONTENT endpoint.
_OFFICIAL_ROUTES = {
    "na": "na", "latam": "latam", "br": "br",
    "eu": "eu", "ap": "ap", "kr": "kr", "pbe": "na",
}

_FAR_PAST = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _norm_id(value: str | None) -> str:
    return (value or "").replace("-", "").lower()


def _roman_to_int(token: str) -> str:
    token = token.upper()
    return str(_ROMAN.get(token, token))


def _parse_time(value: str | None) -> datetime:
    if not value:
        return _FAR_PAST
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _FAR_PAST


def _load_static(name: str) -> dict[str, str]:
    path = _STATIC_DIR / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("Static content file missing or unreadable: %s", path)
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


@dataclass
class Act:
    id: str
    name: str
    episode_name: str = ""
    is_active: bool = False
    start: datetime = _FAR_PAST

    @property
    def short(self) -> str:
        """A compact label such as ``V26A5`` or ``E9A2``."""
        act_match = _ACT_RE.search(self.name)
        act_number = _roman_to_int(act_match.group(1)) if act_match else ""

        episode_match = _EPISODE_RE.search(self.episode_name or self.name)
        if episode_match and act_number:
            return f"E{episode_match.group(1)}A{act_number}"

        version_match = _VERSIONED_RE.search(self.episode_name or self.name)
        if version_match and act_number:
            return f"V{version_match.group(1)}A{act_number}"

        return f"{self.episode_name} {self.name}".strip() or self.name

    @property
    def full(self) -> str:
        if self.episode_name and self.episode_name.lower() not in self.name.lower():
            return f"{self.episode_name} // {self.name}"
        return self.name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "short": self.short,
            "full": self.full,
            "active": self.is_active,
            "start": self.start.isoformat() if self.start != _FAR_PAST else None,
        }


@dataclass
class GameContent:
    agents: dict[str, str] = field(default_factory=dict)        # normalized id -> name
    maps_by_id: dict[str, str] = field(default_factory=dict)    # normalized id -> name
    maps_by_path: dict[str, str] = field(default_factory=dict)  # asset path -> name
    acts: dict[str, Act] = field(default_factory=dict)          # normalized id -> Act
    icons: set = field(default_factory=set)                     # normalized agent ids
    source: str = "none"

    # -- lookups ------------------------------------------------------------

    def agent_name(self, character_id: str | None) -> str | None:
        if not character_id:
            return None
        return self.agents.get(_norm_id(character_id))

    def agent_icon(self, character_id: str | None) -> str | None:
        """Local URL for an agent portrait, or None if we do not have that one."""
        key = _norm_id(character_id)
        if not key or key not in self.icons:
            return None
        return f"{_ICON_URL}/{key}.png"

    def map_name(self, map_id: str | None) -> str | None:
        if not map_id:
            return None
        if map_id in self.maps_by_path:
            return self.maps_by_path[map_id]
        by_id = self.maps_by_id.get(_norm_id(map_id))
        if by_id:
            return by_id
        # Fall back to the codename in the asset path, e.g. ".../Bonsai/Bonsai".
        return map_id.rstrip("/").split("/")[-1] or None

    def act(self, season_id: str | None) -> Act | None:
        if not season_id:
            return None
        return self.acts.get(_norm_id(season_id))

    def _sorted_acts(self) -> list[Act]:
        return sorted(self.acts.values(), key=lambda a: a.start)

    def current_act(self) -> Act | None:
        for act in self.acts.values():
            if act.is_active:
                return act
        # No active flag (stale cache): fall back to the newest act that has
        # already started.
        now = datetime.now(timezone.utc)
        started = [a for a in self._sorted_acts() if a.start <= now]
        return started[-1] if started else None

    def previous_act(self) -> Act | None:
        current = self.current_act()
        if current is None:
            return None
        earlier = [a for a in self._sorted_acts() if a.start < current.start]
        return earlier[-1] if earlier else None


def _base_content() -> GameContent:
    """Agent and map tables that ship with the app."""
    content = GameContent(source="bundled")
    content.agents = _load_static("agents.json")
    content.icons = _available_icons()
    for key, name in _load_static("maps.json").items():
        if key.startswith("uuid:"):
            content.maps_by_id[key[5:]] = name
        else:
            content.maps_by_path[key] = name
    return content


def _apply_seasons(content: GameContent, seasons: list) -> None:
    """Attach acts from a ``content-service`` Seasons array.

    Entries arrive newest-first with each episode immediately followed by its
    own acts, so the episode name is carried forward as the list is walked.
    """
    current_episode = ""
    for entry in seasons or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("Type") or "").lower()
        name = str(entry.get("Name") or "")
        if kind == "episode":
            current_episode = name
            continue
        if kind != "act" or not entry.get("ID"):
            continue
        act = Act(
            id=_norm_id(entry["ID"]),
            name=name,
            episode_name=current_episode,
            is_active=bool(entry.get("IsActive")),
            start=_parse_time(entry.get("StartTime")),
        )
        content.acts[act.id] = act


def _apply_official(content: GameContent, payload: dict) -> None:
    for entry in payload.get("characters") or []:
        if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
            content.agents[_norm_id(entry["id"])] = str(entry["name"])

    for entry in payload.get("maps") or []:
        if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
            content.maps_by_id[_norm_id(entry["id"])] = str(entry["name"])

    episodes = {
        _norm_id(e.get("id")): str(e.get("name") or "")
        for e in payload.get("episodes") or []
        if isinstance(e, dict)
    }
    for entry in payload.get("acts") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        act_id = _norm_id(entry["id"])
        existing = content.acts.get(act_id)
        content.acts[act_id] = Act(
            id=act_id,
            name=str(entry.get("name") or ""),
            episode_name=episodes.get(_norm_id(entry.get("parentId")), ""),
            is_active=bool(entry.get("isActive")),
            start=existing.start if existing else _FAR_PAST,
        )


async def fetch_official_content(api_key: str, region: str, timeout: float) -> dict | None:
    route = _OFFICIAL_ROUTES.get(region, "na")
    url = f"https://{route}.api.riotgames.com/val/content/v1/contents"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url, params={"locale": "en-US"}, headers={"X-Riot-Token": api_key}
        )
    if response.status_code != 200:
        log.warning("Official VAL-CONTENT returned %s", response.status_code)
        return None
    return response.json()


def _load_season_cache() -> list | None:
    if not paths.CONTENT_CACHE.exists():
        return None
    try:
        cached = json.loads(paths.CONTENT_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    seasons = cached.get("seasons")
    return seasons if isinstance(seasons, list) else None


def _save_season_cache(seasons: list) -> None:
    paths.ensure_data_dirs()
    try:
        paths.CONTENT_CACHE.write_text(
            json.dumps({"seasons": seasons}, separators=(",", ":")), encoding="utf-8"
        )
    except OSError:
        pass


async def load_content(remote, cfg) -> GameContent:
    """Build the content tables: bundled base, then live acts, then official."""
    content = _base_content()
    sources = ["bundled"]

    seasons: list | None = None
    try:
        payload = await remote.content()
        if isinstance(payload, dict) and payload.get("Seasons"):
            seasons = payload["Seasons"]
            _save_season_cache(seasons)
            sources.append("content-service")
    except Exception as exc:  # noqa: BLE001 - content is never worth crashing over
        log.warning("content-service fetch failed: %s", exc)

    if seasons is None:
        seasons = _load_season_cache()
        if seasons:
            sources.append("cached-acts")

    if seasons:
        _apply_seasons(content, seasons)
    else:
        log.warning("No act timeline available; peak-rank acts will be blank.")

    api_key = str(cfg.get("riot.official_api_key", "") or "")
    if api_key:
        try:
            payload = await fetch_official_content(
                api_key, remote.endpoints.region, float(cfg.get("network.timeout", 12.0))
            )
            if payload:
                _apply_official(content, payload)
                sources.append("official-api")
        except httpx.HTTPError as exc:
            log.warning("Official content fetch failed: %s", exc)

    content.source = "+".join(sources)
    return content
