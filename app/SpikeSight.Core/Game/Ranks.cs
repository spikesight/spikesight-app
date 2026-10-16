namespace SpikeSight.Core.Game;

/// <summary>One competitive tier, as Riot numbers them on the wire.</summary>
/// <param name="Color">For a dark background.</param>
/// <param name="LightColor">For a light one. Not derived from
/// <paramref name="Color"/>: darkening the dark set by luminance collapses
/// Gold and Bronze onto the same brown and Silver onto a neutral grey, which
/// is a scoreboard where every rank reads the same. The tier is identified by
/// its hue, so the hues are chosen rather than computed.</param>
public sealed record Tier(int Number, string Name, string Short, string Color, string LightColor)
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
    private const string UnrankedLight = "#52525B";
    private const string RadiantColor = "#f7e8a4";
    private const string RadiantLight = "#B45309";

    // Light values hold the tier's hue and drop to roughly 5:1 against a
    // white row. Silver and Iron stay neutral and keep their lightness order,
    // since on a light background the pale one has to be the darker grey.
    private static readonly (string Group, string Short, string Color, string Light)[] Groups =
    [
        ("Iron", "I", "#5a5a5f", "#374151"),
        ("Bronze", "B", "#9b6b3e", "#92400E"),
        ("Silver", "S", "#c3ccd0", "#5B6A7D"),
        ("Gold", "G", "#e0c04a", "#8A6D0B"),
        ("Platinum", "P", "#3fa3b8", "#0E7490"),
        ("Diamond", "D", "#b070d8", "#7E22CE"),
        ("Ascendant", "A", "#25b06b", "#15803D"),
        ("Immortal", "IM", "#c0374f", "#BE123C"),
    ];

    public static IReadOnlyDictionary<int, Tier> Table { get; } = Build();

    private static Dictionary<int, Tier> Build()
    {
        var table = new Dictionary<int, Tier>
        {
            [0] = new(0, "Unranked", "UNR", UnrankedColor, UnrankedLight),
            [1] = new(1, "Unranked", "UNR", UnrankedColor, UnrankedLight),
            [2] = new(2, "Unranked", "UNR", UnrankedColor, UnrankedLight),
            [RadiantTier] = new(RadiantTier, "Radiant", "RAD", RadiantColor, RadiantLight),
        };

        var number = GroupBase;
        foreach (var (group, shortCode, color, light) in Groups)
        {
            for (var division = 1; division <= 3; division++)
            {
                table[number] = new Tier(number, $"{group} {division}",
                                         $"{shortCode}{division}", color, light);
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
