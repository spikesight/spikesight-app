"""Recent-form statistics derived from match history.

The expensive part of scouting a lobby is match *details*: ten players with
five recent games each is up to fifty large payloads. Two things keep that
cheap:

* a finished match is immutable, so details are cached on disk forever;
* ten players in one lobby frequently share recent games, so the unique-match
  set is usually far smaller than ``players x depth``.

Only a compact summary is kept on disk - the scoreboard line for each player,
never names.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from .cache import MatchCache
from .errors import RemoteError, SpikeSightError

log = logging.getLogger(__name__)


@dataclass
class MatchLine:
    match_id: str
    started_ms: int
    queue: str
    map_id: str
    won: bool | None
    kills: int
    deaths: int
    assists: int
    rounds: int
    score: int
    character_id: str
    competitive_tier: int

    def to_dict(self) -> dict:
        return {
            "matchId": self.match_id,
            "startedMs": self.started_ms,
            "queue": self.queue,
            "mapId": self.map_id,
            "won": self.won,
            "kills": self.kills,
            "deaths": self.deaths,
            "assists": self.assists,
            "rounds": self.rounds,
            "score": self.score,
            "characterId": self.character_id,
            "tier": self.competitive_tier,
        }


@dataclass
class PlayerStats:
    puuid: str
    matches: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    rounds: int = 0
    score: int = 0
    wins: int = 0
    losses: int = 0
    lines: list[MatchLine] = field(default_factory=list)
    complete: bool = False

    @property
    def kd(self) -> float | None:
        if self.matches == 0:
            return None
        return round(self.kills / max(1, self.deaths), 2)

    @property
    def kda(self) -> float | None:
        if self.matches == 0:
            return None
        return round((self.kills + self.assists) / max(1, self.deaths), 2)

    @property
    def acs(self) -> int | None:
        if self.rounds == 0:
            return None
        return int(round(self.score / self.rounds))

    @property
    def recent_winrate(self) -> float | None:
        decided = self.wins + self.losses
        if decided == 0:
            return None
        return round(self.wins / decided, 3)

    def to_dict(self) -> dict:
        return {
            "matches": self.matches,
            "kd": self.kd,
            "kda": self.kda,
            "acs": self.acs,
            "kills": self.kills,
            "deaths": self.deaths,
            "assists": self.assists,
            "wins": self.wins,
            "losses": self.losses,
            "recentWinrate": self.recent_winrate,
            "complete": self.complete,
            "recent": [line.to_dict() for line in self.lines[:10]],
        }


@dataclass
class StatsResult:
    players: dict[str, PlayerStats]
    #: (puuid_a, puuid_b) -> recent matches the two were partied in. Catches a
    #: consistent duo even when the wider group around them changes.
    co_party: dict[tuple[str, str], int]
    #: The exact groups seen partied together, and how often. This is what makes
    #: a real 4- or 5-stack detectable as one group rather than a chain of
    #: pairs that happen to share a member.
    co_parties: dict[frozenset[str], int]
    matches_examined: int
    matches_fetched: int


def _summarise_match(payload: dict) -> dict | None:
    """Reduce a full match-details payload to the few fields we keep."""
    info = payload.get("matchInfo") or {}
    match_id = str(info.get("matchId") or "")
    if not match_id:
        return None

    won_by_team = {}
    rounds_by_team = {}
    for team in payload.get("teams") or []:
        if isinstance(team, dict) and team.get("teamId"):
            won_by_team[str(team["teamId"])] = bool(team.get("won"))
            rounds_by_team[str(team["teamId"])] = int(team.get("roundsWon") or 0)

    players = []
    for entry in payload.get("players") or []:
        if not isinstance(entry, dict) or entry.get("isObserver"):
            continue
        stats = entry.get("stats") or {}
        players.append(
            {
                "s": str(entry.get("subject") or ""),
                "t": str(entry.get("teamId") or ""),
                "p": str(entry.get("partyId") or ""),
                "c": str(entry.get("characterId") or ""),
                "ct": int(entry.get("competitiveTier") or 0),
                "k": int(stats.get("kills") or 0),
                "d": int(stats.get("deaths") or 0),
                "a": int(stats.get("assists") or 0),
                "r": int(stats.get("roundsPlayed") or 0),
                "sc": int(stats.get("score") or 0),
            }
        )

    return {
        "matchId": match_id,
        "queue": str(info.get("queueID") or info.get("queueId") or ""),
        "mapId": str(info.get("mapId") or ""),
        "start": int(info.get("gameStartMillis") or 0),
        "completed": bool(info.get("isCompleted", True)),
        "won": won_by_team,
        "scores": rounds_by_team,
        "players": players,
    }


class StatsEngine:
    def __init__(self, remote, cfg) -> None:
        self._remote = remote
        self._cfg = cfg
        self._cache = MatchCache()

    @property
    def cached_matches(self) -> int:
        return self._cache.count()

    async def _history_for(self, puuid: str, depth: int, queue: str) -> list[dict]:
        try:
            data = await self._remote.match_history(puuid, count=depth, queue=queue)
        except (RemoteError, SpikeSightError) as exc:
            log.debug("history failed for %s: %s", puuid[:8], exc)
            return []
        history = (data or {}).get("History") or []
        if not history and queue:
            # Unrated-only accounts have no competitive history at all.
            try:
                data = await self._remote.match_history(puuid, count=depth, queue="")
            except (RemoteError, SpikeSightError):
                return []
            history = (data or {}).get("History") or []
        return [h for h in history if isinstance(h, dict) and h.get("MatchID")]

    async def _details(self, match_id: str) -> dict | None:
        cached = self._cache.peek(match_id)
        if cached is not None:
            return cached
        async with self._cache.lock(match_id):
            cached = self._cache.peek(match_id)
            if cached is not None:
                return cached
            try:
                payload = await self._remote.match_details(match_id)
            except (RemoteError, SpikeSightError) as exc:
                log.debug("details failed for %s: %s", match_id[:8], exc)
                return None
            if not isinstance(payload, dict):
                return None
            summary = _summarise_match(payload)
            if summary is None:
                return None
            if summary.get("completed"):
                self._cache.put(match_id, summary)
            return summary

    async def match_summary(self, match_id: str) -> dict | None:
        """Public accessor for one cached/fetched match summary."""
        return await self._details(match_id)

    def _queue_filter(self, lobby_queue: str) -> str:
        """Which queue's history to read.

        Defaults to following the lobby. Pulling competitive history while you
        are in a Swiftplay game is the wrong evidence twice over: the K/D is
        from a different mode, and a group who only ever play Swiftplay
        together would show no shared parties at all.
        """
        configured = str(self._cfg.get("stats.queue", "auto")).strip().lower()
        if configured and configured != "auto":
            return configured
        # An empty filter means "all queues", which is the right fallback when
        # the lobby's queue is unknown.
        return (lobby_queue or "").strip().lower()

    async def collect(
        self, puuids: list[str], progress=None, lobby_queue: str = ""
    ) -> StatsResult:
        depth = int(self._cfg.get("stats.match_history_depth", 5))
        budget = int(self._cfg.get("stats.max_match_details_per_scan", 40))
        queue = self._queue_filter(lobby_queue)
        targets = [p for p in dict.fromkeys(puuids) if p]

        histories = await asyncio.gather(
            *(self._history_for(p, depth, queue) for p in targets),
            return_exceptions=True,
        )

        # Rank candidate matches by how many of our targets appear in them, then
        # by recency. Shared matches are worth far more per request.
        weight: dict[str, int] = defaultdict(int)
        recency: dict[str, int] = {}
        for entries in histories:
            if isinstance(entries, BaseException):
                continue
            for entry in entries:
                match_id = str(entry["MatchID"])
                weight[match_id] += 1
                recency[match_id] = max(
                    recency.get(match_id, 0), int(entry.get("GameStartTime") or 0)
                )

        ordered = sorted(
            weight, key=lambda m: (-weight[m], -recency.get(m, 0))
        )
        # Anything already on disk is free, so take it regardless of budget.
        free = [m for m in ordered if self._cache.peek(m) is not None]
        paid = [m for m in ordered if self._cache.peek(m) is None][:budget]
        selected = free + paid

        summaries: list[dict] = []
        done = 0
        for match_id in selected:
            summary = await self._details(match_id)
            done += 1
            if summary:
                summaries.append(summary)
            if progress is not None and (done % 5 == 0 or done == len(selected)):
                await progress(done, len(selected))

        stats = {p: PlayerStats(puuid=p) for p in targets}
        target_set = set(targets)
        co_party: dict[tuple[str, str], int] = defaultdict(int)
        co_parties: dict[frozenset[str], int] = defaultdict(int)

        for summary in sorted(summaries, key=lambda s: -s.get("start", 0)):
            by_party: dict[str, list[str]] = defaultdict(list)
            for row in summary.get("players", []):
                puuid = row.get("s", "")
                party_id = row.get("p", "")
                if puuid in target_set and party_id:
                    by_party[party_id].append(puuid)

                if puuid not in stats:
                    continue
                record = stats[puuid]
                won = summary.get("won", {}).get(row.get("t", ""))
                record.matches += 1
                record.kills += row.get("k", 0)
                record.deaths += row.get("d", 0)
                record.assists += row.get("a", 0)
                record.rounds += row.get("r", 0)
                record.score += row.get("sc", 0)
                if won is True:
                    record.wins += 1
                elif won is False:
                    record.losses += 1
                record.lines.append(
                    MatchLine(
                        match_id=summary["matchId"],
                        started_ms=summary.get("start", 0),
                        queue=summary.get("queue", ""),
                        map_id=summary.get("mapId", ""),
                        won=won,
                        kills=row.get("k", 0),
                        deaths=row.get("d", 0),
                        assists=row.get("a", 0),
                        rounds=row.get("r", 0),
                        score=row.get("sc", 0),
                        character_id=row.get("c", ""),
                        competitive_tier=row.get("ct", 0),
                    )
                )

            for members in by_party.values():
                members = sorted(set(members))
                if len(members) < 2:
                    continue
                # The group as it actually was, plus every pair inside it.
                co_parties[frozenset(members)] += 1
                for i, first in enumerate(members):
                    for second in members[i + 1:]:
                        co_party[(first, second)] += 1

        for record in stats.values():
            record.complete = True

        return StatsResult(
            players=stats,
            co_party=dict(co_party),
            co_parties=dict(co_parties),
            matches_examined=len(selected),
            matches_fetched=len(paid),
        )
