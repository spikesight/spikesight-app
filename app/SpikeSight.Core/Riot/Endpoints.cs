using System.Text;
using System.Text.RegularExpressions;

namespace SpikeSight.Core.Riot;

/// <summary>
/// The region, shard and build string Riot's edge servers demand in headers.
///
/// All of it is read from files the client already wrote, or from the local
/// API. Nothing is guessed at and nothing is scraped from the game process.
/// </summary>
public sealed partial record Endpoints(string Region, string Shard, string ClientVersion)
{
    public string Pd => $"https://pd.{Shard}.a.pvp.net";
    public string Glz => $"https://glz-{Region}-1.{Shard}.a.pvp.net";
    public string Shared => $"https://shared.{Shard}.a.pvp.net";

    /// <summary>
    /// Sent verbatim by the client. A constant describing a PC, not a
    /// fingerprint of this machine - built here rather than pasted so it
    /// stays readable.
    /// </summary>
    public const string PlatformJson =
        "{\r\n\t\"platformType\": \"PC\",\r\n\t\"platformOS\": \"Windows\",\r\n\t" +
        "\"platformOSVersion\": \"10.0.19042.1.256.64bit\",\r\n\t" +
        "\"platformChipset\": \"Unknown\"\r\n}";

    public static string ClientPlatform { get; } =
        Convert.ToBase64String(Encoding.UTF8.GetBytes(PlatformJson));

    /// <summary>Regions without a shard of their own.</summary>
    private static readonly Dictionary<string, string> RegionToShard = new()
    {
        ["na"] = "na", ["latam"] = "na", ["br"] = "na",
        ["eu"] = "eu", ["ap"] = "ap", ["kr"] = "kr", ["pbe"] = "pbe",
    };

    [GeneratedRegex(@"release-[\d.]+-shipping-\d+-\d+")]
    private static partial Regex VersionPattern();

    [GeneratedRegex(@"glz-([a-z0-9]+)-\d+\.([a-z0-9]+)\.a\.pvp\.net")]
    private static partial Regex HostPattern();

    public static string LogDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "VALORANT", "Saved", "Logs");

    private const int HeadBytes = 1_000_000;
    private const int TailBytes = 4_000_000;

    /// <summary>
    /// Head plus tail of a log that can be tens of megabytes and is held open.
    ///
    /// The build string is written in the first hundred lines of a session
    /// while the shard hostnames appear throughout, so both ends are worth
    /// reading and the middle never is.
    /// </summary>
    private static string ReadEnds(string path)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            var size = stream.Length;
            var head = new byte[(int)Math.Min(size, HeadBytes)];
            _ = stream.Read(head, 0, head.Length);
            if (size <= HeadBytes) return Encoding.UTF8.GetString(head);

            stream.Seek(Math.Max(HeadBytes, size - TailBytes), SeekOrigin.Begin);
            var tail = new byte[(int)(size - stream.Position)];
            _ = stream.Read(tail, 0, tail.Length);
            return Encoding.UTF8.GetString(head) + "\n" + Encoding.UTF8.GetString(tail);
        }
        catch (IOException)
        {
            return "";
        }
        catch (UnauthorizedAccessException)
        {
            return "";
        }
    }

    /// <summary>The live log first, then the most recent rotated backups.</summary>
    private static IEnumerable<string> CandidateLogs()
    {
        var live = Path.Combine(LogDirectory, "ShooterGame.log");
        if (File.Exists(live)) yield return live;

        string[] backups;
        try
        {
            backups = Directory.GetFiles(LogDirectory, "ShooterGame-backup-*.log");
        }
        catch (IOException) { yield break; }
        catch (UnauthorizedAccessException) { yield break; }

        foreach (var path in backups.OrderByDescending(File.GetLastWriteTimeUtc).Take(3))
            yield return path;
    }

    /// <summary>
    /// The build string in some log text, latest wins.
    ///
    /// Last, not first: a log spans a patch when the game updates mid-session,
    /// and sending yesterday's build gets every request refused.
    /// </summary>
    public static string? VersionIn(string text)
    {
        var matches = VersionPattern().Matches(text);
        return matches.Count > 0 ? matches[^1].Value : null;
    }

    /// <summary>Region and shard from a glz hostname in some log text.</summary>
    public static (string Region, string Shard)? RegionShardIn(string text)
    {
        var matches = HostPattern().Matches(text);
        if (matches.Count == 0) return null;
        var last = matches[^1];
        return (last.Groups[1].Value, last.Groups[2].Value);
    }

    /// <summary>Most recent shipping-version string written to a log.</summary>
    public static string? VersionFromLog()
        => CandidateLogs().Select(p => VersionIn(ReadEnds(p))).FirstOrDefault(v => v is not null);

    /// <summary>Region and shard from the glz hostname the game actually used.</summary>
    public static (string Region, string Shard)? RegionShardFromLog()
    {
        foreach (var path in CandidateLogs())
            if (RegionShardIn(ReadEnds(path)) is { } found) return found;
        return null;
    }

    /// <summary>Work out region, shard and version, preferring explicit settings.</summary>
    public static async Task<Endpoints> ResolveAsync(
        LocalClient local, string region = "", string shard = "", string version = "",
        CancellationToken ct = default)
    {
        region = region.ToLowerInvariant();
        shard = shard.ToLowerInvariant();

        if (region.Length == 0 || shard.Length == 0)
        {
            if (RegionShardFromLog() is { } fromLog)
            {
                if (region.Length == 0) region = fromLog.Region;
                if (shard.Length == 0) shard = fromLog.Shard;
            }
        }

        if (region.Length == 0)
        {
            // Fall back to what the client reports; the shard follows from it.
            try
            {
                var locale = await local.GetJsonAsync("/riotclient/region-locale", ct);
                if (locale.TryGetProperty("region", out var value))
                    region = (value.GetString() ?? "").ToLowerInvariant();
            }
            catch (Exception ex) when (ex is SpikeSightException or HttpRequestException)
            {
                // Best effort; the error below is the useful one.
            }
        }

        if (region.Length == 0)
            throw new SpikeSightException(
                "Could not determine your region. Launch VALORANT once, or set it in settings.");

        if (shard.Length == 0)
            shard = RegionToShard.TryGetValue(region, out var mapped) ? mapped : region;

        if (version.Length == 0) version = VersionFromLog() ?? "";
        if (version.Length == 0)
            throw new SpikeSightException(
                "Could not read the VALORANT client version from ShooterGame.log. Launch VALORANT once.");

        return new Endpoints(region, shard, version);
    }
}
