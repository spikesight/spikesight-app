"""A synthetic lobby, for looking at the UI without being in a match.

Every name in here is invented. Nothing in this file should ever be copied
from a real lobby - the demo ships to whoever you hand the app to.

``python -m spikesight --demo`` serves this instead of talking to Riot at all:
no lockfile, no credentials, no network. Useful for tuning the layout, for
screenshots, and for checking that the privacy redactions look right.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from urllib.parse import quote

from . import content as content_module
from . import privacy, ranks, smurf, winprob
from .config import Config
from .notes import NotesStore

_AGENTS_ALLY = ["Jett", "Omen", "Sova", "Killjoy", "Reyna"]
_AGENTS_ENEMY = ["Raze", "Viper", "Fade", "Chamber", "Breach"]

# Reuse the real bundled tables so the demo exercises the same icon path the
# live scoreboard does.
_CONTENT = content_module._base_content()
_ID_BY_NAME = {name.lower(): key for key, name in _CONTENT.agents.items()}


def _agent_block(name: str) -> dict:
    agent_id = _ID_BY_NAME.get(name.lower(), "")
    return {"id": agent_id, "name": name, "icon": _CONTENT.agent_icon(agent_id)}


def _tracker(name: str, incognito: bool) -> str | None:
    if incognito:
        return None
    return ("https://tracker.gg/valorant/profile/riot/"
            f"{quote(name, safe='')}/overview")


def _player(
    *,
    puuid: str,
    side: str,
    agent: str,
    name: str,
    tier: int,
    rr: int,
    peak_tier: int,
    peak_act: str,
    prev_tier: int,
    wins: int,
    games: int,
    level: int | None,
    kd: float,
    acs: int,
    party: dict | None = None,
    incognito: bool = False,
    level_hidden: bool = False,
    act_hidden: bool = False,
    notes: list | None = None,
    leaderboard: int | None = None,
    encounters: int = 0,
    is_self: bool = False,
    cfg: Config | None = None,
) -> dict:
    notes = notes or []
    verdict = smurf.evaluate(
        cfg=cfg,
        account_level=None if level_hidden else level,
        level_hidden=level_hidden,
        current_tier=tier,
        peak_tier=None if act_hidden else peak_tier,
        act_games=0 if act_hidden else games,
        act_wins=0 if act_hidden else wins,
        kd=kd,
        recent_matches=5,
        recent_winrate=0.6,
    )
    display = agent if incognito else name
    return {
        "puuid": puuid,
        "team": "Blue" if side == "ally" else "Red",
        "side": side,
        "isSelf": is_self,
        "isCoach": False,
        "selectionState": "locked",
        "agent": _agent_block(agent),
        "trackerUrl": _tracker(name, incognito),
        "name": {
            "display": display,
            "gameName": None if incognito else name.split("#")[0],
            "tagLine": None if incognito else name.split("#")[-1],
            "hidden": incognito,
            "hiddenId": privacy.hidden_tag(puuid) if incognito else None,
            "reason": "Player has Streamer Mode enabled" if incognito else "",
        },
        "rank": ranks.tier_dict(tier, rr),
        "ratingPoints": ranks.rating_points(tier, rr),
        "leaderboardRank": leaderboard,
        "peak": None if act_hidden else {
            "rank": ranks.tier_dict(peak_tier),
            "act": {"id": "demo", "short": peak_act, "full": peak_act, "active": False},
        },
        "previousAct": None if act_hidden else {
            "rank": ranks.tier_dict(prev_tier),
            "act": {"id": "demo", "short": "V26A4", "full": "V26 // ACT IV", "active": False},
            "games": 40,
            "wins": 21,
        },
        "act": {
            "hidden": act_hidden,
            "games": 0 if act_hidden else games,
            "wins": 0 if act_hidden else wins,
            "losses": 0 if act_hidden else max(0, games - wins),
            "winrate": None if act_hidden or not games else round(wins / games, 3),
            "act": {"id": "demo", "short": "V26A5", "full": "V26 // ACT V", "active": True},
        },
        "level": None if level_hidden else level,
        "levelHidden": level_hidden,
        "party": party,
        "smurf": verdict.to_dict(),
        "stats": {
            "matches": 5,
            "kd": kd,
            "kda": round(kd + 0.4, 2),
            "acs": acs,
            "kills": int(kd * 60),
            "deaths": 60,
            "assists": 22,
            "wins": 3,
            "losses": 2,
            "recentWinrate": 0.6,
            "complete": True,
            "recent": [
                {"matchId": f"{puuid}-{i}", "startedMs": 1788000000000 - i * 86400000,
                 "queue": "competitive", "mapId": "/Game/Maps/Ascent/Ascent",
                 "won": i % 2 == 0, "kills": 18 + i, "deaths": 15, "assists": 5,
                 "rounds": 22, "score": acs * 22, "characterId": "", "tier": tier}
                for i in range(5)
            ],
        },
        "notes": {
            "count": len(notes),
            "maxSeverity": max((n["severity"] for n in notes), default=0),
            "items": notes,
        },
        "encounters": {"count": encounters, "ally": encounters // 2,
                       "enemy": encounters - encounters // 2,
                       "firstSeen": "2026-07-11T20:00:00+00:00",
                       "lastSeen": "2026-08-24T20:00:00+00:00"}
        if encounters else {"count": 0, "ally": 0, "enemy": 0,
                            "firstSeen": None, "lastSeen": None},
        "privacy": {
            "incognito": incognito,
            "hideAccountLevel": level_hidden,
            "actRankHidden": act_hidden,
            "leaderboardAnonymized": False,
            "reasons": (
                (["Streamer Mode is on for this player"] if incognito else [])
                + (["Account level hidden by player"] if level_hidden else [])
                + (["Act rank badge hidden by player"] if act_hidden else [])
            ),
        },
    }


def _note(severity: int, body: str, tags: list[str]) -> dict:
    meta = {1: ("watch", "Watch", "#d8a13a"),
            2: ("avoid", "Avoid", "#e0722f"),
            3: ("dodge", "Dodge", "#d13c4b")}[severity]
    return {
        "id": severity,
        "puuid": "demo",
        "severity": severity,
        "severityKey": meta[0],
        "severityLabel": meta[1],
        "severityColor": meta[2],
        "tags": tags,
        "body": body,
        "matchId": None,
        "createdAt": "2026-08-02T21:14:00+00:00",
        "updatedAt": "2026-08-02T21:14:00+00:00",
    }


def build_snapshot(cfg: Config) -> dict:
    duo = {"group": 1, "size": 2, "confidence": "confirmed",
           "evidence": "Shared live party id"}
    stack = {"group": 2, "size": 4, "confidence": "likely",
             "evidence": "Partied together in 4 recent matches"}

    ally = [
        _player(puuid="a1", side="ally", agent=_AGENTS_ALLY[0], name="you#0000",
                tier=17, rr=64, peak_tier=19, peak_act="V25A3", prev_tier=17,
                wins=28, games=47, level=266, kd=1.18, acs=231, party=duo,
                is_self=True, cfg=cfg),
        _player(puuid="a2", side="ally", agent=_AGENTS_ALLY[1], name="Bellwether#NA1",
                tier=18, rr=31, peak_tier=20, peak_act="V26A2", prev_tier=18,
                wins=33, games=55, level=412, kd=1.06, acs=214, party=duo, cfg=cfg),
        _player(puuid="a3", side="ally", agent=_AGENTS_ALLY[2], name="quietstorm#0451",
                tier=16, rr=12, peak_tier=17, peak_act="V26A1", prev_tier=15,
                wins=14, games=31, level=88, kd=0.91, acs=178, cfg=cfg),
        _player(puuid="a4", side="ally", agent=_AGENTS_ALLY[3], name="hidden#level",
                tier=17, rr=77, peak_tier=18, peak_act="V25A6", prev_tier=17,
                wins=19, games=30, level=140, kd=1.02, acs=205,
                level_hidden=True, cfg=cfg),
        _player(puuid="a5", side="ally", agent=_AGENTS_ALLY[4], name="onetrickpony#eu",
                tier=15, rr=44, peak_tier=16, peak_act="V26A3", prev_tier=15,
                wins=9, games=24, level=63, kd=0.84, acs=161, encounters=4,
                notes=[_note(2, "Refused to swap off duelist, then went afk in round 14.",
                             ["griefer", "no comms"])], cfg=cfg),
    ]

    enemy = [
        _player(puuid="e1", side="enemy", agent=_AGENTS_ENEMY[0], name="ELEVEN#RUSH",
                tier=20, rr=48, peak_tier=21, peak_act="V26A4", prev_tier=19,
                wins=41, games=62, level=27, kd=1.71, acs=302, party=stack, cfg=cfg),
        _player(puuid="e2", side="enemy", agent=_AGENTS_ENEMY[1], name="slowplay#1337",
                tier=18, rr=9, peak_tier=19, peak_act="V25A5", prev_tier=18,
                wins=25, games=48, level=301, kd=1.03, acs=199, party=stack,
                encounters=2, cfg=cfg),
        _player(puuid="e3", side="enemy", agent=_AGENTS_ENEMY[2], name="thirdwheel#gg",
                tier=17, rr=70, peak_tier=18, peak_act="V26A1", prev_tier=17,
                wins=17, games=33, level=155, kd=0.98, acs=188, party=stack,
                encounters=7,
                notes=[_note(3, "Threw two games in a row after losing a 1v1. "
                                "Dodge if you see them again.",
                             ["thrower", "troll"])], cfg=cfg),
        # Deliberately the busiest row in the demo: a smurf score, a stack badge
        # and a privacy badge all at once, which is what overflows the cell.
        _player(puuid="e4", side="enemy", agent=_AGENTS_ENEMY[3], name="anonymous#pro",
                tier=19, rr=15, peak_tier=22, peak_act="V25A2", prev_tier=20,
                wins=0, games=0, level=None, kd=1.62, acs=268, party=stack,
                incognito=True, act_hidden=True, cfg=cfg),
        _player(puuid="e5", side="enemy", agent=_AGENTS_ENEMY[4], name="justhere#4fun",
                tier=15, rr=3, peak_tier=16, peak_act="V26A2", prev_tier=15,
                wins=6, games=19, level=52, kd=0.79, acs=152, cfg=cfg),
    ]

    ally.sort(key=lambda p: -(p["ratingPoints"] or 0))
    enemy.sort(key=lambda p: -(p["ratingPoints"] or 0))

    probability = winprob.estimate(
        cfg=cfg,
        ally=[{"points": p["ratingPoints"], "kd": p["stats"]["kd"]} for p in ally],
        enemy=[{"points": p["ratingPoints"], "kd": p["stats"]["kd"]} for p in enemy],
    )

    from .scoreboard import _build_alerts  # local import keeps the cycle out of module load

    players = ally + enemy
    return {
        "state": "PREGAME",
        "phase": "enriched",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "self": "a1",
        "message": "Demo lobby - no live data.",
        "match": {
            "id": "demo-match",
            "mapId": "/Game/Maps/Ascent/Ascent",
            "map": "Ascent",
            "mode": "/Game/GameModes/Bomb/BombGameMode.BombGameMode_C",
            "modeName": "Standard",
            "label": "Competitive",
            "queue": "competitive",
            "provisioning": "Matchmaking",
            "isRanked": True,
            "enemyTeamSize": 5,
            "enemyLockCount": 3,
        },
        "winProbability": probability.to_dict() if probability else None,
        "teams": [
            {"side": "ally", "label": "Your team", "players": ally},
            {"side": "enemy", "label": "Enemy team", "players": enemy},
        ],
        "alerts": _build_alerts(players, "PREGAME"),
        "stats": {"matchesExamined": 34, "matchesFetched": 12},
        "connection": {
            "connected": True,
            "message": "Demo mode - nothing is being requested from Riot",
            "region": "demo",
            "shard": "demo",
            "clientVersion": "demo",
            "contentSource": "demo",
            "currentAct": {"id": "demo", "short": "V26A5", "full": "V26 // ACT V",
                           "active": True},
        },
        "settings": {"deepStats": True, "smurfDetection": True,
                     "theme": cfg.get("ui.theme", "dark")},
    }


_DEMO_MAPS = ["Ascent", "Haven", "Split", "Lotus", "Sunset", "Icebox", "Abyss"]
_DEMO_AGENTS = ["Jett", "Omen", "Sova", "Killjoy", "Reyna", "Raze", "Viper"]


def build_history(count: int = 15) -> dict:
    """A plausible-looking ranked history for --demo."""
    import random

    # Seed chosen so the sample contains both a promotion and a demotion.
    rng = random.Random(1)
    now = 1788000000000

    # Walk forward through time so RR carries over correctly, crossing division
    # boundaries where it should - that is what exercises the UP/DOWN badges.
    forward = []
    rr, tier = 40, 16
    for i in range(count):
        won = rng.random() < 0.55
        earned = rng.randint(14, 28) if won else -rng.randint(12, 26)
        tier_before = tier
        rr += earned
        while rr > 99 and tier < 26:
            rr -= 100
            tier += 1
        while rr < 0 and tier > 3:
            rr += 100
            tier -= 1
        rr = max(0, min(99, rr))

        kills, deaths = rng.randint(10, 26), rng.randint(10, 22)
        movement = ("promoted" if tier > tier_before
                    else "demoted" if tier < tier_before else None)
        forward.append({
            "matchId": f"demo-{i}",
            "startedMs": now - (count - 1 - i) * 5_400_000,
            "mapId": "", "map": _DEMO_MAPS[i % len(_DEMO_MAPS)],
            "queue": "competitive",
            "agent": _agent_block(_DEMO_AGENTS[i % len(_DEMO_AGENTS)]),
            "won": won,
            "roundsWon": 13 if won else rng.randint(4, 11),
            "roundsLost": rng.randint(4, 11) if won else 13,
            "kills": kills, "deaths": deaths, "assists": rng.randint(2, 9),
            "rounds": rng.randint(16, 25),
            "acs": rng.randint(140, 290),
            "kd": round(kills / max(1, deaths), 2),
            "rrEarned": earned, "rrAfter": rr, "tierAfter": tier,
            "rank": ranks.tier_dict(tier, rr),
            "movement": movement, "afkPenalty": 0, "statsMissing": False,
        })

    matches = list(reversed(forward))   # newest first, like the real payload
    decided = [m for m in matches if m["won"] is not None]
    wins = sum(1 for m in decided if m["won"])
    return {
        "puuid": "a1",
        "rank": {**ranks.tier_dict(17, 64), "leaderboardRank": None,
                 "points": ranks.rating_points(17, 64)},
        "peak": {"rank": ranks.tier_dict(19),
                 "act": {"id": "demo", "short": "V25A3", "full": "V25 // ACT III",
                         "active": False}},
        "previousAct": {"rank": ranks.tier_dict(17),
                        "act": {"id": "demo", "short": "V26A4",
                                "full": "V26 // ACT IV", "active": False},
                        "games": 40, "wins": 21, "losses": 19, "winrate": 0.525},
        "act": {"games": 47, "wins": 28, "losses": 19, "winrate": 0.596,
                "act": {"id": "demo", "short": "V26A5", "full": "V26 // ACT V",
                        "active": True}},
        "matches": matches,
        "summary": {
            "matches": len(matches), "wins": wins, "losses": len(decided) - wins,
            "winrate": round(wins / len(decided), 3) if decided else None,
            "kills": sum(m["kills"] for m in matches),
            "deaths": sum(m["deaths"] for m in matches),
            "kd": 1.14, "acs": 218,
            "rrNet": sum(m["rrEarned"] for m in matches),
            "rankedMatches": len(matches),
            "bestRR": max(m["rrEarned"] for m in matches),
            "worstRR": min(m["rrEarned"] for m in matches),
        },
        "rrTimeline": [
            {"matchId": m["matchId"], "startedMs": m["startedMs"], "rr": m["rrAfter"],
             "earned": m["rrEarned"], "tier": m["tierAfter"],
             "points": ranks.rating_points(m["tierAfter"], m["rrAfter"])}
            for m in reversed(matches)
        ],
        "_count": count,
    }


class DemoPoller:
    """Drop-in replacement for :class:`~spikesight.poller.Poller`."""

    def __init__(self, cfg: Config, emit) -> None:
        self._cfg = cfg
        self._emit = emit
        # A throwaway database: demo mode must never write sample flags and
        # encounters into your real notes, nor hold that file open alongside
        # the real app.
        self._scratch = tempfile.TemporaryDirectory(prefix="spikesight-demo-",
                                                    ignore_cleanup_errors=True)
        self.notes = NotesStore(Path(self._scratch.name) / "notes.sqlite3")
        self.limiter = type("_L", (), {"total_requests": 0})()
        self.remote = None
        self.builder = None
        self.snapshot = build_snapshot(cfg)
        self.connection = self.snapshot["connection"]

    def start(self) -> None:
        asyncio.get_event_loop().create_task(self.republish())

    async def stop(self) -> None:
        self.notes.close()
        self._scratch.cleanup()

    def request_refresh(self) -> None:
        self.snapshot = build_snapshot(self._cfg)

    async def history(self, count: int = 15, refresh: bool = False) -> dict:
        return build_history(count)

    async def agent_pool(self) -> dict:
        rows = [("Jett", 12, 7, 5, 1.31, 248), ("Reyna", 8, 3, 5, 1.12, 231),
                ("Omen", 5, 3, 2, 0.94, 196)]
        return {
            "map": "Ascent", "queue": "competitive", "ready": True,
            "scope": "map", "matches": 25, "mapMatches": 25, "totalMatches": 60,
            "agents": [
                {**_agent_block(name), "matches": games, "wins": wins,
                 "losses": losses, "winrate": round(wins / games, 3),
                 "kd": kd, "acs": acs}
                for name, games, wins, losses, kd, acs in rows
            ],
        }

    async def republish(self) -> None:
        self.snapshot["settings"]["theme"] = self._cfg.get("ui.theme", "dark")
        await self._emit({"type": "snapshot", "snapshot": self.snapshot})
