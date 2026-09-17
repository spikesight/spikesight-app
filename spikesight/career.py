"""Your own match history: the view for while you are queueing.

Combines three things the client already exposes:

* ``mmr/v1/players/{puuid}/competitiveupdates`` - RR before/after and the tier
  movement for each ranked game. This is where "+18 RR" comes from.
* ``match-history`` - so unrated, Swiftplay and deathmatch games show up too,
  not just the ranked ones.
* ``match-details`` - K/D/A, ACS, agent and the round score, from the same
  disk-cached summaries the scoreboard uses.

Everything here is about *your* account, so no redaction applies - but the same
rate limiter and cache do.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import mmrview, ranks
from .content import GameContent
from .errors import RemoteError, SpikeSightError

log = logging.getLogger(__name__)

DEFAULT_COUNT = 15
#: Upper bound so a wild ?count= cannot turn into a hundred detail fetches.
MAX_COUNT = 30


@dataclass
class _RRUpdate:
    match_id: str
    started_ms: int
    map_id: str
    queue: str
    earned: int
    rr_after: int
    rr_before: int
    tier_after: int
    tier_before: int
    afk_penalty: int


def _parse_updates(payload: dict | None) -> dict[str, _RRUpdate]:
    out: dict[str, _RRUpdate] = {}
    for entry in (payload or {}).get("Matches") or []:
        if not isinstance(entry, dict) or not entry.get("MatchID"):
            continue
        match_id = str(entry["MatchID"])
        out[match_id] = _RRUpdate(
            match_id=match_id,
            started_ms=int(entry.get("MatchStartTime") or 0),
            map_id=str(entry.get("MapID") or ""),
            queue=str(entry.get("QueueID") or ""),
            earned=int(entry.get("RankedRatingEarned") or 0),
            rr_after=int(entry.get("RankedRatingAfterUpdate") or 0),
            rr_before=int(entry.get("RankedRatingBeforeUpdate") or 0),
            tier_after=int(entry.get("TierAfterUpdate") or 0),
            tier_before=int(entry.get("TierBeforeUpdate") or 0),
            afk_penalty=int(entry.get("AFKPenalty") or 0),
        )
    return out


def _movement(update: _RRUpdate | None) -> str | None:
    if update is None:
        return None
    if update.tier_after > update.tier_before:
        return "promoted"
    if update.tier_after < update.tier_before:
        return "demoted"
    return None


async def build(
    *,
    remote,
    content: GameContent,
    stats_engine,
    puuid: str,
    count: int = DEFAULT_COUNT,
) -> dict:
    count = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))

    mmr = None
    try:
        mmr = await remote.mmr(puuid)
    except (RemoteError, SpikeSightError) as exc:
        log.warning("MMR fetch failed for history view: %s", exc)

    seasons = mmrview.seasonal(mmr)
    current = mmrview.current_rank(mmr, seasons, content)
    current_act = content.current_act()
    previous_act = content.previous_act()

    peak_block = None
    peak = mmrview.peak(seasons)
    if peak:
        act = content.act(peak[1])
        peak_block = {"rank": ranks.tier_dict(peak[0]),
                      "act": act.to_dict() if act else None}

    previous_block = None
    if previous_act:
        info = seasons.get(previous_act.id)
        if info and int(info.get("CompetitiveTier") or 0) >= 3:
            previous_block = {
                "rank": ranks.tier_dict(int(info["CompetitiveTier"])),
                "act": previous_act.to_dict(),
                **mmrview.act_record(seasons, previous_act.id),
            }

    # --- assemble the match list ------------------------------------------
    updates: dict[str, _RRUpdate] = {}
    try:
        updates = _parse_updates(await remote.competitive_updates(puuid, count=count))
    except (RemoteError, SpikeSightError) as exc:
        log.warning("Competitive updates failed: %s", exc)

    history_ids: list[tuple[str, int]] = []
    try:
        payload = await remote.match_history(puuid, count=count, queue="")
        for entry in (payload or {}).get("History") or []:
            if isinstance(entry, dict) and entry.get("MatchID"):
                history_ids.append(
                    (str(entry["MatchID"]), int(entry.get("GameStartTime") or 0))
                )
    except (RemoteError, SpikeSightError) as exc:
        log.warning("Match history failed: %s", exc)

    merged: dict[str, int] = {mid: u.started_ms for mid, u in updates.items()}
    for match_id, started in history_ids:
        merged.setdefault(match_id, started)

    ordered = sorted(merged.items(), key=lambda item: -item[1])[:count]

    matches = []
    for match_id, started_ms in ordered:
        summary = await stats_engine.match_summary(match_id)
        update = updates.get(match_id)
        row = None
        if summary:
            row = next(
                (p for p in summary.get("players", []) if p.get("s") == puuid), None
            )

        if summary is None and update is None:
            continue

        map_id = (summary or {}).get("mapId") or (update.map_id if update else "")
        queue = (summary or {}).get("queue") or (update.queue if update else "")
        team = (row or {}).get("t", "")
        scores = (summary or {}).get("scores") or {}
        won = (summary or {}).get("won", {}).get(team) if summary and team else None

        rounds_won = scores.get(team)
        rounds_lost = None
        if scores and team:
            other = [v for k, v in scores.items() if k != team]
            rounds_lost = other[0] if other else None

        kills = (row or {}).get("k")
        deaths = (row or {}).get("d")
        rounds = (row or {}).get("r") or 0
        score = (row or {}).get("sc") or 0
        character_id = (row or {}).get("c", "")

        matches.append(
            {
                "matchId": match_id,
                "startedMs": started_ms or (summary or {}).get("start", 0),
                "mapId": map_id,
                "map": content.map_name(map_id),
                "queue": queue,
                "agent": {
                    "id": character_id,
                    "name": content.agent_name(character_id),
                    "icon": content.agent_icon(character_id),
                },
                "won": won,
                "roundsWon": rounds_won,
                "roundsLost": rounds_lost,
                "kills": kills,
                "deaths": deaths,
                "assists": (row or {}).get("a"),
                "rounds": rounds,
                "acs": int(round(score / rounds)) if rounds else None,
                "kd": round(kills / max(1, deaths), 2) if kills is not None else None,
                "rrEarned": update.earned if update else None,
                "rrAfter": update.rr_after if update else None,
                "tierAfter": update.tier_after if update else None,
                "rank": ranks.tier_dict(update.tier_after, update.rr_after)
                if update else None,
                "movement": _movement(update),
                "afkPenalty": update.afk_penalty if update else 0,
                "statsMissing": row is None,
            }
        )

    # --- roll-ups ----------------------------------------------------------
    decided = [m for m in matches if m["won"] is not None]
    wins = sum(1 for m in decided if m["won"])
    ranked = [m for m in matches if m["rrEarned"] is not None]
    total_kills = sum(m["kills"] or 0 for m in matches)
    total_deaths = sum(m["deaths"] or 0 for m in matches)
    total_rounds = sum(m["rounds"] or 0 for m in matches)
    total_score = sum(
        (m["acs"] or 0) * (m["rounds"] or 0) for m in matches
    )

    summary_block = {
        "matches": len(matches),
        "wins": wins,
        "losses": len(decided) - wins,
        "winrate": round(wins / len(decided), 3) if decided else None,
        "kills": total_kills,
        "deaths": total_deaths,
        "kd": round(total_kills / max(1, total_deaths), 2) if total_deaths else None,
        "acs": int(round(total_score / total_rounds)) if total_rounds else None,
        "rrNet": sum(m["rrEarned"] or 0 for m in ranked) if ranked else None,
        "rankedMatches": len(ranked),
        "bestRR": max((m["rrEarned"] for m in ranked), default=None),
        "worstRR": min((m["rrEarned"] for m in ranked), default=None),
    }

    # Oldest first, so the sparkline reads left to right.
    timeline = [
        {
            "matchId": m["matchId"],
            "startedMs": m["startedMs"],
            "rr": m["rrAfter"],
            "earned": m["rrEarned"],
            "tier": m["tierAfter"],
            "points": ranks.rating_points(m["tierAfter"], m["rrAfter"]),
        }
        for m in reversed(ranked)
    ]

    return {
        "puuid": puuid,
        "rank": current.to_dict(),
        "peak": peak_block,
        "previousAct": previous_block,
        "act": {
            **mmrview.act_record(seasons, current_act.id if current_act else None),
            "act": current_act.to_dict() if current_act else None,
        },
        "matches": matches,
        "summary": summary_block,
        "rrTimeline": timeline,
    }
