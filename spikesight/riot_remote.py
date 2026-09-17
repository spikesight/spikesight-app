"""Read-only client for Riot's pd/glz/shared endpoints.

Every request here is authenticated with the tokens the *local* Riot Client
minted for the signed-in user - the same credentials the game itself uses. No
password ever touches this process, and there is no separate login flow.

Only two verbs are used:

* ``GET``  for everything.
* ``PUT``  for ``/name-service/v2/players``, which is a batched *lookup*: the
  puuid list is the query, and the call has no side effects. It is the only
  way the client exposes name resolution.

Nothing in this module can create, join, modify or leave anything.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .cache import TTLCache
from .errors import AuthExpired, RemoteError
from .gamedata import CLIENT_PLATFORM, Endpoints
from .local_api import Credentials, LocalClient
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

# Cache lifetimes, in seconds.
TTL_MMR = 90.0
TTL_PARTY = 20.0
TTL_NAMES = 6 * 3600.0
TTL_HISTORY = 300.0
TTL_CONTENT = 12 * 3600.0
TTL_COMP_UPDATES = 300.0


class RemoteClient:
    def __init__(
        self,
        local: LocalClient,
        endpoints: Endpoints,
        limiter: RateLimiter,
        timeout: float = 12.0,
    ) -> None:
        self._local = local
        self.endpoints = endpoints
        self._limiter = limiter
        self._credentials: Credentials | None = None
        self._auth_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        self.cache = TTLCache()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- authentication -----------------------------------------------------

    async def credentials(self, force: bool = False) -> Credentials:
        async with self._auth_lock:
            if force or self._credentials is None:
                self._credentials = await self._local.credentials()
            return self._credentials

    async def _headers(self) -> dict[str, str]:
        creds = await self.credentials()
        return {
            "Authorization": f"Bearer {creds.access_token}",
            "X-Riot-Entitlements-JWT": creds.entitlement,
            "X-Riot-ClientPlatform": CLIENT_PLATFORM,
            "X-Riot-ClientVersion": self.endpoints.client_version,
            "Accept": "application/json",
        }

    # -- transport ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        allow_404: bool = False,
        _retried: bool = False,
    ) -> Any:
        headers = await self._headers()
        async with self._limiter.slot():
            try:
                response = await self._client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                raise RemoteError(f"{method} {url} failed: {exc}") from exc

        if response.status_code == 404 and allow_404:
            return None

        if response.status_code in (400, 401, 403) and not _retried:
            # Tokens rotate roughly hourly and the client re-mints them on its
            # own. Re-read once, then give up rather than hammering.
            log.info(
                "Auth rejected (%s) on %s; re-reading local credentials.",
                response.status_code,
                url,
            )
            await self.credentials(force=True)
            return await self._request(
                method,
                url,
                json_body=json_body,
                params=params,
                allow_404=allow_404,
                _retried=True,
            )

        if response.status_code == 429:
            raise RemoteError("Rate limited by Riot; backing off.", 429)
        if response.status_code in (401, 403):
            raise AuthExpired(f"{method} {url} -> {response.status_code}")
        if response.status_code >= 400:
            raise RemoteError(
                f"{method} {url} -> {response.status_code} {response.text[:200]}",
                response.status_code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RemoteError(f"{method} {url} returned non-JSON") from exc

    async def _get(self, url: str, **kwargs) -> Any:
        return await self._request("GET", url, **kwargs)

    # -- live match state ---------------------------------------------------

    async def pregame_player(self, puuid: str) -> dict | None:
        return await self._get(
            f"{self.endpoints.glz}/pregame/v1/players/{puuid}", allow_404=True
        )

    async def pregame_match(self, match_id: str) -> dict | None:
        return await self._get(
            f"{self.endpoints.glz}/pregame/v1/matches/{match_id}", allow_404=True
        )

    async def coregame_player(self, puuid: str) -> dict | None:
        return await self._get(
            f"{self.endpoints.glz}/core-game/v1/players/{puuid}", allow_404=True
        )

    async def coregame_match(self, match_id: str) -> dict | None:
        return await self._get(
            f"{self.endpoints.glz}/core-game/v1/matches/{match_id}", allow_404=True
        )

    # -- per-player lookups -------------------------------------------------

    async def party_of(self, puuid: str) -> str | None:
        """CurrentPartyID for a player, or None if the server will not say.

        Riot does not guarantee this for players outside your own party, so a
        missing answer is normal and the caller falls back to a heuristic.
        """

        async def fetch():
            data = await self._get(
                f"{self.endpoints.glz}/parties/v1/players/{puuid}", allow_404=True
            )
            if isinstance(data, dict):
                return {"party": str(data.get("CurrentPartyID") or "")}
            return {"party": ""}

        try:
            result = await self.cache.get_or_fetch(f"party:{puuid}", TTL_PARTY, fetch)
        except (RemoteError, AuthExpired):
            return None
        party = (result or {}).get("party") or ""
        return party or None

    async def mmr(self, puuid: str) -> dict | None:
        async def fetch():
            return await self._get(
                f"{self.endpoints.pd}/mmr/v1/players/{puuid}", allow_404=True
            )

        return await self.cache.get_or_fetch(f"mmr:{puuid}", TTL_MMR, fetch)

    async def competitive_updates(self, puuid: str, count: int = 15) -> dict | None:
        async def fetch():
            return await self._get(
                f"{self.endpoints.pd}/mmr/v1/players/{puuid}/competitiveupdates",
                params={"startIndex": 0, "endIndex": count, "queue": "competitive"},
                allow_404=True,
            )

        return await self.cache.get_or_fetch(
            f"compupd:{puuid}:{count}", TTL_COMP_UPDATES, fetch
        )

    #: Riot rejects a request for more than this many matches at once.
    HISTORY_PAGE = 25

    async def match_history(
        self,
        puuid: str,
        count: int = 10,
        queue: str = "competitive",
        start: int = 0,
    ) -> dict | None:
        count = min(count, self.HISTORY_PAGE)

        async def fetch():
            params: dict[str, Any] = {
                "startIndex": start,
                "endIndex": start + count,
            }
            if queue:
                params["queue"] = queue
            return await self._get(
                f"{self.endpoints.pd}/match-history/v1/history/{puuid}",
                params=params,
                allow_404=True,
            )

        return await self.cache.get_or_fetch(
            f"history:{puuid}:{start}:{count}:{queue}", TTL_HISTORY, fetch
        )

    async def match_history_deep(
        self, puuid: str, count: int, queue: str = ""
    ) -> list[str]:
        """Match ids going back ``count`` games, a page at a time."""
        ids: list[str] = []
        while len(ids) < count:
            page = await self.match_history(
                puuid, count=min(self.HISTORY_PAGE, count - len(ids)),
                queue=queue, start=len(ids),
            )
            entries = (page or {}).get("History") or []
            found = [
                str(entry["MatchID"])
                for entry in entries
                if isinstance(entry, dict) and entry.get("MatchID")
            ]
            ids.extend(found)
            # A short page means that is all the history there is.
            if len(found) < self.HISTORY_PAGE:
                break
        return ids[:count]

    async def match_details(self, match_id: str) -> dict | None:
        return await self._get(
            f"{self.endpoints.pd}/match-details/v1/matches/{match_id}", allow_404=True
        )

    async def names(self, puuids: list[str]) -> dict[str, dict]:
        """Batch name lookup.

        Callers MUST filter out incognito players before calling this - see
        ``privacy.py``. SpikeSight does not resolve names it is not allowed to
        display.
        """
        wanted = [p for p in dict.fromkeys(puuids) if p]
        if not wanted:
            return {}

        resolved: dict[str, dict] = {}
        missing: list[str] = []
        for puuid in wanted:
            cached = self.cache.peek(f"name:{puuid}")
            if cached is not None:
                resolved[puuid] = cached
            else:
                missing.append(puuid)

        if missing:
            data = await self._request(
                "PUT", f"{self.endpoints.pd}/name-service/v2/players", json_body=missing
            )
            for entry in data or []:
                if not isinstance(entry, dict):
                    continue
                puuid = str(entry.get("Subject", ""))
                record = {
                    "game_name": str(entry.get("GameName", "")),
                    "tag_line": str(entry.get("TagLine", "")),
                }
                if puuid:
                    self.cache.put(f"name:{puuid}", record, TTL_NAMES)
                    resolved[puuid] = record
        return resolved

    # -- static game content ------------------------------------------------

    async def content(self) -> dict | None:
        async def fetch():
            return await self._get(
                f"{self.endpoints.shared}/content-service/v3/content",
                params={"locale": "en-US"},
                allow_404=True,
            )

        return await self.cache.get_or_fetch("content", TTL_CONTENT, fetch)
