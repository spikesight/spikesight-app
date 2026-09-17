"""Smurf heuristics.

A smurf is a strong player on a young account, so the score combines account
age with performance. No single signal is enough on its own: a level-40 Silver
is just new, and a Radiant on a level-900 account is just Radiant.

Signals are additive and capped at 100. Every contributing signal is returned
with its own weight so the UI can explain *why* a player was flagged rather
than showing an unarguable red dot.

Account level is excluded entirely when the player has hidden it. SpikeSight does
not reconstruct a hidden level from match history.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ranks


@dataclass
class Signal:
    label: str
    weight: int
    detail: str

    def to_dict(self) -> dict:
        return {"label": self.label, "weight": self.weight, "detail": self.detail}


@dataclass
class SmurfVerdict:
    score: int
    flagged: bool
    signals: list[Signal]
    level_hidden: bool

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "flagged": self.flagged,
            "levelHidden": self.level_hidden,
            "signals": [s.to_dict() for s in self.signals],
        }


def evaluate(
    *,
    cfg,
    account_level: int | None,
    level_hidden: bool,
    current_tier: int | None,
    peak_tier: int | None,
    act_games: int | None,
    act_wins: int | None,
    kd: float | None,
    recent_matches: int,
    recent_winrate: float | None,
) -> SmurfVerdict:
    if not cfg.get("smurf.enabled", True):
        return SmurfVerdict(score=0, flagged=False, signals=[], level_hidden=level_hidden)

    level_threshold = int(cfg.get("smurf.level_threshold", 60))
    kd_threshold = float(cfg.get("smurf.kd_threshold", 1.45))
    winrate_threshold = float(cfg.get("smurf.winrate_threshold", 0.65))
    min_games = int(cfg.get("smurf.min_games_for_winrate", 8))
    score_threshold = int(cfg.get("smurf.score_threshold", 45))

    signals: list[Signal] = []
    score = 0

    effective_tier = max(current_tier or 0, peak_tier or 0)
    tier_info = ranks.tier(effective_tier)
    high_rank = effective_tier >= 15  # Platinum 1 and above

    # --- account age -------------------------------------------------------
    if not level_hidden and account_level:
        if account_level < level_threshold:
            # Scale: right at the threshold is mild, level 20 is severe.
            severity = min(1.0, (level_threshold - account_level) / max(1, level_threshold))
            weight = int(12 + 23 * severity)
            signals.append(
                Signal(
                    "Low account level",
                    weight,
                    f"Level {account_level} (below {level_threshold})",
                )
            )
            score += weight

            if high_rank:
                weight = 25 if effective_tier >= 21 else 15
                signals.append(
                    Signal(
                        "High rank on a young account",
                        weight,
                        f"{tier_info.name} at level {account_level}",
                    )
                )
                score += weight

    # --- performance -------------------------------------------------------
    if kd is not None and recent_matches >= 3 and kd >= kd_threshold:
        weight = 15 if kd < kd_threshold + 0.35 else 25
        signals.append(
            Signal("High K/D", weight, f"{kd:.2f} K/D across {recent_matches} matches")
        )
        score += weight

    decided_games = act_games or 0
    if decided_games >= min_games and act_wins is not None and decided_games > 0:
        winrate = act_wins / decided_games
        if winrate >= winrate_threshold:
            weight = 15 if winrate < winrate_threshold + 0.1 else 22
            signals.append(
                Signal(
                    "High act win rate",
                    weight,
                    f"{winrate:.0%} over {decided_games} act games",
                )
            )
            score += weight
    elif recent_winrate is not None and recent_matches >= 4 and recent_winrate >= 0.75:
        signals.append(
            Signal(
                "Winning streak",
                10,
                f"{recent_winrate:.0%} of the last {recent_matches} matches",
            )
        )
        score += 10

    # --- climbing fast -----------------------------------------------------
    if (
        not level_hidden
        and account_level
        and account_level < level_threshold * 2
        and decided_games
        and decided_games <= 15
        and effective_tier >= 18  # Diamond 1+
    ):
        signals.append(
            Signal(
                "Placed high, few games",
                14,
                f"{tier_info.name} after only {decided_games} act games",
            )
        )
        score += 14

    score = max(0, min(100, score))
    return SmurfVerdict(
        score=score,
        flagged=score >= score_threshold,
        signals=sorted(signals, key=lambda s: -s.weight),
        level_hidden=level_hidden,
    )
