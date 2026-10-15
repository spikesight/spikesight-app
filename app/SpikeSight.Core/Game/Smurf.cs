namespace SpikeSight.Core.Game;

/// <summary>One reason a player scored, with the weight it contributed.</summary>
public sealed record Signal(string Label, int Weight, string Detail);

public sealed record SmurfVerdict(
    int Score, bool Flagged, IReadOnlyList<Signal> Signals, bool LevelHidden);

/// <summary>
/// Smurf heuristics.
///
/// A smurf is a strong player on a young account, so the score combines
/// account age with performance. No single signal is enough on its own: a
/// level-40 Silver is just new, and a Radiant on a level-900 account is just
/// Radiant.
///
/// Every contributing signal comes back with its own weight, so the board can
/// explain *why* somebody was flagged rather than asking anyone to trust a
/// number.
///
/// Account level is excluded entirely when the player has hidden it, and is
/// never reconstructed from match history - that would defeat the setting.
/// </summary>
public static class Smurf
{
    public static SmurfVerdict Evaluate(
        Settings settings,
        int? accountLevel,
        bool levelHidden,
        int? currentTier,
        int? peakTier,
        int? actGames = null,
        int? actWins = null,
        double? kd = null,
        int recentMatches = 0,
        double? recentWinrate = null)
    {
        if (!settings.SmurfDetectionEnabled)
            return new SmurfVerdict(0, false, [], levelHidden);

        var signals = new List<Signal>();
        var score = 0;

        var effectiveTier = Math.Max(currentTier ?? 0, peakTier ?? 0);
        var tierInfo = Ranks.Of(effectiveTier);
        var highRank = effectiveTier >= 15;  // Platinum 1 and above

        // --- account age --------------------------------------------------
        if (!levelHidden && accountLevel is > 0 and var level
            && level < settings.LevelThreshold)
        {
            // Right at the threshold is mild; level 20 is severe.
            var severity = Math.Min(1.0,
                (settings.LevelThreshold - level) / (double)Math.Max(1, settings.LevelThreshold));
            var weight = (int)(12 + 23 * severity);
            signals.Add(new Signal("Low account level", weight,
                $"Level {level} (below {settings.LevelThreshold})"));
            score += weight;

            if (highRank)
            {
                weight = effectiveTier >= 21 ? 25 : 15;
                signals.Add(new Signal("High rank on a young account", weight,
                    $"{tierInfo.Name} at level {level}"));
                score += weight;
            }
        }

        // --- performance ---------------------------------------------------
        if (kd is { } ratio && recentMatches >= 3 && ratio >= settings.KdThreshold)
        {
            var weight = ratio < settings.KdThreshold + 0.35 ? 15 : 25;
            signals.Add(new Signal("High K/D", weight,
                $"{ratio:F2} K/D across {recentMatches} matches"));
            score += weight;
        }

        var decided = actGames ?? 0;
        if (decided >= settings.MinGamesForWinrate && actWins is { } wins)
        {
            var winrate = wins / (double)decided;
            if (winrate >= settings.WinrateThreshold)
            {
                var weight = winrate < settings.WinrateThreshold + 0.1 ? 15 : 22;
                signals.Add(new Signal("High act win rate", weight,
                    $"{winrate:P0} over {decided} act games"));
                score += weight;
            }
        }
        else if (recentWinrate is { } recent && recentMatches >= 4 && recent >= 0.75)
        {
            signals.Add(new Signal("Winning streak", 10,
                $"{recent:P0} of the last {recentMatches} matches"));
            score += 10;
        }

        // --- climbing fast ---------------------------------------------------
        if (!levelHidden && accountLevel is > 0 and var lvl
            && lvl < settings.LevelThreshold * 2
            && decided is > 0 and <= 15
            && effectiveTier >= 18)  // Diamond 1+
        {
            signals.Add(new Signal("Placed high, few games", 14,
                $"{tierInfo.Name} after only {decided} act games"));
            score += 14;
        }

        score = Math.Clamp(score, 0, 100);
        return new SmurfVerdict(
            score,
            score >= settings.SmurfScoreThreshold,
            signals.OrderByDescending(s => s.Weight).ToList(),
            levelHidden);
    }
}
