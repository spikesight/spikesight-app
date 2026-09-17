"""Reading a player's MMR payload.

Shared by the live scoreboard and the career/history view, which need exactly
the same interpretation of Riot's ``SeasonalInfoBySeasonID`` blob.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ranks
from .content import GameContent


def norm_id(value: str | None) -> str:
    return (value or "").replace("-", "").lower()


@dataclass
class RankView:
    tier: int | None = None
    rr: int | None = None
    leaderboard: int | None = None

    def to_dict(self) -> dict:
        return {
            **ranks.tier_dict(self.tier, self.rr),
            "leaderboardRank": self.leaderboard,
            "points": ranks.rating_points(self.tier, self.rr),
        }


def seasonal(mmr: dict | None) -> dict[str, dict]:
    """``{normalized season id: seasonal info}`` for the competitive queue."""
    queue_skills = ((mmr or {}).get("QueueSkills") or {}).get("competitive") or {}
    seasons = queue_skills.get("SeasonalInfoBySeasonID") or {}
    return {
        norm_id(key): value
        for key, value in seasons.items()
        if isinstance(value, dict)
    }


def current_rank(
    mmr: dict | None, seasons: dict[str, dict], content: GameContent
) -> RankView:
    """Live rank: the current act's entry, falling back to the last update."""
    act = content.current_act()
    info = seasons.get(act.id) if act else None

    if info and (info.get("NumberOfGames") or info.get("CompetitiveTier")):
        return RankView(
            tier=int(info.get("CompetitiveTier") or 0),
            rr=int(info.get("RankedRating") or 0),
            leaderboard=int(info.get("LeaderboardRank") or 0) or None,
        )

    latest = (mmr or {}).get("LatestCompetitiveUpdate") or {}
    if latest.get("TierAfterUpdate"):
        return RankView(
            tier=int(latest.get("TierAfterUpdate") or 0),
            rr=int(latest.get("RankedRatingAfterUpdate") or 0),
        )
    return RankView()


def peak(seasons: dict[str, dict]) -> tuple[int, str] | None:
    """Highest tier ever reached, and the act id it happened in."""
    best_tier = 0
    best_act = ""
    for season_id, info in seasons.items():
        candidates = [int(info.get("CompetitiveTier") or 0)]
        wins_by_tier = info.get("WinsByTier") or {}
        if isinstance(wins_by_tier, dict):
            for tier_key, wins in wins_by_tier.items():
                try:
                    if int(wins) > 0:
                        candidates.append(int(tier_key))
                except (TypeError, ValueError):
                    continue
        season_best = max(candidates)
        if season_best > best_tier:
            best_tier = season_best
            best_act = season_id
    if best_tier < 3:
        return None
    return best_tier, best_act


def act_record(seasons: dict[str, dict], act_id: str | None) -> dict:
    info = seasons.get(act_id or "") or {}
    games = int(info.get("NumberOfGames") or 0)
    wins = int(info.get("NumberOfWins") or 0)
    return {
        "games": games,
        "wins": wins,
        "losses": max(0, games - wins),
        "winrate": round(wins / games, 3) if games else None,
    }
