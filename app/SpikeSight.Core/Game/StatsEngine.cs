using System.Text.Json;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Store;

namespace SpikeSight.Core.Game;

/// <summary>One recent match, as the form pips show it.</summary>
public sealed record FormLine(
    string MatchId, long StartedMs, string Queue, string MapId, bool? Won,
    int Kills, int Deaths, int Assists, int Rounds, int Score,
    string CharacterId, int Tier, double? Performance);

public sealed record PlayerStats(string Puuid)
{
    /// <summary>Below this a match has no rounds worth dividing by.</summary>
    public const int MinRoundsForAcs = 2;

    public int Matches { get; init; }
    public int Kills { get; init; }
    public int Deaths { get; init; }
    public int Assists { get; init; }
    public int Rounds { get; init; }
    public int Score { get; init; }
    public int Wins { get; init; }
    public int Losses { get; init; }
    public double PerformanceTotal { get; init; }
    public int PerformanceMatches { get; init; }
    public IReadOnlyList<FormLine> Recent { get; init; } = [];

    public double? Kd => Matches == 0 ? null : Math.Round(Kills / (double)Math.Max(1, Deaths), 2);
    public double? Kda => Matches == 0 ? null
        : Math.Round((Kills + Assists) / (double)Math.Max(1, Deaths), 2);
    public int? Acs => Rounds == 0 ? null : (int)Math.Round(Score / (double)Rounds);

    /// <summary>Performance Score is a whole-match figure, so it averages over
    /// matches rather than over rounds the way ACS does.</summary>
    public int? Performance => PerformanceMatches == 0
        ? null : (int)Math.Round(PerformanceTotal / PerformanceMatches);

    public double? RecentWinrate => Wins + Losses == 0
        ? null : Math.Round(Wins / (double)(Wins + Losses), 3);
}

public sealed record StatsResult(
    IReadOnlyDictionary<string, PlayerStats> Players,
    IReadOnlyDictionary<(string, string), int> CoParty,
    IReadOnlyDictionary<string, int> CoParties,
    int MatchesExamined,
    int MatchesFetched);

/// <summary>
/// Recent form, from match history.
///
/// The expensive part of scouting a lobby is match *details*: ten players with
/// five recent games each is up to fifty large payloads. Two things keep that
/// affordable - a finished match is immutable so details are cached forever,
/// and ten players in one lobby frequently share recent games, so the unique
/// set is far smaller than players times depth.
/// </summary>
public sealed class StatsEngine(RemoteClient remote, MatchCache? cache = null)
{
    private readonly MatchCache _cache = cache ?? new MatchCache();

    /// <summary>How many recent matches to read per player.</summary>
    public int Depth { get; init; } = 5;

    /// <summary>The most uncached matches one scan may fetch.</summary>
    public int Budget { get; init; } = 40;

    public int CachedMatches => _cache.Count;

    /// <summary>Raised when <c>score</c> stops looking like combat score.</summary>
    public event Action<string>? Warning;

    public async Task<StatsResult> CollectAsync(
        IEnumerable<string> puuids, string lobbyQueue = "",
        IProgress<(int Done, int Total)>? progress = null,
        CancellationToken ct = default)
    {
        var targets = puuids.Where(p => p.Length > 0).Distinct().ToList();
        if (targets.Count == 0)
            return new StatsResult(new Dictionary<string, PlayerStats>(), new Dictionary<(string, string), int>(),
                                   new Dictionary<string, int>(), 0, 0);

        // Rank candidates by how many of our targets appear in them: a shared
        // match tells us about several players for one request.
        var weight = new Dictionary<string, int>();
        var recency = new Dictionary<string, long>();

        foreach (var puuid in targets)
        {
            foreach (var (matchId, started) in await HistoryAsync(puuid, lobbyQueue, ct))
            {
                weight[matchId] = weight.GetValueOrDefault(matchId) + 1;
                recency[matchId] = Math.Max(recency.GetValueOrDefault(matchId), started);
            }
        }

        var ordered = weight.Keys
            .OrderByDescending(m => weight[m])
            .ThenByDescending(m => recency.GetValueOrDefault(m))
            .ToList();

        // Anything already on disk is free, so take it regardless of budget.
        var free = ordered.Where(m => _cache.Peek(m) is not null).ToList();
        var paid = ordered.Where(m => _cache.Peek(m) is null).Take(Budget).ToList();
        var selected = free.Concat(paid).ToList();

        var summaries = new List<MatchSummary>();
        var done = 0;
        foreach (var matchId in selected)
        {
            if (await DetailsAsync(matchId, ct) is { } summary) summaries.Add(summary);
            done++;
            if (done % 5 == 0 || done == selected.Count)
                progress?.Report((done, selected.Count));
        }

        return Aggregate(targets, summaries, selected.Count, paid.Count);
    }

    private async Task<List<(string MatchId, long Started)>> HistoryAsync(
        string puuid, string queue, CancellationToken ct)
    {
        var entries = await ReadHistoryAsync(puuid, queue, ct);
        // An unrated-only account has no competitive history at all, so fall
        // back to every queue rather than showing them as having never played.
        if (entries.Count == 0 && queue.Length > 0)
            entries = await ReadHistoryAsync(puuid, "", ct);
        return entries;
    }

