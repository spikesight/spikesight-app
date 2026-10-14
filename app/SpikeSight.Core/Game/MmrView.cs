using System.Text.Json;

namespace SpikeSight.Core.Game;

/// <summary>A rank as the board shows it.</summary>
public sealed record RankView(int? Tier = null, int? Rr = null, int? Leaderboard = null)
{
    public Tier Resolved => Ranks.Of(Tier);
    public double? Points => Ranks.RatingPoints(Tier, Rr);
}

/// <summary>
/// Reading a player's MMR payload. Shared by the live board and the career
/// view, which need exactly the same reading of Riot's
/// <c>SeasonalInfoBySeasonID</c> blob.
/// </summary>
public static class MmrView
{
    public static string NormalizeId(string? value)
        => (value ?? "").Replace("-", "").ToLowerInvariant();

    /// <summary>Seasonal info for the competitive queue, keyed by act id.</summary>
    public static Dictionary<string, JsonElement> Seasonal(JsonElement? mmr)
    {
        var seasons = new Dictionary<string, JsonElement>();
        if (mmr is not { ValueKind: JsonValueKind.Object } root) return seasons;

        if (!root.TryGetProperty("QueueSkills", out var queues)
            || queues.ValueKind != JsonValueKind.Object
            || !queues.TryGetProperty("competitive", out var competitive)
            || competitive.ValueKind != JsonValueKind.Object
            || !competitive.TryGetProperty("SeasonalInfoBySeasonID", out var bySeason)
            || bySeason.ValueKind != JsonValueKind.Object)
        {
            return seasons;
        }

        foreach (var season in bySeason.EnumerateObject())
            if (season.Value.ValueKind == JsonValueKind.Object)
                seasons[NormalizeId(season.Name)] = season.Value;

        return seasons;
    }

    private static int Int(JsonElement element, string name)
        => element.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetInt32() : 0;

    /// <summary>Live rank: this act's entry, falling back to the last update.</summary>
    public static RankView CurrentRank(JsonElement? mmr, Dictionary<string, JsonElement> seasons, string? currentActId)
    {
        if (currentActId is { Length: > 0 } act
            && seasons.TryGetValue(act, out var info)
            && (Int(info, "NumberOfGames") > 0 || Int(info, "CompetitiveTier") > 0))
        {
            var leaderboard = Int(info, "LeaderboardRank");
            return new RankView(Int(info, "CompetitiveTier"), Int(info, "RankedRating"),
                                leaderboard == 0 ? null : leaderboard);
        }

        if (mmr is { ValueKind: JsonValueKind.Object } root
            && root.TryGetProperty("LatestCompetitiveUpdate", out var latest)
            && latest.ValueKind == JsonValueKind.Object
            && Int(latest, "TierAfterUpdate") > 0)
        {
            return new RankView(Int(latest, "TierAfterUpdate"), Int(latest, "RankedRatingAfterUpdate"));
        }

        return new RankView();
    }

    /// <summary>Highest tier ever reached, and the act it happened in.</summary>
    public static (int Tier, string ActId)? Peak(Dictionary<string, JsonElement> seasons)
    {
        var bestTier = 0;
        var bestAct = "";

        foreach (var (actId, info) in seasons)
        {
            var seasonBest = Int(info, "CompetitiveTier");

            // A player can peak mid-act and fall back before it ends, so the
            // wins-by-tier map is the real high-water mark, not the final tier.
            if (info.TryGetProperty("WinsByTier", out var wins)
                && wins.ValueKind == JsonValueKind.Object)
            {
                foreach (var entry in wins.EnumerateObject())
                {
                    if (entry.Value.ValueKind != JsonValueKind.Number || entry.Value.GetInt32() <= 0)
                        continue;
                    if (int.TryParse(entry.Name, out var tier) && tier > seasonBest)
                        seasonBest = tier;
                }
            }

            if (seasonBest > bestTier)
            {
                bestTier = seasonBest;
                bestAct = actId;
            }
        }

        return bestTier < Ranks.GroupBase ? null : (bestTier, bestAct);
    }

    public static (int Games, int Wins, int Losses, double? Winrate) ActRecord(
        Dictionary<string, JsonElement> seasons, string? actId)
    {
        if (actId is not { Length: > 0 } key || !seasons.TryGetValue(key, out var info))
            return (0, 0, 0, null);

        var games = Int(info, "NumberOfGames");
        var wins = Int(info, "NumberOfWins");
        return (games, wins, Math.Max(0, games - wins),
                games > 0 ? Math.Round((double)wins / games, 3) : null);
    }
}
