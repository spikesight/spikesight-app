using System.Text.Json;
using SpikeSight.Core.Riot;

namespace SpikeSight.Core.Game;

/// <summary>One of your own matches, as the history view shows it.</summary>
public sealed record CareerMatch(
    string MatchId,
    long StartedMs,
    string Map,
    string Queue,
    string? Agent,
    bool? Won,
    int? RoundsWon,
    int? RoundsLost,
    int? Kills,
    int? Deaths,
    int? Assists,
    double? Kd,
    int? Acs,
    int? Performance,
    int? RrEarned,
    int? RrAfter,
    int? TierAfter,
    bool StatsMissing);

public sealed record CareerSummary(
    int Matches, int Wins, int Losses, double? Winrate,
    double? Kd, int? Acs, int? Performance, int? NetRr, int RankedMatches);

public sealed record Career(
    CareerSummary Summary,
    IReadOnlyList<CareerMatch> Matches,
    RankView Current,
    (int Tier, string ActId)? Peak);

/// <summary>
/// Your own recent matches: the view for while you are queueing.
///
/// Two sources, joined on match id. Competitive updates carry the RR and the
/// rank you landed on but nothing about how you played; match details carry
/// the performance but no RR. A match in only one of them still shows, with
/// whichever half is known.
/// </summary>
public static class CareerBuilder
{
    public const int DefaultCount = 15;

    public static async Task<Career> BuildAsync(
        RemoteClient remote, StatsEngine stats, GameContent content,
        string puuid, string? currentActId, int count = DefaultCount,
        CancellationToken ct = default)
    {
        var updates = await CompetitiveUpdatesAsync(remote, puuid, count, ct);

        // Match history as well as competitive updates, so unrated, Swiftplay
        // and Deathmatch games show up rather than only ranked ones.
        var history = new List<(string MatchId, long Started)>();
        if (await remote.MatchHistoryAsync(puuid, count, "", ct) is { } payload
            && payload.TryGetProperty("History", out var entries)
            && entries.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in entries.EnumerateArray())
            {
                if (entry.TryGetProperty("MatchID", out var id)
                    && id.GetString() is { Length: > 0 } matchId)
                {
                    history.Add((matchId, entry.TryGetProperty("GameStartTime", out var t)
                        && t.ValueKind == JsonValueKind.Number ? t.GetInt64() : 0));
                }
            }
        }

        var ordered = history
            .Concat(updates.Select(u => (u.Key, 0L)))
            .GroupBy(m => m.Item1)
            .Select(g => (MatchId: g.Key, Started: g.Max(m => m.Item2)))
            .OrderByDescending(m => m.Started)
            .Take(count)
            .ToList();

        var matches = new List<CareerMatch>();
        foreach (var (matchId, started) in ordered)
        {
            var summary = await stats.SummaryAsync(matchId, ct);
            var update = updates.GetValueOrDefault(matchId);
            if (summary is null && update is null) continue;

            var row = summary?.Players.FirstOrDefault(p => p.Puuid == puuid);
            var team = row?.Team ?? "";
            bool? won = summary is not null && team.Length > 0
                        && summary.WonByTeam.TryGetValue(team, out var w) ? w : null;

            int? roundsWon = summary is not null && team.Length > 0
                && summary.RoundsByTeam.TryGetValue(team, out var mine) ? mine : null;
            int? roundsLost = summary?.RoundsByTeam
                .Where(r => r.Key != team).Select(r => (int?)r.Value).FirstOrDefault();

            matches.Add(new CareerMatch(
                MatchId: matchId,
                StartedMs: started != 0 ? started : summary?.StartedMs ?? 0,
                Map: content.MapName(summary?.MapId) ?? "Unknown map",
                Queue: summary?.Queue ?? "",
                Agent: content.AgentName(row?.CharacterId),
                Won: won,
                RoundsWon: roundsWon,
                RoundsLost: roundsLost,
                Kills: row?.Kills,
                Deaths: row?.Deaths,
                Assists: row?.Assists,
                Kd: row is null ? null : Math.Round(row.Kills / (double)Math.Max(1, row.Deaths), 2),
                // Same reason as PlayerStats.MinRoundsForAcs: Deathmatch has
                // one "round", so this would report the whole match score.
                Acs: row is not null && row.Rounds >= PlayerStats.MinRoundsForAcs
                    ? (int)Math.Round(row.Score / (double)row.Rounds) : null,
                Performance: row?.Performance is { } ps ? (int)Math.Round(ps) : null,
                RrEarned: update?.Earned,
                RrAfter: update?.RrAfter,
                TierAfter: update?.TierAfter,
                StatsMissing: row is null));
        }

        return new Career(Roll(matches), matches,
            MmrView.CurrentRank(null, [], currentActId), null);
    }

    private static CareerSummary Roll(List<CareerMatch> matches)
    {
        var decided = matches.Where(m => m.Won is not null).ToList();
        var wins = decided.Count(m => m.Won is true);
        var ranked = matches.Where(m => m.RrEarned is not null).ToList();

        var kills = matches.Sum(m => m.Kills ?? 0);
        var deaths = matches.Sum(m => m.Deaths ?? 0);
        var scored = matches.Where(m => m.Acs is not null).ToList();
        var performed = matches.Where(m => m.Performance is not null).ToList();

        return new CareerSummary(
            Matches: matches.Count,
            Wins: wins,
            Losses: decided.Count - wins,
            Winrate: decided.Count == 0 ? null : Math.Round(wins / (double)decided.Count, 3),
            Kd: deaths == 0 ? null : Math.Round(kills / (double)deaths, 2),
            Acs: scored.Count == 0 ? null : (int)Math.Round(scored.Average(m => m.Acs!.Value)),
            Performance: performed.Count == 0
                ? null : (int)Math.Round(performed.Average(m => m.Performance!.Value)),
            NetRr: ranked.Count == 0 ? null : ranked.Sum(m => m.RrEarned!.Value),
            RankedMatches: ranked.Count);
    }

    private sealed record Update(int Earned, int RrAfter, int TierAfter);

    private static async Task<Dictionary<string, Update>> CompetitiveUpdatesAsync(
        RemoteClient remote, string puuid, int count, CancellationToken ct)
    {
        var updates = new Dictionary<string, Update>();
        JsonElement? payload;
        try
        {
            payload = await remote.CompetitiveUpdatesAsync(puuid, count, ct);
        }
        catch (SpikeSightException)
        {
            return updates;   // the match list is still worth showing
        }

        if (payload is not { } root
            || !root.TryGetProperty("Matches", out var entries)
            || entries.ValueKind != JsonValueKind.Array)
        {
            return updates;
        }

        foreach (var entry in entries.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;
            if (!entry.TryGetProperty("MatchID", out var id)
                || id.GetString() is not { Length: > 0 } matchId) continue;

            updates[matchId] = new Update(
                Int(entry, "RankedRatingEarned"),
                Int(entry, "RankedRatingAfterUpdate"),
                Int(entry, "TierAfterUpdate"));
        }
        return updates;
    }

    private static int Int(JsonElement e, string name)
        => e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetInt32() : 0;
}
