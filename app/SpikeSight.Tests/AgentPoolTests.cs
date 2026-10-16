using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

public class AgentPoolTests
{
    private static MatchSummary Match(
        string id, string map, string agent, bool won, int rounds = 20, int score = 4400)
        => new()
        {
            MatchId = id, MapId = map, Completed = true,
            WonByTeam = new Dictionary<string, bool> { ["Blue"] = won },
            Players =
            [
                new MatchRow
                {
                    Puuid = "me", Team = "Blue", CharacterId = agent,
                    Kills = won ? 20 : 10, Deaths = 10, Rounds = rounds, Score = score,
                },
            ],
        };

    [Fact]
    public void Best_agents_are_per_map()
    {
        // A Viper record on Icebox says nothing about Ascent.
        var history = new[]
        {
            Match("1", "/Game/Maps/Icebox/Icebox", "viper", won: true),
            Match("2", "/Game/Maps/Icebox/Icebox", "viper", won: true),
            Match("3", "/Game/Maps/Ascent/Ascent", "jett", won: true),
        };

        var icebox = AgentPool.Best(history, "me", "/Game/Maps/Icebox/Icebox");

        Assert.Equal("viper", Assert.Single(icebox).CharacterId);
        Assert.Equal(2, icebox[0].Games);
    }

    [Fact]
    public void A_better_record_wins()
    {
        var history = new[]
        {
            Match("1", "m", "jett", won: false),
            Match("2", "m", "jett", won: false),
            Match("3", "m", "sage", won: true),
            Match("4", "m", "sage", won: true),
        };

        var best = AgentPool.Best(history, "me", "m");

        Assert.Equal("sage", best[0].CharacterId);
        Assert.Equal(1.0, best[0].Winrate);
    }

    [Fact]
    public void Evidence_breaks_a_tie()
    {
        // One lucky game should not outrank a solid three.
        var history = new[]
        {
            Match("1", "m", "sage", won: true),
            Match("2", "m", "omen", won: true),
            Match("3", "m", "omen", won: true),
            Match("4", "m", "omen", won: true),
        };

        var best = AgentPool.Best(history, "me", "m");

        Assert.Equal("omen", best[0].CharacterId);
        Assert.Equal(3, best[0].Games);
    }

    [Fact]
    public void Deathmatch_does_not_poison_the_acs()
    {
        var history = new[]
        {
            Match("1", "m", "jett", won: true, rounds: 20, score: 4400),
            Match("2", "m", "jett", won: true, rounds: 1, score: 4301),
        };

        var best = AgentPool.Best(history, "me", "m");

        Assert.Equal(220, best[0].Acs);
        Assert.Equal(2, best[0].Games);   // the game still counts
    }

    [Fact]
    public void Somebody_elses_matches_are_not_yours()
    {
        var history = new[] { Match("1", "m", "jett", won: true) };

        Assert.Empty(AgentPool.Best(history, "somebody-else", "m"));
    }

    [Fact]
    public void No_history_on_this_map_is_simply_empty()
        => Assert.Empty(AgentPool.Best([Match("1", "icebox", "viper", true)], "me", "ascent"));

    [Fact]
    public void Record_counts_only_your_own_rows()
    {
        // The cache holds opponents' matches too, so a summary you were not
        // in must not count as a loss against you.
        var theirs = new MatchSummary
        {
            MatchId = "x", MapId = "m", Completed = true,
            WonByTeam = new Dictionary<string, bool> { ["Blue"] = true },
            Players = [new MatchRow { Puuid = "someone", Team = "Blue", CharacterId = "jett" }],
        };

        var (wins, losses) = AgentPool.Record(
            [Match("1", "m", "jett", won: true), Match("2", "m", "jett", won: false), theirs],
            "me");

        Assert.Equal(1, wins);
        Assert.Equal(1, losses);
    }
}
