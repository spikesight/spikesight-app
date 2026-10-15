using System.Text.Json;
using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

/// <summary>
/// Deathmatch reports a single "round", so score-per-round there is the whole
/// match score - four thousand and something. Caught by running the history
/// view against a real account, where it showed an ACS of 4301 and dragged
/// the ten-match average to 979.
/// </summary>
public class RoundlessModeTests
{
    private static MatchSummary Match(string id, string queue, int rounds, int score, int kills)
    {
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(new
        {
            matchInfo = new { matchId = id, queueID = queue, isCompleted = true },
            teams = new[] { new { teamId = "Blue", won = true, roundsWon = 13 } },
            players = new[]
            {
                new
                {
                    subject = "me", teamId = "Blue", partyId = "", characterId = "c",
                    competitiveTier = 18,
                    stats = new { kills, deaths = 10, assists = 2, roundsPlayed = rounds, score },
                },
            },
        }));
        return MatchSummary.From(document.RootElement.Clone())!;
    }

    private static PlayerStats Accumulate(params MatchSummary[] matches)
    {
        var stats = new PlayerStats("me");
        var rounds = 0;
        var score = 0;
        var kills = 0;
        foreach (var match in matches)
        {
            var row = match.Players[0];
            kills += row.Kills;
            if (row.Rounds >= PlayerStats.MinRoundsForAcs)
            {
                rounds += row.Rounds;
                score += row.Score;
            }
        }
        return stats with { Matches = matches.Length, Kills = kills, Rounds = rounds, Score = score };
    }

    [Fact]
    public void A_deathmatch_contributes_no_acs()
    {
        // One round, 4301 score. Left in, it reports an ACS of 4301.
        var stats = Accumulate(Match("dm", "deathmatch", rounds: 1, score: 4301, kills: 13));

        Assert.Null(stats.Acs);
    }

    [Fact]
    public void A_deathmatch_does_not_drag_a_mixed_average()
    {
        var mixed = Accumulate(
            Match("c1", "competitive", rounds: 20, score: 4400, kills: 19),
            Match("dm", "deathmatch", rounds: 1, score: 4301, kills: 13));

        // 4400 over 20 rounds, and the deathmatch left out entirely.
        Assert.Equal(220, mixed.Acs);
    }

    [Fact]
    public void Ordinary_matches_are_untouched()
    {
        var stats = Accumulate(
            Match("c1", "competitive", rounds: 20, score: 4400, kills: 19),
            Match("c2", "competitive", rounds: 24, score: 4800, kills: 20));

        Assert.Equal((4400 + 4800) / (20 + 24), stats.Acs);
    }

    [Fact]
    public void Kills_still_count_from_every_mode()
    {
        // Only the per-round arithmetic is meaningless; a kill is a kill.
        var stats = Accumulate(
            Match("c1", "competitive", rounds: 20, score: 4400, kills: 19),
            Match("dm", "deathmatch", rounds: 1, score: 4301, kills: 13));

        Assert.Equal(32, stats.Kills);
        Assert.Equal(2, stats.Matches);
    }
}
