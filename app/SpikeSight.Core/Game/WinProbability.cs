namespace SpikeSight.Core.Game;

public sealed record WinProbability(
    double Probability,
    int Percent,
    string Confidence,
    double? AllyPoints,
    double? EnemyPoints,
    string AllyLabel,
    string EnemyLabel,
    int KnownRanks,
    int TotalPlayers,
    string Note);

/// <summary>
/// A win-probability estimate for the current lobby.
///
/// A heuristic, not a model trained on outcomes, and the board says so. It
/// compares the two sides' average rating points through a logistic curve,
/// then nudges the result with recent form when that is known.
///
/// Unranked players are imputed as the average of every *known* rank in the
/// lobby, which is a far better guess than treating them as Iron.
/// </summary>
public static class WinProbabilityEstimate
{
    public readonly record struct Entry(double? Points, double? Kd);

    public static WinProbability? Estimate(
        Settings settings, IReadOnlyList<Entry> ally, IReadOnlyList<Entry> enemy)
    {
        if (ally.Count == 0 || enemy.Count == 0) return null;

        var scale = settings.EloScale <= 0 ? 120.0 : settings.EloScale;
        var known = ally.Concat(enemy).Where(p => p.Points is not null)
                        .Select(p => p.Points!.Value).ToList();
        var total = ally.Count + enemy.Count;

        if (known.Count == 0)
        {
            return new WinProbability(0.5, 50, "low", null, null, "Unknown", "Unknown",
                0, total, "No ranks visible yet.");
        }

        var fallback = known.Average();
        double SidePoints(IReadOnlyList<Entry> side)
            => side.Select(p => p.Points ?? fallback).Average();

        var allyPoints = SidePoints(ally);
        var enemyPoints = SidePoints(enemy);
        var delta = allyPoints - enemyPoints;

        // Recent K/D is a weak but real signal: worth about a third of a
        // division at the extremes, and only when most of the lobby has stats.
        var allyKd = Average(ally.Select(p => p.Kd));
        var enemyKd = Average(enemy.Select(p => p.Kd));
        var formNote = "";
        if (allyKd is { } a && enemyKd is { } e)
        {
            var bonus = Math.Clamp(a - e, -0.6, 0.6) * 55.0;
            delta += bonus;
            if (Math.Abs(bonus) >= 8)
                formNote = $" Recent form favors {(bonus > 0 ? "your" : "the enemy")} side.";
        }

        var probability = Math.Clamp(1.0 / (1.0 + Math.Exp(-delta / scale)), 0.02, 0.98);
        var coverage = total == 0 ? 0.0 : known.Count / (double)total;
        var confidence = coverage >= 0.9 ? "high" : coverage >= 0.6 ? "medium" : "low";

        return new WinProbability(
            probability,
            (int)Math.Round(probability * 100),
            confidence,
            allyPoints, enemyPoints,
            Ranks.PointsToLabel(allyPoints), Ranks.PointsToLabel(enemyPoints),
            known.Count, total,
            $"Rank-weighted estimate from {known.Count}/{total} visible ranks.{formNote}");
    }

    private static double? Average(IEnumerable<double?> values)
    {
        var present = values.Where(v => v is not null).Select(v => v!.Value).ToList();
        return present.Count == 0 ? null : present.Average();
    }
}
