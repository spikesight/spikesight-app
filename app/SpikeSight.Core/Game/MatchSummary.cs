using System.Text.Json;
using System.Text.Json.Serialization;

namespace SpikeSight.Core.Game;

/// <summary>One player's line in a finished match. Deliberately compact: this
/// goes to disk for every match ever scanned, and never carries a name.</summary>
public sealed record MatchRow
{
    [JsonPropertyName("s")] public string Puuid { get; init; } = "";
    [JsonPropertyName("t")] public string Team { get; init; } = "";
    [JsonPropertyName("p")] public string PartyId { get; init; } = "";
    [JsonPropertyName("c")] public string CharacterId { get; init; } = "";
    [JsonPropertyName("ct")] public int CompetitiveTier { get; init; }
    [JsonPropertyName("k")] public int Kills { get; init; }
    [JsonPropertyName("d")] public int Deaths { get; init; }
    [JsonPropertyName("a")] public int Assists { get; init; }
    [JsonPropertyName("r")] public int Rounds { get; init; }
    [JsonPropertyName("sc")] public int Score { get; init; }

    /// <summary>Performance Score, when the payload carried one.</summary>
    [JsonPropertyName("ps")] public double? Performance { get; init; }
}

public sealed record MatchSummary
{
    [JsonPropertyName("matchId")] public string MatchId { get; init; } = "";
    [JsonPropertyName("queue")] public string Queue { get; init; } = "";
    [JsonPropertyName("mapId")] public string MapId { get; init; } = "";
    [JsonPropertyName("start")] public long StartedMs { get; init; }
    [JsonPropertyName("completed")] public bool Completed { get; init; }
    [JsonPropertyName("won")] public Dictionary<string, bool> WonByTeam { get; init; } = [];
    [JsonPropertyName("scores")] public Dictionary<string, int> RoundsByTeam { get; init; } = [];
    [JsonPropertyName("players")] public List<MatchRow> Players { get; init; } = [];

    /// <summary>Bump when the stored shape changes; older files are refetched.</summary>
    [JsonPropertyName("v")] public int Version { get; init; } = CurrentVersion;

    public const int CurrentVersion = 1;

    /// <summary>
    /// Where Performance Score is read from, best name first.
    ///
    /// <c>TempValueF</c> is Riot's placeholder and works today - identified by
    /// matching the match and both team MVPs, and confirmed against the score
    /// the end-of-game screen showed. The real name goes first so the day it
    /// ships nothing here needs changing.
    /// </summary>
    private static readonly string[] PerformanceFields = ["performanceScore", "TempValueF"];

    /// <summary>Riot describes it as 0-500. A placeholder can be repurposed
    /// without warning, so anything outside that is not treated as a score.</summary>
    private const double PerformanceMax = 500.0;

    public static double? PerformanceScore(JsonElement scores)
    {
        if (scores.ValueKind != JsonValueKind.Object) return null;
        foreach (var name in PerformanceFields)
        {
            if (scores.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.Number
                && value.TryGetDouble(out var number)
                && number >= 0 && number <= PerformanceMax)
            {
                return number;
            }
        }
        return null;
    }

    /// <summary>Reduce a full match-details payload to the few fields we keep.</summary>
    public static MatchSummary? From(JsonElement payload)
    {
        if (payload.ValueKind != JsonValueKind.Object
            || !payload.TryGetProperty("matchInfo", out var info)
            || info.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var matchId = Str(info, "matchId");
        if (matchId.Length == 0) return null;

        var won = new Dictionary<string, bool>();
        var rounds = new Dictionary<string, int>();
        if (payload.TryGetProperty("teams", out var teams) && teams.ValueKind == JsonValueKind.Array)
        {
            foreach (var team in teams.EnumerateArray())
            {
                var id = Str(team, "teamId");
                if (id.Length == 0) continue;
                won[id] = team.TryGetProperty("won", out var w) && w.ValueKind == JsonValueKind.True;
                rounds[id] = Int(team, "roundsWon");
            }
        }

        var rows = new List<MatchRow>();
        if (payload.TryGetProperty("players", out var players) && players.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in players.EnumerateArray())
            {
                if (entry.ValueKind != JsonValueKind.Object) continue;
                if (entry.TryGetProperty("isObserver", out var obs) && obs.ValueKind == JsonValueKind.True)
                    continue;

                var stats = entry.TryGetProperty("stats", out var st) ? st : default;
                rows.Add(new MatchRow
                {
                    Puuid = Str(entry, "subject"),
                    Team = Str(entry, "teamId"),
                    PartyId = Str(entry, "partyId"),
                    CharacterId = Str(entry, "characterId"),
                    CompetitiveTier = Int(entry, "competitiveTier"),
                    Kills = Int(stats, "kills"),
                    Deaths = Int(stats, "deaths"),
                    Assists = Int(stats, "assists"),
                    Rounds = Int(stats, "roundsPlayed"),
                    Score = Int(stats, "score"),
                    Performance = entry.TryGetProperty("scores", out var sc)
                        ? PerformanceScore(sc) : null,
                });
            }
        }

        return new MatchSummary
        {
            MatchId = matchId,
            Queue = Str(info, "queueID") is { Length: > 0 } q ? q : Str(info, "queueId"),
            MapId = Str(info, "mapId"),
            StartedMs = Long(info, "gameStartMillis"),
            Completed = !info.TryGetProperty("isCompleted", out var done)
                        || done.ValueKind != JsonValueKind.False,
            WonByTeam = won,
            RoundsByTeam = rounds,
            Players = rows,
        };
    }

    /// <summary>
    /// Does <c>score</c> still mean what ACS assumes it means?
    ///
    /// ACS is combat score over rounds played, which only holds while score is
    /// the running total it has always been. If Riot ever puts a per-match
    /// number there instead, every ACS silently becomes a fraction of what it
    /// should be - and a wrong number on the board is worse than a missing one.
    /// </summary>
    public bool ScoringLooksWrong()
    {
        if (!Completed) return false;
        var played = Players.Where(p => p.Rounds >= 10).ToList();
        if (played.Count < 6) return false;   // a short or half-empty match proves nothing
        return played.Max(p => p.Score / (double)p.Rounds) < 25;
    }

    private static string Str(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v)
           && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static int Int(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v)
           && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;

    private static long Long(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v)
           && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : 0;
}
