using System.Text.Json;
using SpikeSight.Core;
using SpikeSight.Core.Riot;

// Temporary harness: runs the newest slice of the port against the live
// client, so each one is proven before the next is written. Prints structure
// and never player names. Deleted when the real app can show the same thing.

try
{
    using var local = new LocalClient();
    var credentials = await local.GetCredentialsAsync();
    var endpoints = await Endpoints.ResolveAsync(local);
    using var remote = new RemoteClient(local, endpoints);

    Console.WriteLine($"== {endpoints.Region}/{endpoints.Shard} on {endpoints.ClientVersion} ==");

    // --- find the live session ------------------------------------------
    var core = await remote.CoregamePlayerAsync(credentials.Puuid);
    var pre = core is null ? await remote.PregamePlayerAsync(credentials.Puuid) : null;

    static string? MatchId(JsonElement? e)
        => e is { ValueKind: JsonValueKind.Object } v && v.TryGetProperty("MatchID", out var m)
            ? m.GetString() : null;

    var state = MatchId(core) is not null ? "INGAME" : MatchId(pre) is not null ? "PREGAME" : "MENUS";
    Console.WriteLine($"\n== detect ==\n  state: {state}");

    var payload = state switch
    {
        "INGAME" => await remote.CoregameMatchAsync(MatchId(core)!),
        "PREGAME" => await remote.PregameMatchAsync(MatchId(pre)!),
        _ => null,
    };
    if (payload is not { } match)
    {
        Console.WriteLine("  nothing live to read.");
        return 0;
    }

    // --- roster ----------------------------------------------------------
    var players = match.TryGetProperty("Players", out var list) && list.ValueKind == JsonValueKind.Array
        ? list.EnumerateArray().ToList()
        : match.GetProperty("AllyTeam").GetProperty("Players").EnumerateArray().ToList();

    static string Str(JsonElement e, string name)
        => e.TryGetProperty(name, out var v) ? v.GetString() ?? "" : "";
    static bool Flag(JsonElement identity, string name)
        => identity.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    Console.WriteLine($"  mode : {Str(match, "ModeID").Split('/').LastOrDefault()}");
    Console.WriteLine($"  map  : {Str(match, "MapID")}");
    Console.WriteLine($"  players: {players.Count}");

    var incognito = new List<string>();
    var open = new List<string>();
    foreach (var player in players)
    {
        var subject = Str(player, "Subject");
        var identity = player.GetProperty("PlayerIdentity");
        (Flag(identity, "Incognito") ? incognito : open).Add(subject);
    }
    Console.WriteLine($"  incognito: {incognito.Count}, resolvable: {open.Count}");

    // --- MMR for everyone -------------------------------------------------
    var ranked = 0;
    foreach (var subject in players.Select(p => Str(p, "Subject")))
    {
        var mmr = await remote.MmrAsync(subject);
        if (mmr is { ValueKind: JsonValueKind.Object }) ranked++;
    }
    Console.WriteLine($"\n== mmr ==\n  answered for {ranked}/{players.Count}");

    // --- names, for the ones we are allowed to ask about ------------------
    // Incognito players are never sent to name-service at all.
    var names = await remote.NamesAsync(open);
    Console.WriteLine($"\n== names ==\n  asked for {open.Count}, got {names.Count}");
    Console.WriteLine($"  incognito never requested: {incognito.Count} players");
    var sample = names.Values.FirstOrDefault();
    Console.WriteLine($"  shape: {(sample.GameName.Length > 0 ? "GameName/TagLine present" : "empty")}");
    return 0;
}
catch (SpikeSightException ex)
{
    Console.Error.WriteLine($"\n{ex.Message}");
    if (ex.Hint.Length > 0) Console.Error.WriteLine($"  {ex.Hint}");
    return 1;
}
