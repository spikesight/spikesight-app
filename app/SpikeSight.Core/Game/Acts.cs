using System.Text.Json;
using System.Text.RegularExpressions;

namespace SpikeSight.Core.Game;

/// <summary>One competitive act, from the client's content service.</summary>
public sealed partial record Act(string Id, string Name, string EpisodeName, bool IsActive, DateTimeOffset Start)
{
    [GeneratedRegex(@"EPISODE\s*(\d+)", RegexOptions.IgnoreCase)]
    private static partial Regex EpisodePattern();

    [GeneratedRegex(@"ACT\s*([IVX]+|\d+)", RegexOptions.IgnoreCase)]
    private static partial Regex ActPattern();

    [GeneratedRegex(@"\bV(\d+)\b", RegexOptions.IgnoreCase)]
    private static partial Regex VersionedPattern();

    private static readonly Dictionary<string, string> Roman = new(StringComparer.OrdinalIgnoreCase)
    {
        ["I"] = "1", ["II"] = "2", ["III"] = "3", ["IV"] = "4",
        ["V"] = "5", ["VI"] = "6", ["VII"] = "7", ["VIII"] = "8",
    };

    /// <summary>A compact label such as <c>V26A5</c> or <c>E9A2</c>.</summary>
    public string Short
    {
        get
        {
            var act = ActPattern().Match(Name);
            if (!act.Success) return $"{EpisodeName} {Name}".Trim();

            var number = Roman.TryGetValue(act.Groups[1].Value, out var arabic)
                ? arabic : act.Groups[1].Value;

            var source = EpisodeName.Length > 0 ? EpisodeName : Name;
            var episode = EpisodePattern().Match(source);
            if (episode.Success) return $"E{episode.Groups[1].Value}A{number}";

            // Riot renamed episodes to versions: "V26" rather than "EPISODE 9".
            var versioned = VersionedPattern().Match(source);
            if (versioned.Success) return $"V{versioned.Groups[1].Value}A{number}";

            return $"{EpisodeName} {Name}".Trim();
        }
    }

    public string Full => EpisodeName.Length > 0
        && !Name.Contains(EpisodeName, StringComparison.OrdinalIgnoreCase)
            ? $"{EpisodeName} // {Name}" : Name;
}

/// <summary>
/// The act timeline, which is what turns "peak rank" into "peak rank, and
/// when". Only the client's content service still carries it; agent and map
/// names moved out of there years ago and now ship with the app.
/// </summary>
public sealed class Acts
{
    private readonly List<Act> _acts;

    private Acts(List<Act> acts) => _acts = acts;

    public static Acts Empty { get; } = new([]);
    public IReadOnlyList<Act> All => _acts;

    public Act? Current
    {
        get
        {
            var active = _acts.FirstOrDefault(a => a.IsActive);
            if (active is not null) return active;

            // No active flag (a stale cache): the newest act that has started.
            var now = DateTimeOffset.UtcNow;
            return _acts.Where(a => a.Start <= now).MaxBy(a => a.Start);
        }
    }

    public Act? Find(string? actId)
    {
        var key = MmrView.NormalizeId(actId);
        return key.Length == 0 ? null : _acts.FirstOrDefault(a => a.Id == key);
    }

    /// <summary>
    /// Parse a content-service <c>Seasons</c> array.
    ///
    /// Entries arrive newest first, each episode immediately followed by its
    /// own acts, so the episode name is carried forward as the list is walked.
    /// </summary>
    public static Acts FromSeasons(JsonElement seasons)
    {
        var acts = new List<Act>();
        if (seasons.ValueKind != JsonValueKind.Array) return new Acts(acts);

        var episode = "";
        foreach (var entry in seasons.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;

            var kind = (entry.TryGetProperty("Type", out var t) ? t.GetString() ?? "" : "").ToLowerInvariant();
            var name = entry.TryGetProperty("Name", out var n) ? n.GetString() ?? "" : "";

            if (kind == "episode") { episode = name; continue; }
            if (kind != "act") continue;

            var id = entry.TryGetProperty("ID", out var i) ? i.GetString() ?? "" : "";
            if (id.Length == 0) continue;

            acts.Add(new Act(
                MmrView.NormalizeId(id), name, episode,
                entry.TryGetProperty("IsActive", out var a) && a.ValueKind == JsonValueKind.True,
                ParseTime(entry)));
        }
        return new Acts(acts);
    }

    private static DateTimeOffset ParseTime(JsonElement entry)
        => entry.TryGetProperty("StartTime", out var v)
           && v.ValueKind == JsonValueKind.String
           && DateTimeOffset.TryParse(v.GetString(), out var parsed)
            ? parsed : DateTimeOffset.UnixEpoch;
}
