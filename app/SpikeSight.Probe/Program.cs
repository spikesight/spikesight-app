using System.Text.Json;
using SpikeSight.Core;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Store;

// Reproducing the scan the polling loop does, with nothing swallowed.

using var local = new LocalClient();
var credentials = await local.GetCredentialsAsync();
var endpoints = await Endpoints.ResolveAsync(local);
using var remote = new RemoteClient(local, endpoints);
using var notes = new NotesStore(Path.Combine(Path.GetTempPath(), $"probe-{Guid.NewGuid():N}.db"));

Console.WriteLine($"== {endpoints.Region}/{endpoints.Shard} {endpoints.ClientVersion} ==");

var presence = await local.GetSelfPresenceAsync(credentials.Puuid);
Console.WriteLine($"presence: {presence?.SessionLoopState} queue={presence?.QueueId}");

var core = await remote.CoregamePlayerAsync(credentials.Puuid);
var pre = core is null ? await remote.PregamePlayerAsync(credentials.Puuid) : null;

static string? Id(JsonElement? e)
    => e is { ValueKind: JsonValueKind.Object } v && v.TryGetProperty("MatchID", out var m)
        ? m.GetString() : null;

var state = Id(core) is not null ? "INGAME" : Id(pre) is not null ? "PREGAME" : "MENUS";
var matchId = Id(core) ?? Id(pre);
Console.WriteLine($"detected: {state} {matchId?[..8]}");
if (matchId is null) return 0;

var match = state == "INGAME"
    ? await remote.CoregameMatchAsync(matchId)
    : await remote.PregameMatchAsync(matchId);
if (match is not { } payload) { Console.WriteLine("no payload"); return 0; }

Console.WriteLine($"payload keys: {string.Join(", ", payload.EnumerateObject().Select(p => p.Name))}");

// Each step separately, so whichever one throws is obvious.
Console.WriteLine("\n-- roster --");
var roster = new List<string>();
var entries = state == "PREGAME"
    ? payload.GetProperty("AllyTeam").GetProperty("Players").EnumerateArray()
    : payload.GetProperty("Players").EnumerateArray();
foreach (var entry in entries)
    if (entry.TryGetProperty("Subject", out var s) && s.GetString() is { Length: > 0 } p)
        roster.Add(p);
Console.WriteLine($"  {roster.Count} players");

Console.WriteLine("-- notes --");
var noteMap = await notes.NotesForAsync(roster);
var encounters = await notes.EncounterSummaryAsync(roster);
Console.WriteLine($"  {noteMap.Count} noted, {encounters.Count} met before");

Console.WriteLine("-- mmr + names --");
var mmr = new Dictionary<string, JsonElement>();
var resolvable = new List<string>();
foreach (var entry in state == "PREGAME"
    ? payload.GetProperty("AllyTeam").GetProperty("Players").EnumerateArray()
    : payload.GetProperty("Players").EnumerateArray())
{
    var puuid = entry.GetProperty("Subject").GetString() ?? "";
    if (puuid.Length == 0) continue;
    if (await remote.MmrAsync(puuid) is { } m) mmr[puuid] = m;
    var identity = entry.TryGetProperty("PlayerIdentity", out var id) ? id : default;
    if (!PrivacyFlags.FromIdentity(identity).Incognito) resolvable.Add(puuid);
}
var names = await remote.NamesAsync(resolvable);
Console.WriteLine($"  {mmr.Count} mmr, {names.Count} named");

Console.WriteLine("-- stats --");
var stats = new StatsEngine(remote);
var queue = payload.TryGetProperty("MatchmakingData", out var mm)
            && mm.TryGetProperty("QueueID", out var q) ? q.GetString() ?? "" : "";
var result = await stats.CollectAsync(roster, queue);
Console.WriteLine($"  {result.MatchesExamined} examined, {result.MatchesFetched} fetched");

Console.WriteLine("-- board --");
var board = BoardBuilder.Build(payload, credentials.Puuid, mmr, names, null,
    state: state, stats: result.Players, notes: noteMap, encounters: encounters);
Console.WriteLine($"  {board.MapName} {board.Label} {board.Players.Count()} players");
Console.WriteLine("\nno exception - the scan completes cleanly");
return 0;
