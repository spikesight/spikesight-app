"""Your best agents, from your own recent matches.

Feeds the overlay's "Your top agents" panel during agent select. It is a
summary of history you already played - how many games, how they went - and
nothing more. It does not pick, hover or lock anything.

Built from the same compact match summaries the scoreboard caches on disk, so
after the first look it costs no requests at all.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Maps suit different agents, so the map you are about to play wins whenever
#: there is anything at all to show for it. Everything else is the fallback
#: for a map you have not played yet, and says so on the panel.
MIN_MAP_MATCHES = 1
TOP_N = 3


@dataclass
class _Tally:
    character_id: str
    matches: int = 0
    wins: int = 0
    losses: int = 0
    kills: int = 0
    deaths: int = 0
    rounds: int = 0
    score: int = 0

    def add(self, row: dict, won: bool | None) -> None:
        self.matches += 1
        if won is True:
            self.wins += 1
        elif won is False:
            self.losses += 1
        self.kills += int(row.get("k") or 0)
        self.deaths += int(row.get("d") or 0)
        self.rounds += int(row.get("r") or 0)
        self.score += int(row.get("sc") or 0)

    def to_dict(self, content) -> dict:
        decided = self.wins + self.losses
        return {
            "id": self.character_id,
            "name": content.agent_name(self.character_id) if content else None,
            "icon": content.agent_icon(self.character_id) if content else None,
            "matches": self.matches,
            "wins": self.wins,
            "losses": self.losses,
            "winrate": round(self.wins / decided, 3) if decided else None,
            "kd": round(self.kills / max(1, self.deaths), 2),
            "acs": int(round(self.score / self.rounds)) if self.rounds else None,
        }


def _rank(tallies: dict[str, _Tally], content) -> list[dict]:
    ordered = sorted(
        tallies.values(),
        key=lambda t: (-t.matches, -(t.wins / max(1, t.wins + t.losses)), -t.kills),
    )
    return [t.to_dict(content) for t in ordered[:TOP_N]]


def aggregate(summaries: list[dict], puuid: str, map_id: str, content=None) -> dict:
    """Top agents on ``map_id``, falling back to every map only with none."""
    on_map: dict[str, _Tally] = {}
    overall: dict[str, _Tally] = {}
    map_matches = 0
    total = 0

    for summary in summaries:
        if not summary:
            continue
        row = next(
            (p for p in summary.get("players") or [] if p.get("s") == puuid), None
        )
        if not row or not row.get("c"):
            continue
        won = (summary.get("won") or {}).get(row.get("t"))
        agent = str(row["c"]).lower()
        total += 1
        overall.setdefault(agent, _Tally(agent)).add(row, won)
        if map_id and summary.get("mapId") == map_id:
            map_matches += 1
            on_map.setdefault(agent, _Tally(agent)).add(row, won)

    use_map = map_matches >= MIN_MAP_MATCHES
    return {
        "scope": "map" if use_map else "all",
        "matches": map_matches if use_map else total,
        "mapMatches": map_matches,
        "totalMatches": total,
        "agents": _rank(on_map if use_map else overall, content),
    }
