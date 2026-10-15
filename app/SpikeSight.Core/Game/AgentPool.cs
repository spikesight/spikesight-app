namespace SpikeSight.Core.Game;

/// <summary>How you have done on one agent.</summary>
public sealed record AgentRecord(
    string CharacterId, int Games, int Wins, int Losses, double? Kd, int? Acs)
{
    public double? Winrate => Games == 0 ? null : Math.Round(Wins / (double)Games, 3);
}

/// <summary>
/// Your best agents, from your own recent matches.
///
/// Per map rather than overall, because maps suit different agents - a Viper
/// record on Icebox says nothing about Ascent. Built from the match summaries
/// already cached on disk, so the overlay costs nothing extra to fill.
/// </summary>
public static class AgentPool
{
    /// <summary>A single match is thin evidence, but thin evidence beats none
    /// when the alternative is a blank panel during agent select.</summary>
    public const int MinGamesOnMap = 1;

    public static List<AgentRecord> Best(
        IEnumerable<MatchSummary> matches, string puuid, string? mapId, int take = 3)
    {
        var tally = new Dictionary<string, (int Games, int Wins, int Kills, int Deaths,
                                            int Rounds, int Score)>();

        foreach (var match in matches)
        {
            if (mapId is { Length: > 0 } && !string.Equals(match.MapId, mapId, StringComparison.OrdinalIgnoreCase))
                continue;

            var row = match.Players.FirstOrDefault(p => p.Puuid == puuid);
            if (row is null || row.CharacterId.Length == 0) continue;

            var won = match.WonByTeam.TryGetValue(row.Team, out var w) && w;
            var current = tally.GetValueOrDefault(row.CharacterId);
            tally[row.CharacterId] = (
                current.Games + 1,
                current.Wins + (won ? 1 : 0),
                current.Kills + row.Kills,
                current.Deaths + row.Deaths,
                // Deathmatch has one "round", so it would wreck an ACS the
                // same way it does everywhere else.
                current.Rounds + (row.Rounds >= PlayerStats.MinRoundsForAcs ? row.Rounds : 0),
                current.Score + (row.Rounds >= PlayerStats.MinRoundsForAcs ? row.Score : 0));
        }

        return tally
            .Where(entry => entry.Value.Games >= MinGamesOnMap)
            .Select(entry => new AgentRecord(
                entry.Key,
                entry.Value.Games,
                entry.Value.Wins,
                entry.Value.Games - entry.Value.Wins,
                entry.Value.Deaths == 0 ? null
                    : Math.Round(entry.Value.Kills / (double)entry.Value.Deaths, 2),
                entry.Value.Rounds == 0 ? null
                    : (int)Math.Round(entry.Value.Score / (double)entry.Value.Rounds)))
            // Win rate first, then how much evidence there is for it, so one
            // lucky game does not outrank a solid five.
            .OrderByDescending(a => a.Winrate ?? 0)
            .ThenByDescending(a => a.Games)
            .Take(take)
            .ToList();
    }
}
