"""The polling loop that keeps the scoreboard in sync with the game.

Design goal: touch Riot's servers as little as possible.

The heartbeat is the *local* presence endpoint on 127.0.0.1, which is free and
tells us whether the client is in menus, agent select, or a live match. Only
when that state actually changes does the loop reach out to glz/pd to fetch a
roster. A finished roster is never re-fetched, because it never changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from . import content as content_module
from . import agentpool, career, gamedata, parties, scoreboard
from .errors import ClientNotRunning, RemoteError, SpikeSightError
from .local_api import LocalClient
from .notes import NotesStore
from .ratelimit import RateLimiter
from .riot_remote import RemoteClient
from .stats import StatsEngine

log = logging.getLogger(__name__)

Emit = Callable[[dict], Awaitable[None]]

#: How often to re-check glz when presence is unavailable.
BLIND_DETECT_INTERVAL = 12.0
#: Your own matches read for the overlay's top-agents panel. Deep enough that
#: a single map has something to say: spread over the map pool, 20 games is
#: two per map. Details are cached on disk forever, so this is paid once.
AGENT_POOL_DEPTH = 50
AGENT_POOL_TTL = 600.0


class Poller:
    def __init__(self, cfg, emit: Emit) -> None:
        self._cfg = cfg
        self._emit = emit

        self.local = LocalClient(timeout=float(cfg.get("network.timeout", 12.0)))
        self.notes = NotesStore()
        self.limiter = RateLimiter(
            rate_per_second=float(cfg.get("network.requests_per_second", 4.0)),
            burst=int(cfg.get("network.burst", 8)),
            max_concurrency=int(cfg.get("network.max_concurrency", 3)),
        )

        self.remote: RemoteClient | None = None
        self.builder: scoreboard.ScoreboardBuilder | None = None
        self.content = content_module.GameContent()
        self.endpoints: gamedata.Endpoints | None = None
        self.self_puuid: str = ""

        self.snapshot: dict = _idle_snapshot("STARTING", "Connecting to the Riot Client...")
        self.connection: dict = {"connected": False, "message": "Starting up"}

        self._state = "UNKNOWN"
        self._match_id: str | None = None
        self._last_pregame_scan = 0.0
        self._last_lobby: dict | None = None
        # An in-progress match, held until it ends. Agent select is never held:
        # a lobby you dodge must not enter the encounter log.
        self._pending_encounters: dict | None = None
        self._stats_engine: StatsEngine | None = None
        self._history: tuple[float, dict] | None = None
        self._history_lock = asyncio.Lock()
        self._agent_pool: tuple[tuple[str, str], float, dict] | None = None
        self._last_blind_detect = 0.0
        self._enrich_task: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="spikesight-poller")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        # Shutting down while a match is held: bank it rather than lose it.
        # By this point the match has started, so it cannot have been a dodge.
        try:
            await self._finish_match()
        except Exception:  # noqa: BLE001
            pass
        for task in (self._enrich_task, self._task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self.remote:
            await self.remote.aclose()
        await self.local.aclose()
        self.notes.close()

    def request_refresh(self) -> None:
        """Force the next tick to re-scan, even if nothing changed."""
        self._match_id = None
        self._state = "UNKNOWN"
        self._wake.set()

    # -- connection ---------------------------------------------------------

    async def _connect(self) -> None:
        creds = await self.local.credentials()
        self.self_puuid = creds.puuid
        endpoints = await gamedata.resolve_endpoints(self.local, self._cfg)

        if self.remote is None or self.endpoints != endpoints:
            if self.remote is not None:
                await self.remote.aclose()
            self.endpoints = endpoints
            self.remote = RemoteClient(
                self.local,
                endpoints,
                self.limiter,
                timeout=float(self._cfg.get("network.timeout", 12.0)),
            )
            self.content = await content_module.load_content(self.remote, self._cfg)
            self._stats_engine = StatsEngine(self.remote, self._cfg)
            self.builder = scoreboard.ScoreboardBuilder(
                self.remote,
                self.content,
                self.notes,
                self._stats_engine,
                self._cfg,
            )
            self._history = None
            log.info(
                "Connected: region=%s shard=%s version=%s content=%s",
                endpoints.region, endpoints.shard, endpoints.client_version,
                self.content.source,
            )

        self.connection = {
            "connected": True,
            "message": "Connected to the Riot Client",
            "region": endpoints.region,
            "shard": endpoints.shard,
            "clientVersion": endpoints.client_version,
            "contentSource": self.content.source,
            "currentAct": (
                self.content.current_act().to_dict() if self.content.current_act() else None
            ),
        }

    # -- main loop ----------------------------------------------------------

    async def _run(self) -> None:
        idle = float(self._cfg.get("polling.idle_interval", 5.0))
        presence_interval = float(self._cfg.get("polling.presence_interval", 2.0))

        while not self._stopping:
            delay = presence_interval
            try:
                await self._connect()
                delay = await self._tick()
            except ClientNotRunning as exc:
                self.connection = {"connected": False, "message": str(exc), "hint": exc.hint}
                await self._publish(_idle_snapshot("DISCONNECTED", str(exc)))
                delay = idle
            except (RemoteError, SpikeSightError) as exc:
                log.warning("Poll failed: %s", exc)
                self.connection = {"connected": False, "message": str(exc)}
                delay = idle
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("Unexpected polling error")
                delay = idle

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> float:
        assert self.builder is not None
        presence_interval = float(self._cfg.get("polling.presence_interval", 2.0))
        pregame_interval = float(self._cfg.get("polling.pregame_interval", 5.0))

        presence_state = await self._presence_state()
        now = time.monotonic()

        if presence_state == "MENUS":
            if self._state != scoreboard.STATE_MENUS:
                await self._finish_match()
                self._state = scoreboard.STATE_MENUS
                self._match_id = None
                await self._publish(self._menus_snapshot())
            return presence_interval

        needs_detect = False
        if presence_state in (scoreboard.STATE_PREGAME, scoreboard.STATE_INGAME):
            needs_detect = presence_state != self._state or self._match_id is None
        elif presence_state is None:
            # No presence available: fall back to a slow direct probe.
            needs_detect = now - self._last_blind_detect >= BLIND_DETECT_INTERVAL
            if needs_detect:
                self._last_blind_detect = now

        if not needs_detect and self._state == scoreboard.STATE_PREGAME:
            # Agent select keeps changing: refresh the roster while it runs.
            needs_detect = now - self._last_pregame_scan >= pregame_interval

        if not needs_detect:
            return presence_interval

        state, match_id = await self.builder.detect_match(self.self_puuid)

        if state == scoreboard.STATE_MENUS:
            if self._state != scoreboard.STATE_MENUS:
                await self._finish_match()
                self._state = scoreboard.STATE_MENUS
                self._match_id = None
                await self._publish(self._menus_snapshot())
            return presence_interval

        fresh_match = match_id != self._match_id or state != self._state
        if fresh_match and match_id != self._match_id:
            # Moved on to a different match: bank the previous one first.
            await self._finish_match()
        self._state = state
        self._match_id = match_id
        if state == scoreboard.STATE_PREGAME:
            self._last_pregame_scan = now

        await self._scan(state, match_id, fresh_match)
        return presence_interval

    async def _presence_state(self) -> str | None:
        try:
            presence = await self.local.self_presence(self.self_puuid)
        except (ClientNotRunning, SpikeSightError):
            return None
        if presence is None:
            return None
        state = presence.session_loop_state
        return state or None

    async def _scan(self, state: str, match_id: str | None, fresh: bool) -> None:
        assert self.builder is not None and self.remote is not None
        if not match_id:
            return

        if state == scoreboard.STATE_PREGAME:
            payload = await self.remote.pregame_match(match_id)
        else:
            payload = await self.remote.coregame_match(match_id)

        if not isinstance(payload, dict):
            return

        party_hints, presence_names = await self._presence_snapshot()
        snapshot = await self.builder.build(
            state=state,
            match_payload=payload,
            self_puuid=self.self_puuid,
            deep=False,
            party_hints=party_hints,
            presence_names=presence_names,
        )
        await self._publish(snapshot)

        if fresh and self._cfg.get("stats.enable_deep_stats", True):
            if self._enrich_task and not self._enrich_task.done():
                self._enrich_task.cancel()
            self._enrich_task = asyncio.create_task(
                self._enrich(state, match_id, payload, party_hints, presence_names),
                name="spikesight-enrich",
            )

    async def _finish_match(self) -> None:
        """A live match ended - now, and only now, log who was in it.

        This is what keeps dodges out of the encounter history: agent-select
        rosters are never held here, so a lobby you back out of leaves no
        trace.
        """
        pending, self._pending_encounters = self._pending_encounters, None
        if not pending or self.builder is None:
            return
        try:
            await self.builder.record_encounters(pending)
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not break the loop
            log.warning("Could not record encounters: %s", exc)

    def _menus_snapshot(self) -> dict:
        """Back in menus - keep the last lobby on screen.

        The most useful moment to flag a thrower is right after the game, so
        the board is deliberately not cleared; it is marked stale instead.
        """
        if not self._last_lobby:
            return _idle_snapshot("MENUS", "In menus - queue up.")
        snapshot = dict(self._last_lobby)
        snapshot["state"] = "MENUS"
        snapshot["stale"] = True
        snapshot["message"] = (
            "Match over - showing the last lobby so you can still flag people."
        )
        return snapshot

    async def _presence_snapshot(self) -> tuple[dict[str, str], dict[str, dict]]:
        """Party ids and riot IDs the local client has already published."""
        try:
            presences = await self.local.presences()
        except (ClientNotRunning, SpikeSightError):
            return {}, {}
        return (
            parties.hints_from_presence(presences),
            parties.names_from_presence(presences),
        )

    async def _enrich(
        self, state: str, match_id: str, payload: dict, party_hints: dict[str, str],
        presence_names: dict[str, dict],
    ) -> None:
        assert self.builder is not None

        async def progress(done: int, total: int) -> None:
            await self._emit(
                {
                    "type": "progress",
                    "matchId": match_id,
                    "done": done,
                    "total": total,
                }
            )

        try:
            snapshot = await self.builder.build(
                state=state,
                match_payload=payload,
                self_puuid=self.self_puuid,
                deep=True,
                party_hints=party_hints,
                presence_names=presence_names,
                progress=progress,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - enrichment is optional
            log.warning("Enrichment failed for %s: %s", match_id[:8], exc)
            return

        if self._match_id == match_id:
            await self._publish(snapshot)

    # -- publishing ---------------------------------------------------------

    async def _publish(self, snapshot: dict) -> None:
        snapshot = dict(snapshot)
        snapshot["connection"] = self.connection
        snapshot["settings"] = {
            "deepStats": bool(self._cfg.get("stats.enable_deep_stats", True)),
            "smurfDetection": bool(self._cfg.get("smurf.enabled", True)),
            # The overlay runs in a separate browser profile and follows the
            # theme through this.
            "theme": str(self._cfg.get("ui.theme", "dark")),
        }
        if snapshot.get("state") in (scoreboard.STATE_PREGAME, scoreboard.STATE_INGAME):
            self._last_lobby = snapshot
        if snapshot.get("state") == scoreboard.STATE_INGAME:
            # Held until the match ends; see _finish_match.
            self._pending_encounters = snapshot
        self.snapshot = snapshot
        await self._emit({"type": "snapshot", "snapshot": snapshot})

    async def history(self, count: int = career.DEFAULT_COUNT, refresh: bool = False) -> dict:
        """Your own recent matches, cached briefly so tab-switching is free."""
        ttl = float(self._cfg.get("history.cache_seconds", 120.0))
        async with self._history_lock:
            if not refresh and self._history is not None:
                age = time.monotonic() - self._history[0]
                if age < ttl and self._history[1].get("_count") == count:
                    return self._history[1]

            await self._connect()
            if self.remote is None or self._stats_engine is None:
                raise SpikeSightError("Not connected to the Riot Client yet.")

            payload = await career.build(
                remote=self.remote,
                content=self.content,
                stats_engine=self._stats_engine,
                puuid=self.self_puuid,
                count=count,
            )
            payload["_count"] = count
            payload["generatedAt"] = time.time()
            self._history = (time.monotonic(), payload)
            return payload

    async def agent_pool(self) -> dict:
        """Your top agents for the map and queue of the lobby you are in.

        Only asked for by the overlay, so nobody pays for it who does not use
        it. Match summaries are cached on disk, so repeat looks are free.
        """
        match = (self.snapshot or {}).get("match") or {}
        map_id = str(match.get("mapId") or "")
        queue = str(match.get("queue") or "").strip().lower()
        base = {"map": match.get("map"), "queue": queue, "ready": False}
        if not map_id or self.remote is None or self._stats_engine is None:
            return base

        key = (queue, map_id)
        cached = self._agent_pool
        if cached and cached[0] == key and time.monotonic() - cached[1] < AGENT_POOL_TTL:
            return cached[2]

        try:
            ids = await self.remote.match_history_deep(
                self.self_puuid, count=AGENT_POOL_DEPTH, queue=queue
            )
        except (RemoteError, SpikeSightError) as exc:
            log.warning("Agent pool history failed: %s", exc)
            return base
        # The rate limiter paces these; failures come back as None and are
        # simply left out.
        summaries = await asyncio.gather(
            *(self._stats_engine.match_summary(match_id) for match_id in ids)
        )

        result = {
            **base,
            **agentpool.aggregate(summaries, self.self_puuid, map_id, self.content),
            "ready": True,
        }
        self._agent_pool = (key, time.monotonic(), result)
        return result

    async def republish(self) -> None:
        await self._publish(self.snapshot)


def _idle_snapshot(state: str, message: str) -> dict:
    return {
        "state": state,
        "phase": "quick",
        "message": message,
        "generatedAt": None,
        "self": "",
        "match": None,
        "winProbability": None,
        "teams": [],
        "alerts": [],
        "stats": {"matchesExamined": 0, "matchesFetched": 0},
    }
