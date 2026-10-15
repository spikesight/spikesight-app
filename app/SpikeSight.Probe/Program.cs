using SpikeSight.Core;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;

// Temporary harness: runs a slice of the port against the live client, so
// each one is proven before the next is written. Prints structure, never
// player names. Deleted once the app itself can show the same thing.

try
{
    using var local = new LocalClient();
    var credentials = await local.GetCredentialsAsync();
    var endpoints = await Endpoints.ResolveAsync(local);
    using var remote = new RemoteClient(local, endpoints);

    Console.WriteLine($"== {endpoints.Region}/{endpoints.Shard} on {endpoints.ClientVersion} ==");

    var presence = await local.GetSelfPresenceAsync(credentials.Puuid);
    Console.WriteLine($"presence: {presence?.SessionLoopState ?? "none"} "
                      + $"queue={presence?.QueueId ?? "-"}");

    if (await remote.ContentAsync() is { } content
        && content.TryGetProperty("Seasons", out var seasons))
    {
        var acts = Acts.FromSeasons(seasons);
        Console.WriteLine($"act     : {acts.Current?.Short} ({acts.Current?.Full})");
    }

    Console.WriteLine($"requests: {remote.TotalRequests}");
    return 0;
}
catch (SpikeSightException ex)
{
    Console.Error.WriteLine($"\n{ex.Message}");
    if (ex.Hint.Length > 0) Console.Error.WriteLine($"  {ex.Hint}");
    return 1;
}
