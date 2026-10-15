using SpikeSight.Core;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;

// Temporary harness: runs the newest slice of the port against the live
// client so each one is proven before the next is written.

try
{
    using var local = new LocalClient();
    var credentials = await local.GetCredentialsAsync();
    var endpoints = await Endpoints.ResolveAsync(local);
    using var remote = new RemoteClient(local, endpoints);

    Console.WriteLine($"== {endpoints.Region}/{endpoints.Shard} on {endpoints.ClientVersion} ==");

    var acts = Acts.Empty;
    if (await remote.ContentAsync() is { } content
        && content.TryGetProperty("Seasons", out var seasons))
    {
        acts = Acts.FromSeasons(seasons);
        Console.WriteLine($"current act: {acts.Current?.Short} ({acts.Current?.Full})");
    }

    var stats = new StatsEngine(remote);
    var started = DateTime.Now;
    var career = await CareerBuilder.BuildAsync(
        remote, stats, GameContent.Bundled, credentials.Puuid, acts.Current?.Id, count: 10);

    var s = career.Summary;
    Console.WriteLine($"\n== last {s.Matches} matches ==");
    Console.WriteLine($"  {s.Wins}W {s.Losses}L  winrate {s.Winrate:P0}");
    Console.WriteLine($"  K/D {s.Kd:F2}   ACS {s.Acs}   PS {s.Performance}");
    Console.WriteLine($"  net RR {s.NetRr} over {s.RankedMatches} ranked");

    Console.WriteLine($"\n  {"map",-12} {"queue",-12} {"agent",-10} {"score",-7} "
                      + $"{"k/d/a",-10} {"acs",-5} {"ps",-5} rr");
    foreach (var match in career.Matches)
    {
        var score = match.RoundsWon is not null && match.RoundsLost is not null
            ? $"{match.RoundsWon}-{match.RoundsLost}" : "-";
        var kda = match.Kills is null ? "-" : $"{match.Kills}/{match.Deaths}/{match.Assists}";
        var rr = match.RrEarned is { } earned
            ? $"{(earned > 0 ? "+" : "")}{earned} -> {match.RrAfter}" : "";
        Console.WriteLine($"  {match.Map,-12} {match.Queue,-12} {match.Agent ?? "-",-10} "
                          + $"{score,-7} {kda,-10} {match.Acs?.ToString() ?? "-",-5} "
                          + $"{match.Performance?.ToString() ?? "-",-5} {rr}");
    }

    Console.WriteLine($"\n  built in {(DateTime.Now - started).TotalSeconds:F1}s, "
                      + $"{remote.TotalRequests} requests, "
                      + $"cache {remote.CacheStats.Hits} hit / {remote.CacheStats.Misses} miss, "
                      + $"{stats.CachedMatches} matches on disk");
    return 0;
}
catch (SpikeSightException ex)
{
    Console.Error.WriteLine($"\n{ex.Message}");
    if (ex.Hint.Length > 0) Console.Error.WriteLine($"  {ex.Hint}");
    return 1;
}