    private async Task<List<(string, long)>> ReadHistoryAsync(
        string puuid, string queue, CancellationToken ct)
    {
        var result = new List<(string, long)>();
        JsonElement? payload;
        try
        {
            payload = await remote.MatchHistoryAsync(puuid, Depth, queue, ct);
        }
        catch (SpikeSightException)
        {
            return result;   // one unreachable player must not sink the scan
        }

        if (payload is not { ValueKind: JsonValueKind.Object } root
            || !root.TryGetProperty("History", out var history)
            || history.ValueKind != JsonValueKind.Array)
        {
            return result;
        }

        foreach (var entry in history.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;
            if (!entry.TryGetProperty("MatchID", out var id) || id.GetString() is not { Length: > 0 } matchId)
                continue;
            var started = entry.TryGetProperty("GameStartTime", out var t)
                          && t.ValueKind == JsonValueKind.Number ? t.GetInt64() : 0;
            result.Add((matchId, started));
        }
        return result;
    }

    /// <summary>
    /// Your own recent matches from the disk cache, newest first.
    ///
    /// Cache only: the agent pool is a nicety shown during agent select, and
    /// it is not worth a single request to fill.
    /// </summary>
    public async Task<List<MatchSummary>> CachedHistoryAsync(
        string puuid, int count = 40, CancellationToken ct = default)
    {
        var summaries = new List<MatchSummary>();
        foreach (var (matchId, _) in await ReadHistoryAsync(puuid, "", ct))
        {
            if (_cache.Peek(matchId) is { } summary) summaries.Add(summary);
            if (summaries.Count >= count) break;
        }
        return summaries.OrderByDescending(s => s.StartedMs).ToList();
    }

    /// <summary>One match summary, cached or fetched. Used by the history view.</summary>
    public Task<MatchSummary?> SummaryAsync(string matchId, CancellationToken ct = default)
        => DetailsAsync(matchId, ct);

    private async Task<MatchSummary?> DetailsAsync(string matchId, CancellationToken ct)
    {
        if (_cache.Peek(matchId) is { } cached) return cached;

        JsonElement? payload;
        try
        {
            payload = await remote.MatchDetailsAsync(matchId, ct);
        }
        catch (SpikeSightException)
        {
            return null;
        }

        if (payload is not { } details || MatchSummary.From(details) is not { } summary) return null;

        if (summary.ScoringLooksWrong())
            Warning?.Invoke($"Match {matchId[..8]} scores no longer look like combat score; "
                            + "the ACS column may be wrong.");

        // Only a finished match is immutable enough to keep.
        if (summary.Completed) _cache.Put(summary);
        return summary;
    }

    private static StatsResult Aggregate(
        List<string> targets, List<MatchSummary> summaries, int examined, int fetched)
    {
        var totals = targets.ToDictionary(p => p, p => new Accumulator());
        var coParty = new Dictionary<(string, string), int>();
        var coParties = new Dictionary<string, int>();
        var targetSet = targets.ToHashSet();

        foreach (var summary in summaries.OrderByDescending(s => s.StartedMs))
        {
            var byParty = new Dictionary<string, List<string>>();

            foreach (var row in summary.Players)
            {
                if (row.PartyId.Length > 0 && targetSet.Contains(row.Puuid))
                {
                    if (!byParty.TryGetValue(row.PartyId, out var members))
                        byParty[row.PartyId] = members = [];
                    members.Add(row.Puuid);
                }

                if (!totals.TryGetValue(row.Puuid, out var acc)) continue;

                bool? won = summary.WonByTeam.TryGetValue(row.Team, out var w) ? w : null;
                acc.Add(row, summary, won);
            }

            // The group as it actually was, plus every pair inside it. The
            // whole group is what makes a real stack detectable as one thing
            // rather than a chain of pairs that happen to share a member.
            foreach (var members in byParty.Values)
            {
                var group = members.Distinct().OrderBy(p => p, StringComparer.Ordinal).ToList();
                if (group.Count < 2) continue;

                var key = string.Join('|', group);
                coParties[key] = coParties.GetValueOrDefault(key) + 1;

                for (var i = 0; i < group.Count; i++)
                    for (var j = i + 1; j < group.Count; j++)
                        coParty[(group[i], group[j])] = coParty.GetValueOrDefault((group[i], group[j])) + 1;
            }
        }

        return new StatsResult(
            totals.ToDictionary(p => p.Key, p => p.Value.ToStats(p.Key)),
            coParty, coParties, examined, fetched);
    }

    private sealed class Accumulator
    {
        private readonly List<FormLine> _lines = [];
        private int _matches, _kills, _deaths, _assists, _rounds, _score, _wins, _losses;
        private double _performanceTotal;
        private int _performanceMatches;

        public void Add(MatchRow row, MatchSummary summary, bool? won)
        {
            _matches++;
            _kills += row.Kills;
            _deaths += row.Deaths;
            _assists += row.Assists;
            // Deathmatch reports a single "round", so score-per-round there is
            // the whole match score - 4000-odd, which would wreck an average
            // taken across modes. ACS only means anything in a round-based
            // mode, so only those contribute to it.
            if (row.Rounds >= PlayerStats.MinRoundsForAcs)
            {
                _rounds += row.Rounds;
                _score += row.Score;
            }
            if (won is true) _wins++;
            else if (won is false) _losses++;

            if (row.Performance is { } performance)
            {
                _performanceTotal += performance;
                _performanceMatches++;
            }

            _lines.Add(new FormLine(
                summary.MatchId, summary.StartedMs, summary.Queue, summary.MapId, won,
                row.Kills, row.Deaths, row.Assists, row.Rounds, row.Score,
                row.CharacterId, row.CompetitiveTier, row.Performance));
        }

        public PlayerStats ToStats(string puuid) => new(puuid)
        {
            Matches = _matches, Kills = _kills, Deaths = _deaths, Assists = _assists,
            Rounds = _rounds, Score = _score, Wins = _wins, Losses = _losses,
            PerformanceTotal = _performanceTotal, PerformanceMatches = _performanceMatches,
            Recent = _lines.Take(10).ToList(),
        };
    }
}
