"""Win probability estimate for the current lobby.

This is a heuristic, not a model trained on outcomes, and the UI says so. It
compares the two teams' average rating points (see :mod:`ranks`) through a
logistic curve, then nudges the result with recent form when deep stats are
available.

Unranked players are imputed as the average of every *known* rank in the
lobby, which is a much better guess than treating them as Iron.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import ranks


@dataclass
class WinProbability:
    probability: float          # 0..1 for the "ally" side
    confidence: str             # "high" | "medium" | "low"
    ally_points: float | None
    enemy_points: float | None
    ally_label: str
    enemy_label: str
    known_ranks: int
    total_players: int
    note: str

    def to_dict(self) -> dict:
        return {
            "probability": round(self.probability, 4),
            "percent": int(round(self.probability * 100)),
            "confidence": self.confidence,
            "allyPoints": None if self.ally_points is None else round(self.ally_points),
            "enemyPoints": None if self.enemy_points is None else round(self.enemy_points),
            "allyLabel": self.ally_label,
            "enemyLabel": self.enemy_label,
            "knownRanks": self.known_ranks,
            "totalPlayers": self.total_players,
            "note": self.note,
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def estimate(
    *,
    cfg,
    ally: list[dict],
    enemy: list[dict],
) -> WinProbability | None:
    """``ally`` and ``enemy`` are lists of ``{"points": float|None, "kd": float|None}``."""
    if not ally or not enemy:
        return None

    scale = float(cfg.get("winprob.elo_scale", 120.0)) or 120.0

    known = [p["points"] for p in ally + enemy if p.get("points") is not None]
    total = len(ally) + len(enemy)
    fallback = _mean(known)

    if fallback is None:
        return WinProbability(
            probability=0.5,
            confidence="low",
            ally_points=None,
            enemy_points=None,
            ally_label="Unknown",
            enemy_label="Unknown",
            known_ranks=0,
            total_players=total,
            note="No ranks visible yet.",
        )

    def side_points(side: list[dict]) -> float:
        return _mean([
            p["points"] if p.get("points") is not None else fallback for p in side
        ]) or fallback

    ally_points = side_points(ally)
    enemy_points = side_points(enemy)
    delta = ally_points - enemy_points

    # Recent K/D is a weak but real signal; worth about a third of a division
    # at the extremes, and only when most of the lobby has stats.
    ally_kd = _mean([p["kd"] for p in ally if p.get("kd") is not None])
    enemy_kd = _mean([p["kd"] for p in enemy if p.get("kd") is not None])
    form_note = ""
    if ally_kd is not None and enemy_kd is not None:
        kd_delta = max(-0.6, min(0.6, ally_kd - enemy_kd))
        bonus = kd_delta * 55.0
        delta += bonus
        if abs(bonus) >= 8:
            direction = "your" if bonus > 0 else "the enemy"
            form_note = f" Recent form favors {direction} side."

    probability = 1.0 / (1.0 + math.exp(-delta / scale))
    probability = max(0.02, min(0.98, probability))

    coverage = len(known) / total if total else 0.0
    if coverage >= 0.9:
        confidence = "high"
    elif coverage >= 0.6:
        confidence = "medium"
    else:
        confidence = "low"

    note = (
        f"Rank-weighted estimate from {len(known)}/{total} visible ranks."
        + form_note
    )

    return WinProbability(
        probability=probability,
        confidence=confidence,
        ally_points=ally_points,
        enemy_points=enemy_points,
        ally_label=ranks.points_to_label(ally_points),
        enemy_label=ranks.points_to_label(enemy_points),
        known_ranks=len(known),
        total_players=total,
        note=note,
    )
