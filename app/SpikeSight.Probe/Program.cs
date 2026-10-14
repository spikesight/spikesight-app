using SpikeSight.Core;
using SpikeSight.Core.Riot;

// Temporary harness: runs the newest slice of the port against the live
// client, so each one is proven before the next is written. Prints structure
// and never player names. Deleted when the real app can show the same thing.

try
{
    using var local = new LocalClient();

    var lockfile = Lockfile.Read();
    Console.WriteLine("== lockfile ==");
    Console.WriteLine($"  product : {lockfile.Name}");
    Console.WriteLine($"  port    : {lockfile.Port}  (pid {lockfile.Pid}, {lockfile.Protocol})");

    var credentials = await local.GetCredentialsAsync();
    Console.WriteLine("\n== credentials ==");
    Console.WriteLine($"  {credentials.Masked}");
    Console.WriteLine($"  entitlement: {credentials.Entitlement.Length} chars");

    var endpoints = await Endpoints.ResolveAsync(local);
    Console.WriteLine("\n== endpoints ==");
    Console.WriteLine($"  version : {endpoints.ClientVersion}");
    Console.WriteLine($"  region  : {endpoints.Region} / {endpoints.Shard}");
    Console.WriteLine($"  pd      : {endpoints.Pd}");
    Console.WriteLine($"  glz     : {endpoints.Glz}");

    var presences = await local.GetPresencesAsync();
    var self = presences.FirstOrDefault(p => p.Puuid == credentials.Puuid);
    Console.WriteLine("\n== presence ==");
    Console.WriteLine($"  published: {presences.Count}");
    Console.WriteLine(self is null
        ? "  ours     : none - VALORANT is not running"
        : $"  ours     : {self.SessionLoopState} queue={self.QueueId} party={self.PartySize}");
    return 0;
}
catch (SpikeSightException ex)
{
    Console.Error.WriteLine($"\n{ex.Message}");
    if (ex.Hint.Length > 0) Console.Error.WriteLine($"  {ex.Hint}");
    return 1;
}
