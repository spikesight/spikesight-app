namespace SpikeSight.Core.Game;

/// <summary>One competitive tier, as Riot numbers them on the wire.</summary>
public sealed record Tier(int Number, string Name, string Short, string Color)
{
    public bool IsRanked => Number >= Ranks.GroupBase;
    public bool IsImmortalPlus => Number >= 24;
}

/// <summary>
/// The tier table and the rating maths on top of it.
///
/// 0 is Unranked, 1 and 2 are unused legacy slots, 3-26 are Iron 1 through
/// Immortal 3, and 27 is Radiant. Names and colours are stable across acts,
/// so they live here rather than being fetched: the app stays usable offline
/// and that is one request fewer.
/// </summary>
public static class Ranks
{
    public const int GroupBase = 3;
    public const int RadiantTier = 27;
    public const int MaxTier = RadiantTier;

    private const string UnrankedColor = "#4a4a52";
    private const string RadiantColor = "#f7e8a4";

    private static readonly (string Group, string Short, string Color)[] Groups =
    [
        ("Iron", "I", "#5a5a5f"),
        ("Bronze", "B", "#9b6b3e"),
        ("Silver", "S", "#c3ccd0"),
        ("Gold", "G", "#e0c04a"),
        ("Platinum", "P", "#3fa3b8"),
        ("Diamond", "D", "#b070d8"),
        ("Ascendant", "A", "#25b06b"),
        ("Immortal", "IM", "#c0374f"),
    ];

    public static IReadOnlyDictionary<int, Tier> Table { get; } = Build();

    private static Dictionary<int, Tier> Build()
    {
        var table = new Dictionary<int, Tier>
        {
            [0] = new(0, "Unranked", "UNR", UnrankedColor),
            [1] = new(1, "Unranked", "UNR", UnrankedColor),
            [2] = new(2, "Unranked", "UNR", UnrankedColor),
            [RadiantTier] = new(RadiantTier, "Radiant", "RAD", RadiantColor),
        };

        var number = GroupBase;
        foreach (var (group, shortCode, color) in Groups)
        {
            for (var division = 1; division <= 3; division++)
            {
                table[number] = new Tier(number, $"{group} {division}", $"{shortCode}{division}", color);
                number++;
            }
        }
        return table;
    }

    public static Tier Of(int? number)
    {
        if (number is not { } value) return Table[0];
        value = Math.Clamp(value, 0, MaxTier);
        return Table.TryGetValue(value, out var tier) ? tier : Table[0];
    }

    /// <summary>
    /// One comparable number for a rank, in "RR since Iron 1" units.
    ///
    /// Iron 1 at 0 RR is 0 and every division above adds 100. Immortal+ RR is
    /// cumulative in game, so it is clamped: otherwise one high-RR Immortal
    /// drags a whole team average with them.
    /// </summary>
    public static double? RatingPoints(int? tierNumber, int? rr)
    {
        var tier = Of(tierNumber);
        if (!tier.IsRanked) return null;

        var baseline = (tier.Number - GroupBase) * 100.0;
        var points = (double)(rr ?? 0);
        points = tier.IsImmortalPlus ? Math.Min(points, 300.0) : Math.Clamp(points, 0.0, 100.0);
        return baseline + points;
    }

    /// <summary>The inverse, for describing a team average.</summary>
    public static string PointsToLabel(double? points)
    {
        if (points is not { } value) return "Unranked";
        var index = GroupBase + (int)(value / 100);
        var remainder = (int)(value % 100);
        var tier = Of(Math.Min(index, MaxTier));
        return tier.Number >= RadiantTier ? "Radiant" : $"{tier.Name} ({remainder} RR)";
    }
}
