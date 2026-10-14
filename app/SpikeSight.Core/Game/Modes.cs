namespace SpikeSight.Core.Game;

/// <summary>One short phrase for the header: what kind of game is this.</summary>
public static class Modes
{
    /// <summary>Queue ids are stable and human-meaningful, so they win.</summary>
    private static readonly Dictionary<string, string> QueueLabels = new()
    {
        ["competitive"] = "Competitive",
        ["unrated"] = "Unrated",
        ["swiftplay"] = "Swiftplay",
        ["spikerush"] = "Spike Rush",
        ["deathmatch"] = "Deathmatch",
        ["ggteam"] = "Escalation",
        ["onefa"] = "Replication",
        ["hurm"] = "Team Deathmatch",
        ["snowball"] = "Snowball Fight",
        ["newmap"] = "New Map",
        ["premier"] = "Premier",
        ["premiermode"] = "Premier",
    };

    /// <summary>
    /// Mode asset names are internal and messy - Swiftplay's is
    /// "Swiftplay_EoRCredits_GameMode", after a dev folder. Matching on a
    /// substring survives that kind of suffix.
    /// </summary>
    private static readonly (string Token, string Label)[] ModeMatches =
    [
        ("swiftplay", "Swiftplay"),
        ("quickbomb", "Spike Rush"),
        ("deathmatch", "Deathmatch"),
        ("gungameteams", "Escalation"),
        ("gungame", "Escalation"),
        ("oneforall", "Replication"),
        ("snowball", "Snowball Fight"),
        ("hurm", "Team Deathmatch"),
        ("newmap", "New Map"),
        ("gauntlet", "Gauntlet"),
        ("bomb", "Standard"),
    ];

    private static readonly string[] NoiseTokens =
        ["gamemode", "eorcredits", "endofroundcredits", "development"];

    /// <summary>
    /// Turn a ModeID asset path into something a person would say.
    /// <c>/Game/GameModes/Bomb/BombGameMode.BombGameMode_C</c> becomes
    /// "Standard".
    /// </summary>
    public static string Name(string mode)
    {
        if (mode.Length == 0) return "";

        var tail = mode.TrimEnd('/').Split('/').LastOrDefault()?.Split('.').FirstOrDefault() ?? "";
        var key = tail.ToLowerInvariant();
        if (key.EndsWith("_c", StringComparison.Ordinal)) key = key[..^2];
        key = key.Replace("_", "");

        foreach (var (token, label) in ModeMatches)
            if (key.Contains(token, StringComparison.Ordinal)) return label;

        foreach (var noise in NoiseTokens) key = key.Replace(noise, "");
        return key.Length > 0
            ? char.ToUpperInvariant(key[0]) + key[1..]
            : tail;
    }

    public static string Label(string queue, string mode, string provisioning)
    {
        if (provisioning.Equals("customgame", StringComparison.OrdinalIgnoreCase))
        {
            var modeLabel = Name(mode);
            return modeLabel.Length > 0 ? $"Custom Game ({modeLabel})" : "Custom Game";
        }

        var key = queue.ToLowerInvariant().Replace("_", "");
        if (QueueLabels.TryGetValue(key, out var label)) return label;

        var fallback = Name(mode);
        return fallback.Length > 0 ? fallback : queue.Length > 0 ? queue : "Unknown";
    }
}
