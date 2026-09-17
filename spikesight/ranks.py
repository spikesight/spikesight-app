"""Competitive tier table and the rating math built on top of it.

Tier numbers are the ones Riot uses on the wire: 0 is Unranked, 1 and 2 are
unused legacy slots, 3-26 are Iron 1 through Immortal 3, and 27 is Radiant.
The names and colors are stable across acts, so they live here rather than
being fetched - it keeps the app fully usable offline and cuts a request.
"""

from __future__ import annotations

from dataclasses import dataclass

GROUPS: list[tuple[str, str, str]] = [
    # (group name, short code, hex color)
    ("Iron", "I", "#5a5a5f"),
    ("Bronze", "B", "#9b6b3e"),
    ("Silver", "S", "#c3ccd0"),
    ("Gold", "G", "#e0c04a"),
    ("Platinum", "P", "#3fa3b8"),
    ("Diamond", "D", "#b070d8"),
    ("Ascendant", "A", "#25b06b"),
    ("Immortal", "IM", "#c0374f"),
]

UNRANKED_COLOR = "#4a4a52"
RADIANT_COLOR = "#f7e8a4"

#: First tier number of each three-division group.
_GROUP_BASE = 3
RADIANT_TIER = 27
MAX_TIER = RADIANT_TIER


@dataclass(frozen=True)
class Tier:
    number: int
    name: str
    short: str
    color: str

    @property
    def is_ranked(self) -> bool:
        return self.number >= _GROUP_BASE

    @property
    def is_immortal_plus(self) -> bool:
        return self.number >= 24


def _build_table() -> dict[int, Tier]:
    table: dict[int, Tier] = {
        0: Tier(0, "Unranked", "UNR", UNRANKED_COLOR),
        1: Tier(1, "Unranked", "UNR", UNRANKED_COLOR),
        2: Tier(2, "Unranked", "UNR", UNRANKED_COLOR),
        RADIANT_TIER: Tier(RADIANT_TIER, "Radiant", "RAD", RADIANT_COLOR),
    }
    number = _GROUP_BASE
    for group, short, color in GROUPS:
        for division in (1, 2, 3):
            table[number] = Tier(number, f"{group} {division}", f"{short}{division}", color)
            number += 1
    return table


TIERS: dict[int, Tier] = _build_table()


def tier(number: int | None) -> Tier:
    if number is None:
        return TIERS[0]
    try:
        number = int(number)
    except (TypeError, ValueError):
        return TIERS[0]
    return TIERS.get(max(0, min(number, MAX_TIER)), TIERS[0])


def tier_dict(number: int | None, rr: int | None = None) -> dict:
    """Serializable tier description for the UI."""
    resolved = tier(number)
    return {
        "tier": resolved.number,
        "name": resolved.name,
        "short": resolved.short,
        "color": resolved.color,
        "ranked": resolved.is_ranked,
        "rr": rr,
    }


def rating_points(tier_number: int | None, rr: int | None) -> float | None:
    """A single comparable number for a rank, in 'RR since Iron 1' units.

    Iron 1 with 0 RR is 0; every division above adds 100. Immortal+ RR is
    cumulative in-game, so it is clamped to keep one high-RR Immortal from
    dominating a team average.
    """
    resolved = tier(tier_number)
    if not resolved.is_ranked:
        return None
    base = (resolved.number - _GROUP_BASE) * 100.0
    points = float(rr or 0)
    if resolved.is_immortal_plus:
        points = min(points, 300.0)
    else:
        points = max(0.0, min(points, 100.0))
    return base + points


def points_to_label(points: float | None) -> str:
    """Inverse of :func:`rating_points`, for describing a team average."""
    if points is None:
        return "Unranked"
    index = _GROUP_BASE + int(points // 100)
    remainder = int(points % 100)
    resolved = tier(min(index, MAX_TIER))
    if resolved.number >= RADIANT_TIER:
        return "Radiant"
    return f"{resolved.name} ({remainder} RR)"
